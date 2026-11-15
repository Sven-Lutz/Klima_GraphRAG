"""Fail-closed pilot boundary around the existing read-only research application."""

import hashlib
import hmac
import json
import re
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

from starlette.responses import JSONResponse

from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service


class PilotBoundary:
    def __init__(self, app, *, gateway_token, users, snapshot_id, expires_at=None):
        if (
            not isinstance(users, (list, tuple))
            or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", gateway_token)
            or not users
            or len(users) > 50
        ):
            raise ValueError("Explicit strong gateway secret and 1..50 pseudonymous users required")
        if len(set(users)) != len(users) or not all(
            isinstance(u, str) and re.fullmatch(r"p[0-9]{3}", u) for u in users
        ):
            raise ValueError("Pseudonymous pilot identities must use p001..p999")
        self.app = app
        self.token = gateway_token
        self.users = frozenset(users)
        self.snapshot_id = snapshot_id
        self.expires_at = expires_at
        self.limits = defaultdict(lambda: deque(maxlen=120))
        self.costly = defaultdict(lambda: deque(maxlen=6))
        self.owners = {}  # bounded to recent 128 result IDs, never persistent
        self.metrics = Counter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        transport_send = send

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                for name, value in [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ]:
                    if name not in existing:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await transport_send(message)

        send = protected_send
        if self.expires_at is not None and time.time() >= self.expires_at:
            return await JSONResponse(
                {"error": {"code": "pilot_release_expired"}}, status_code=503
            )(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        user = headers.get("x-pilot-user", "")
        if (
            not hmac.compare_digest(
                headers.get("x-pilot-gateway", "").encode(), self.token.encode()
            )
            or user not in self.users
        ):
            return await JSONResponse({"error": {"code": "pilot_auth_required"}}, status_code=401)(
                scope, receive, send
            )
        path = scope["path"]
        if not (
            path
            in {"/", "/methodik", "/api/status", "/api/query", "/api/graph", "/api/pilot-health"}
            or path.startswith(("/static/", "/api/graph/evidence/", "/api/source/", "/api/export/"))
        ):
            return await JSONResponse({"error": {"code": "not_found"}}, status_code=404)(
                scope, receive, send
            )
        now = time.monotonic()
        costly = path == "/api/query" or path.startswith(("/api/graph/evidence/", "/api/source/"))
        buckets = [self.limits[user]] + ([self.costly[user]] if costly else [])
        if any(len(b) == b.maxlen and now - b[0] < 60 for b in buckets):
            return await JSONResponse(
                {"error": {"code": "rate_limited"}}, status_code=429, headers={"Retry-After": "60"}
            )(scope, receive, send)
        for bucket in buckets:
            bucket.append(now)
        if path.startswith(("/api/source/", "/api/export/")):
            result_id = path.split("/")[3].split(".")[0]
            if self.owners.get(result_id) != user:
                return await JSONResponse({"error": {"code": "result_not_found"}}, status_code=404)(
                    scope, receive, send
                )
        if path == "/api/pilot-health":
            return await JSONResponse(
                {"status": "ready", "snapshot_id": self.snapshot_id, "counters": dict(self.metrics)}
            )(scope, receive, send)
        chunks = []
        capture = path == "/api/query" or path.startswith("/api/graph/evidence/")

        async def observe(message):
            if message["type"] == "http.response.start":
                self.metrics[str(message["status"])] += 1
            if capture and message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    try:
                        result = json.loads(b"".join(chunks))
                        if result.get("result_id"):
                            if len(self.owners) >= 128:
                                self.owners.pop(next(iter(self.owners)))
                            self.owners[result["result_id"]] = user
                    except (ValueError, TypeError):
                        pass
            await send(message)

        await self.app(scope, receive, observe)


def verify_bundle(root, *, allow_technical_fixture=False):
    root = Path(root).resolve(strict=True)
    bundle = json.loads((root / "BUNDLE.json").read_text())
    if bundle.get("schema") != "klima-pilot-bundle-v1" or (
        bundle.get("technical_test_only") and not allow_technical_fixture
    ):
        raise ValueError("A production-approved pilot bundle is required")
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Symlinks are not allowed in the pilot bundle")
    required = {"ledger.sqlite", "workspace.json", "rights.json"}
    if not required <= bundle["files"].keys() or any(
        name not in required | {"relations.sqlite"}
        and not re.fullmatch(r"originals/[a-f0-9]{64}\.(gz|bin)", name)
        for name in bundle["files"]
    ):
        raise ValueError("Unexpected bundle artifact class")
    expected = set(bundle["files"]) | {"BUNDLE.json"}
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Unexpected or missing bundle files; no private research mounts allowed")
    for name, sha in bundle["files"].items():
        path = (root / name).resolve(strict=True)
        if root not in path.parents or path.is_symlink():
            raise ValueError("Bundle path escape")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                digest.update(block)
        if digest.hexdigest() != sha:
            raise ValueError("Bundle integrity mismatch")
    rights = json.loads((root / "rights.json").read_text())
    from crawler.web.pilot_bundle import valid_rights

    selected = valid_rights(rights, technical=allow_technical_fixture)
    presentation = rights.get("source_presentation", "archived")
    if bundle.get("source_presentation", "archived") != presentation:
        raise ValueError("Bundle presentation differs from rights decision")
    if presentation in {"linked_originals", "minimal_external_preview"}:
        if any(name.startswith("originals/") for name in bundle["files"]):
            raise ValueError("Linked-original bundle must not contain archives")
        if bundle.get("original_verification") != "SHA256_AT_EXPORT":
            raise ValueError("Linked-original bundle requires explicit export verification")
        from research.bavaria.workspace.core import read_only

        with read_only(root / "ledger.sqlite") as conn:
            documents = {r[0]: r[1] for r in conn.execute("SELECT sha256,raw_path FROM documents")}
            if set(documents) != set(selected):
                raise ValueError("Linked-original source selection mismatch")
            for sha, path in documents.items():
                proof = conn.execute(
                    "SELECT value FROM metadata WHERE key=?", ("pilot_verified_original:" + sha,)
                ).fetchone()
                if path != "NOT_DISTRIBUTED_LINKED_ORIGINAL:" + sha or not proof or proof[0] != sha:
                    raise ValueError("Linked-original verification receipt missing")
    # Check manifest targets before constructing Workspace, which opens its ledgers.
    # File hashes alone do not constrain references contained inside those files.
    workspace = json.loads((root / "workspace.json").read_text())
    expected_artifacts = {"ledger": "ledger.sqlite"}
    if "relations.sqlite" in bundle["files"]:
        expected_artifacts["relations"] = "relations.sqlite"
    if (
        set(workspace) != {"schema", "artifacts", "snapshot_id"}
        or workspace.get("schema") != "bavaria-workspace-manifest-v1"
        or set(workspace.get("artifacts", {})) != set(expected_artifacts)
    ):
        raise ValueError("Pilot workspace must contain only the bundled ledger and sidecar")
    for kind, name in expected_artifacts.items():
        artifact = workspace["artifacts"][kind]
        if (
            artifact.get("path") != str(root / name)
            or artifact.get("sha256") != bundle["files"][name]
        ):
            raise ValueError("Pilot workspace artifact outside isolated bundle or hash mismatch")
    from research.bavaria.workspace.core import identity

    body = {k: v for k, v in workspace.items() if k != "snapshot_id"}
    if (
        workspace["snapshot_id"] != "bavaria-workspace-sha256:" + identity(body)
        or workspace["snapshot_id"] != bundle.get("snapshot_id")
        or rights.get("parent_snapshot_id") != bundle.get("parent_snapshot_id")
    ):
        raise ValueError("Pilot bundle snapshot or rights identity mismatch")
    return bundle, rights


def load_pilot_service(root, expected_bundle_hash):
    """Verify the trusted, bounded bundle before composing any serving boundary."""
    root = Path(root).resolve(strict=True)
    if hashlib.sha256((root / "BUNDLE.json").read_bytes()).hexdigest() != expected_bundle_hash:
        raise ValueError("Bundle differs from trusted owner release reference")
    bundle, rights = verify_bundle(root)
    presentation = bundle.get("source_presentation", "archived")
    service = create_bavaria_service(
        root / "workspace.json",
        original_roots=[root / "originals"],
        source_presentation="linked_originals"
        if presentation == "minimal_external_preview"
        else presentation,
    )
    if service.database_snapshot_id != bundle["snapshot_id"]:
        raise ValueError("Snapshot identity mismatch")
    approved = {
        r["sha256"]
        for r in rights["sources"]
        if presentation == "minimal_external_preview" or r.get("excerpt_display") == "APPROVED"
    }
    from research.bavaria.workspace.core import read_only

    with read_only(service.workspace.db) as conn:
        actual = {r[0] for r in conn.execute("SELECT sha256 FROM documents")}
        paths = (
            [
                Path(r[0]).resolve(strict=True)
                for r in conn.execute("SELECT raw_path FROM documents")
            ]
            if presentation == "archived"
            else []
        )
    if actual != approved or any(root / "originals" not in p.parents for p in paths):
        raise ValueError("Snapshot source rights or original boundaries differ")
    graph_approved = {
        r["sha256"]
        for r in rights["sources"]
        if r.get("derived_graph_display") in {"APPROVED", "SCOPED_OWN_FACTS"}
    }
    if set(service.workspace.source_rows) - graph_approved:
        raise ValueError("Graph source rights differ from the approved subset")
    for source in service.workspace.source_rows.values():
        if presentation in {"linked_originals", "minimal_external_preview"}:
            if source["raw_path"] != "NOT_DISTRIBUTED_LINKED_ORIGINAL:" + source["source_sha256"]:
                raise ValueError("Linked graph source has an unexpected original path")
        elif root / "originals" not in Path(source["raw_path"]).resolve(strict=True).parents:
            raise ValueError("Graph original outside isolated bundle")
    from crawler.web.service import ImmutableResultStore

    service.store = ImmutableResultStore(capacity=64, ttl_seconds=1800)
    service.pilot_mode = True
    with read_only(service.workspace.db) as conn:
        frames = [
            r[0] for r in conn.execute("SELECT DISTINCT ags FROM associations ORDER BY ags LIMIT 3")
        ]
    service.preview_examples = [
        {
            "question": "Welche Quellen nennen Klima, Wärme oder Energie?",
            "municipality": service.workspace.municipalities[ags]["name"],
        }
        for ags in frames
    ]
    if presentation == "minimal_external_preview":
        from crawler.web.minimal_service import MinimalResearchService

        service = MinimalResearchService(service, rights)
    return service, rights


def create_pilot_app():
    """ASGI factory for the isolated Caddy deployment."""
    import os

    service, rights = load_pilot_service(
        os.environ["PILOT_SNAPSHOT_ROOT"], os.environ["PILOT_BUNDLE_SHA256"]
    )
    token = Path(os.environ["PILOT_GATEWAY_TOKEN_FILE"]).read_text().strip()
    users = json.loads(Path(os.environ["PILOT_USERS_FILE"]).read_text())
    # No provider, no writable corpus, no Gold mount, single existing result store.
    app = create_app(service, allowed_origin=os.environ["PILOT_ORIGIN"])
    return PilotBoundary(
        app,
        gateway_token=token,
        users=users,
        snapshot_id=service.database_snapshot_id,
        expires_at=datetime.fromisoformat(rights["expires_at"]).timestamp(),
    )
