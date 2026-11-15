"""Loopback origin boundary for an explicitly protected Cloudflare Quick Tunnel.

The tunnel authenticates emails; its local session token does NOT identify an email.
A private Host override authenticates that proxy, never an incoming identity header.
Opaque origin sessions isolate exports, with stricter whole-group rate budgets.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from crawler.web.app import create_app
from crawler.web.pilot import PilotBoundary, load_pilot_service

COOKIE = "__Host-klima-session"


class QuickOriginGateway:
    def __init__(self, app, *, origin_host, public_origin, gateway_token, health_token, expires_at):
        parsed = urlsplit(public_origin)
        if (
            not re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", parsed.netloc)
            or parsed.scheme != "https"
        ):
            raise ValueError("Explicit HTTPS Quick Tunnel origin required")
        if not re.fullmatch(r"[a-f0-9]{64}\.origin\.invalid", origin_host):
            raise ValueError("Private random origin Host required")
        self.app = app
        self.origin_host = origin_host.encode()
        self.public_host = parsed.netloc.encode()
        self.token = gateway_token.encode()
        self.health_token = health_token.encode()
        self.expires_at = expires_at
        self.sessions = {}
        self.requests = deque(maxlen=120)
        self.costly = deque(maxlen=6)
        self.forwarded = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def deny(code, status=401):
            await JSONResponse(
                {"error": {"code": code}},
                status_code=status,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )(scope, receive, send)

        # Reject duplicates too; never accept public cf-access or X-Pilot headers as proof.
        hosts = [v for k, v in scope["headers"] if k.lower() == b"host"]
        if len(hosts) != 1 or not hmac.compare_digest(hosts[0], self.origin_host):
            return await deny("protected_proxy_required")
        if time.time() >= self.expires_at:
            return await deny("pilot_release_expired", 503)
        if scope["path"] == "/__private_health":
            keys = [v for k, v in scope["headers"] if k.lower() == b"x-local-health"]
            if len(keys) != 1 or not hmac.compare_digest(keys[0], self.health_token):
                return await deny("not_found", 404)
            return await JSONResponse({"status": "ready", "forwarded": self.forwarded})(
                scope, receive, send
            )
        now = time.monotonic()
        costly = scope["path"] == "/api/query" or scope["path"].startswith(
            ("/api/graph/evidence/", "/api/source/")
        )
        buckets = [self.requests] + ([self.costly] if costly else [])
        if any(len(b) == b.maxlen and now - b[0] < 60 for b in buckets):
            return await deny("rate_limited", 429)
        for b in buckets:
            b.append(now)
        self.sessions = {k: v for k, v in self.sessions.items() if now - v[1] < 1800}
        cookies = SimpleCookie()
        try:
            cookies.load(
                b"; ".join(v for k, v in scope["headers"] if k.lower() == b"cookie").decode(
                    "latin-1"
                )
            )
        except CookieError:  # malformed input is unauthenticated, never an internal diagnostic
            return await deny("invalid_session")
        candidate = cookies.get(COOKIE)
        session = candidate.value if candidate else None
        if session not in self.sessions:
            if scope["method"] != "GET":
                return await deny("session_required")
            occupied = {v[0] for v in self.sessions.values()}
            user = next((f"p{i:03}" for i in range(1, 4) if f"p{i:03}" not in occupied), None)
            if user is None:
                return await deny("three_browser_sessions_active", 503)
            session = secrets.token_urlsafe(32)
        else:
            user = self.sessions[session][0]
        self.sessions[session] = (user, now)
        headers = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower()
            not in {
                b"host",
                b"cookie",
                b"authorization",
                b"x-pilot-user",
                b"x-pilot-gateway",
                b"cf-access-authenticated-user-email",
                b"cf-access-jwt-assertion",
                b"x-local-health",
                b"x-forwarded-for",
                b"x-forwarded-host",
                b"x-forwarded-proto",
            }
        ]
        headers += [
            (b"host", self.public_host),
            (b"x-pilot-gateway", self.token),
            (b"x-pilot-user", user.encode()),
        ]
        rewritten = {**scope, "headers": headers, "scheme": "https", "client": ("127.0.0.1", 0)}

        async def respond(message):
            if message["type"] == "http.response.start":
                cookie = f"{COOKIE}={session}; Path=/; Max-Age=1800; Secure; HttpOnly; SameSite=Lax"
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())],
                }
            await send(message)

        self.forwarded += 1
        await self.app(rewritten, receive, respond)


def create_quick_app():
    path = Path(os.environ["KLIMA_PRIVATE_RUNTIME"])
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Private runtime must be an owner-only regular file")
    config = json.loads(path.read_text())
    review = config.get("provider_privacy_review", {})
    if (
        review.get("status") != "DOCUMENTED_FOR_THIS_SERVICE"
        or review.get("service") != "cloudflare_protected_quick_tunnel"
        or not review.get("applicable_agreement")
        or not review.get("processing_roles_and_article28")
        or not review.get("transfer_basis")
        or not review.get("reviewed_by")
    ):
        raise ValueError("Applicable provider privacy agreement evidence required before serving")
    evidence = Path(review["evidence_file"])
    if (
        evidence.is_symlink()
        or not evidence.is_file()
        or evidence.stat().st_mode & 0o077
        or hashlib.sha256(evidence.read_bytes()).hexdigest() != review.get("evidence_sha256")
    ):
        raise ValueError("Private provider evidence differs from reviewed reference")
    service, rights = load_pilot_service(config["bundle"], config["bundle_sha256"])
    if rights["source_presentation"] != "minimal_external_preview":
        raise ValueError("Private Quick Tunnel requires the minimal factual contract")
    from datetime import datetime

    expiry = datetime.fromisoformat(rights["expires_at"]).timestamp()
    boundary = PilotBoundary(
        create_app(service, allowed_origin=config["public_origin"]),
        gateway_token=config["gateway_token"],
        users=["p001", "p002", "p003"],
        snapshot_id=service.database_snapshot_id,
        expires_at=expiry,
    )
    return QuickOriginGateway(
        boundary,
        origin_host=config["origin_host"],
        public_origin=config["public_origin"],
        gateway_token=config["gateway_token"],
        health_token=config["health_token"],
        expires_at=expiry,
    )
