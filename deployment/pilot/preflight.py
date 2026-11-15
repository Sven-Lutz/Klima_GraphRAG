"""Offline configuration inspection. Never starts containers or reads private secrets."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def image_check(images):
    """Image references are public release inputs, never account secrets."""
    if not any(images):
        return "NOT_TESTED"
    return (
        "PASS"
        if all(
            value and re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", value)
            for value in images
        )
        else "FAILED"
    )


def inspect(root=ROOT):
    folder = root / "deployment/pilot"
    compose = yaml.safe_load((folder / "compose.yml").read_text())
    app, gateway = (compose["services"][key] for key in ("app", "gateway"))
    caddy = (folder / "Caddyfile").read_text()
    dockerfile = (folder / "Dockerfile").read_text()
    checks = []

    def check(name, valid):
        checks.append({"id": name, "status": "PASS" if valid else "FAILED"})

    check("app_not_published", not app.get("ports") and app["networks"] == ["research"])
    check("isolated_network", compose["networks"]["research"].get("internal") is True)
    check("readonly_runtime", all(s.get("read_only") is True for s in (app, gateway)))
    check(
        "readonly_snapshot_only",
        len(app["volumes"]) == 1
        and app["volumes"][0].get("target") == "/snapshot"
        and app["volumes"][0].get("read_only") is True,
    )
    check(
        "secrets_as_files",
        set(app["secrets"]) == {"gateway", "users"}
        and "PILOT_GATEWAY_TOKEN" not in app["environment"]
        and all("file" in v for v in compose["secrets"].values()),
    )
    check(
        "resource_limits",
        all(
            all(k in s for k in ("mem_limit", "cpus", "pids_limit", "tmpfs"))
            for s in (app, gateway)
        ),
    )
    check(
        "no_privilege_escalation",
        all(
            s.get("cap_drop") == ["ALL"] and "no-new-privileges:true" in s.get("security_opt", [])
            for s in (app, gateway)
        ),
    )
    check(
        "individual_identity",
        "basic_auth" in caddy
        and "import /etc/caddy/accounts" in caddy
        and "header_up X-Pilot-User {http.auth.user.id}" in caddy
        and "header_up X-Pilot-Gateway {$PILOT_GATEWAY_TOKEN}" in caddy,
    )
    check(
        "strip_client_credentials",
        all(
            f"header_up -{h}" in caddy
            for h in ("Authorization", "Cookie", "X-Forwarded-For", "X-Real-IP")
        ),
    )
    check(
        "tls_and_admin_boundary",
        "https://${PILOT_DOMAIN:" in app["environment"]["PILOT_ORIGIN"]
        and "admin off" in caddy
        and "Strict-Transport-Security" in caddy,
    )
    check(
        "bounded_logging",
        all(s["logging"]["options"] == {"max-size": "2m", "max-file": "2"} for s in (app, gateway))
        and "--no-access-log" in app["command"]
        and not any(line.strip().startswith("log") for line in caddy.splitlines()),
    )
    check(
        "offline_hash_locked_build", "--no-index" in dockerfile and "--require-hashes" in dockerfile
    )
    check("trusted_bundle_reference", "PILOT_BUNDLE_SHA256" in app["environment"])
    rights = json.loads((root / "docs/pilot/evidence/RIGHTS_REGISTER.json").read_text())
    required = (
        "source_metadata_display",
        "original_url_display",
        "excerpt_display",
        "archive_distribution",
    )
    approved = sum(all(row.get(k) == "APPROVED" for k in required) for row in rights["sources"])
    return {
        "schema": "pilot-offline-preflight-v1",
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAILED",
        "scope": (
            "Static invariants only; not Compose validation, legal approval or go-live acceptance"
        ),
        "checks": checks,
        "approved_real_sources_in_register": approved,
        "real_subset": "BLOCKED" if not approved else "OWNER_DECISION_VERIFICATION_REQUIRED",
        "host_tls_auth_isolation_restore_tests": "NOT_TESTED",
        "inputs": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [folder / "compose.yml", folder / "Caddyfile", folder / "Dockerfile"]
        },
    }


def capabilities():
    docker = shutil.which("docker")
    compose = False
    if docker:
        try:
            compose = (
                subprocess.run(
                    [docker, "compose", "version"], capture_output=True, timeout=5, check=False
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "docker_client": bool(docker),
        "compose_plugin": compose,
        "caddy_binary": bool(shutil.which("caddy")),
        "daemon": "NOT_TESTED",
        "note": "No socket connection, downloads, secret reads or services started",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--app-image")
    parser.add_argument("--gateway-image")
    parser.add_argument("--python-image")
    args = parser.parse_args()
    result = inspect()
    result["image_digest_syntax"] = image_check(
        (args.app_image, args.gateway_image, args.python_image)
    )
    if result["image_digest_syntax"] == "FAILED":
        result["status"] = "FAILED"
    if args.capabilities:
        result["local_capabilities"] = capabilities()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
