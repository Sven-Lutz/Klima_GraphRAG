"""Loopback TLS/browser preflight. Never proves external OTP or authorizes deployment."""

import argparse
import json
from pathlib import Path


def serve(directory, port):
    import secrets
    import subprocess
    import time

    import uvicorn

    from crawler.web.app import create_app
    from crawler.web.fixture import create_fixture_service
    from crawler.web.pilot import PilotBoundary
    from crawler.web.quick_gateway import QuickOriginGateway

    directory.mkdir(parents=True, exist_ok=False)
    key, cert = directory / "private-test.key", directory / "test-cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    proof = secrets.token_urlsafe(32)
    (directory / "test-ingress-proof.txt").write_text(proof)
    (directory / "test-ingress-proof.txt").chmod(0o600)
    token = secrets.token_urlsafe(36)
    origin = "https://synthetic-operational.trycloudflare.com"
    host = secrets.token_hex(32) + ".origin.invalid"
    service = create_fixture_service(directory / "fixture")
    app = create_app(service, allowed_origin=origin)
    pilot = PilotBoundary(
        app,
        gateway_token=token,
        users=["p001", "p002", "p003"],
        snapshot_id=service.database_snapshot_id,
    )
    gateway = QuickOriginGateway(
        pilot,
        origin_host=host,
        public_origin=origin,
        gateway_token=token,
        health_token=secrets.token_urlsafe(32),
        expires_at=time.time() + 600,
    )

    async def synthetic_ingress(scope, receive, send):
        if scope["type"] == "http":
            headers = scope["headers"]
            if (b"x-synthetic-test-proxy", proof.encode()) in headers:
                headers = [
                    (k, v)
                    for k, v in headers
                    if k.lower() not in {b"host", b"x-synthetic-test-proxy"}
                ]
                # This bridge substitutes only the synthetic local test ingress.
                # A real deployment must authenticate upstream; this is not OTP.
                local_origin = f"https://127.0.0.1:{port}".encode()
                headers = [
                    (k, origin.encode() if k == b"origin" and v == local_origin else v)
                    for k, v in headers
                ]
                scope = {**scope, "headers": headers + [(b"host", host.encode())]}
        await gateway(scope, receive, send)

    uvicorn.run(
        synthetic_ingress,
        host="127.0.0.1",
        port=port,
        access_log=False,
        ssl_keyfile=str(key),
        ssl_certfile=str(cert),
    )


def browse(directory, port):
    from playwright.sync_api import sync_playwright

    base = f"https://127.0.0.1:{port}"
    proof = (directory / "test-ingress-proof.txt").read_text()
    errors, checks = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        anonymous = browser.new_context(ignore_https_errors=True)
        assert anonymous.request.get(base + "/api/status").status == 401
        assert (
            anonymous.request.get(
                base + "/api/status",
                headers={
                    "X-Pilot-User": "p001",
                    "Cf-Access-Authenticated-User-Email": "forged@example.test",
                },
            ).status
            == 401
        )
        checks.append("Anonymous and forged identity rejected by real loopback HTTPS origin")
        headers = {"X-Synthetic-Test-Proxy": proof}
        first = browser.new_context(ignore_https_errors=True, extra_http_headers=headers)
        page = first.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.goto(base, wait_until="networkidle")
        assert response.status == 200
        tls = response.security_details()
        cookies = first.cookies()
        cookie = next(c for c in cookies if c["name"] == "__Host-klima-session")
        assert cookie["secure"] and cookie["httpOnly"] and cookie["sameSite"] in {"Lax", "Strict"}
        checks.append("Actual TLS response, UI and Secure/HttpOnly/SameSite origin session cookie")
        page.locator("#question").fill("Welche Solarprojekte planen die Stadtwerke?")
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy", timeout=30000)
        assert page.evaluate("Boolean(state.result)"), page.locator("#query-status").inner_text()
        result = page.evaluate("state.result")
        assert result["evidence"]
        result_id = result["result_id"]
        assert first.request.get(base + f"/api/export/{result_id}.json").status == 200
        second = browser.new_context(ignore_https_errors=True, extra_http_headers=headers)
        assert second.request.get(base + "/api/status").status == 200
        assert second.request.get(base + f"/api/export/{result_id}.json").status == 404
        checks.append("Actual fixture search/export; separate browser cannot export foreign result")
        for path in [
            "/review",
            "/api/review-protocol/accounts",
            "/openapi.json",
            "/api/research/history",
            "/generated/private-live-pilot/access.json",
        ]:
            assert first.request.get(base + path).status == 404
        checks.append("Private review/admin/history/files remain outside pilot allowlist")
        third = browser.new_context(ignore_https_errors=True, extra_http_headers=headers)
        assert third.request.post(base + "/api/query", data="{}").status == 401
        checks.append("Fresh cookie-free POST denied; no identity from forged headers")
        assert not errors, errors
        page.screenshot(path=str(directory / "synthetic-ui.png"), full_page=True)
        browser.close()
    report = dict(
        schema="synthetic-https-browser-preflight-v1",
        external_acceptance=False,
        otp="NOT_TESTED_EXTERNAL_PROVIDER_REQUIRED",
        tls_certificate="SELF_SIGNED_TEST_ONLY",
        trust_validation="BYPASSED_FOR_LOCAL_TEST",
        upstream_auth="SIMULATED_TEST_INGRESS",
        private_real_sources_used=False,
        checks=checks,
        page_errors=errors,
        tls=tls,
    )
    (directory / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(checks=len(checks), external_acceptance=False, page_errors=errors)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "browse"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8865)
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.directory, args.port)
    else:
        browse(args.directory, args.port)
