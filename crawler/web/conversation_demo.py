"""Explicit synthetic review setup. Creates no judgments and no Human Gold."""

import json
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def attach_demo_review(app, directory, protocol, sources, chat_store):
    directory = directory.resolve()
    credentials = directory / "synthetic-demo-accounts.json"
    if not credentials.exists():
        accounts = {}
        for name, role in [
            ("demo-a", "reviewer"),
            ("demo-b", "reviewer"),
            ("demo-adjudicator", "adjudicator"),
        ]:
            password = secrets.token_urlsafe(24)
            protocol.create_account(
                "owner", name, password, identity="SYNTHETIC_" + name, role=role
            )
            accounts[name] = password
        credentials.write_text(json.dumps(accounts))
        credentials.chmod(0o600)
    else:
        accounts = json.loads(credentials.read_text())
    source = next(iter(sources.values()))
    for kind in ["source", "claim", "relation", "answer_evidence"]:
        key = "v2-synthetic-demo-" + kind
        existing = next((oid for oid, obj in protocol.objects().items() if obj["key"] == key), None)
        if existing:
            continue
        oid = protocol.register(
            "owner",
            kind=kind,
            key=key,
            payload={
                "label": "Synthetischer " + kind + "-Prüffall",
                "proposed_text": source["quote"],
                "annotation_unit": kind,
                "source_kind": "SYNTHETIC_REVIEW_DOCUMENT",
            },
            sources=[{"sha256": source["sha256"], "quote": source["quote"]}],
        )
        protocol.assign("owner", oid, ["demo-a", "demo-b"], "demo-adjudicator")

    @app.post("/api/chat-review/demo-session/{name}")
    def demo_login(name: str, request: Request):
        if not chat_store.session(request.cookies.get("klima_chat")) or name not in accounts:
            raise HTTPException(403, "Synthetic session unavailable")
        token = protocol.login(name, accounts[name])
        response = JSONResponse(
            {"technical": True, "human_gold": False, "role": protocol.account(name)["role"]}
        )
        response.set_cookie(
            "klima_review",
            token,
            httponly=True,
            samesite="strict",
            max_age=43200,
            secure=request.url.scheme == "https",
        )
        return response
