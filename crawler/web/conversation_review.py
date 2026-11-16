"""Focused transport for the existing blinded, append-only ReviewProtocol.

No chat feedback ingestion. Original-opening receipt is a necessary interaction
check, never proof that a person read or understood a source.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from crawler.web.review_protocol import ReviewProtocol

CORRECTNESS = ["correct", "incorrect", "missing", "unknown"]
UNIT_SCHEMAS = {
    "source": {
        "relevance": ["relevant", "irrelevant", "unknown"],
        "authority": ["official", "institutional", "unclear", "not_authoritative"],
        "municipality_scope": ["local", "shared", "external", "unknown"],
        "version_match": CORRECTNESS,
    },
    "extraction": {key: CORRECTNESS for key in ["value", "unit", "actor_role", "scope", "time"]},
    "relation": {
        key: CORRECTNESS for key in ["endpoints", "relation_type", "actor_roles", "scope", "time"]
    },
    "answer_evidence": {
        "relevance": ["relevant", "irrelevant", "unknown"],
        "completeness": ["complete", "partial", "none", "unknown"],
        "scope": CORRECTNESS,
        "time": CORRECTNESS,
        "abstention": ["appropriate", "false_abstention", "not_applicable", "unknown"],
    },
}


def attach_conversation_review(app, directory, snapshot, *, technical, sources):
    def resolve(source):
        anchor = sources.get(source.get("sha256"))
        if not anchor or source.get("quote", anchor["quote"]) not in anchor["quote"]:
            raise ValueError("Source is not in the explicitly registered review material")
        return dict(anchor)

    protocol = ReviewProtocol(directory, snapshot, technical=technical, source_resolver=resolve)
    app.state.conversation_review = protocol

    def actor(request):
        try:
            return protocol.session(request.cookies.get("klima_review"))["username"]
        except PermissionError as exc:
            raise HTTPException(401, "Individual reviewer login required") from exc

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, "Review role or assignment unavailable") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, "Review contract incomplete or unavailable") from exc

    @app.post("/api/chat-review/session")
    async def login(request: Request):
        data = await request.json()
        token = call(protocol.login, data.get("username"), data.get("password"))
        response = JSONResponse({"role": protocol.session(token)["role"], "technical": technical})
        response.set_cookie(
            "klima_review",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=43200,
        )
        return response

    @app.delete("/api/chat-review/session")
    def logout(request: Request):
        protocol.logout(request.cookies.get("klima_review"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("klima_review")
        return response

    @app.get("/api/chat-review/queue")
    def queue(
        request: Request,
        kind: str = "",
        status: str = "",
        q: str = "",
        limit: int = 50,
        offset: int = 0,
    ):
        who = actor(request)
        value = call(
            protocol.queue,
            who,
            kind=kind,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )
        return dict(value, actor=who)

    @app.get("/api/chat-review/objects/{oid}")
    def view(oid: str, request: Request):
        value = call(protocol.view, actor(request), oid)
        return dict(
            value,
            unit_schema=UNIT_SCHEMAS.get(value["kind"], {}),
            unit_schema_version="unit-review-v2.1",
        )

    @app.get("/api/chat-review/objects/{oid}/original")
    def original(oid: str, request: Request):
        who = actor(request)
        obj = call(protocol.view, who, oid)
        call(protocol._event, who, oid, "original_opened", {"snapshot": snapshot})
        return {"sources": obj["sources"], "notice": "Opening is not proof of human understanding"}

    @app.post("/api/chat-review/objects/{oid}/review")
    async def review(oid: str, request: Request):
        who, data = actor(request), await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("form"), dict):
            raise HTTPException(422, "Structured review form required")
        corrections = data["form"].get("corrections", {})
        if not isinstance(corrections, dict):
            raise HTTPException(422, "Structured corrections required")
        if corrections.get("review_schema", "unit-review-v2.1") != "unit-review-v2.1":
            raise HTTPException(422, "Unknown annotation schema version")
        obj = call(protocol.view, who, oid)
        judgments = corrections.get("unit_judgments", {})
        schema = UNIT_SCHEMAS.get(obj["kind"], {})
        if (
            not isinstance(judgments, dict)
            or set(judgments) - schema.keys()
            or any(
                value is not None and value not in schema[key] for key, value in judgments.items()
            )
        ):
            raise HTTPException(422, "Unknown unit-specific judgment")
        if "unit_judgments" in corrections:
            corrections["review_schema"] = "unit-review-v2.1"
        if not data.get("draft") and not any(
            e["role"] == who for e in protocol._events("original_opened", oid)
        ):
            raise HTTPException(422, "Open the bound original before submitting")
        seconds = data.get("form", {}).get("interaction_seconds")
        if seconds is not None:
            if type(seconds) is not int or not 0 <= seconds <= 86400:
                raise HTTPException(422, "Bounded interaction duration required")
            call(
                protocol._event,
                who,
                oid,
                "interaction_timing",
                {
                    "seconds": seconds,
                    "semantics": "SELF_REPORTED_UI_TIME_NOT_READING_PROOF",
                    "draft": bool(data.get("draft")),
                },
            )
        account = protocol.account(who)
        method = protocol.adjudicate if account["role"] == "adjudicator" else protocol.save
        if account["role"] == "adjudicator" and data.get("draft"):
            method = protocol.adjudication_draft
        if account["role"] == "reviewer":
            return call(method, who, oid, data["form"], draft=data.get("draft", False))
        return call(method, who, oid, data["form"])

    @app.post("/api/chat-review/accounts")
    async def accounts(request: Request):
        data = await request.json()
        return call(protocol.create_account, actor(request), **data)

    @app.post("/api/chat-review/assignment")
    async def assign(request: Request):
        data = await request.json()
        return call(
            protocol.assign,
            actor(request),
            data["object_id"],
            data["reviewers"],
            data["adjudicator"],
        )

    @app.post("/api/chat-review/objects")
    async def register(request: Request):
        return call(protocol.register, actor(request), **await request.json())

    @app.post("/api/chat-review/export")
    def export(request: Request):
        who = actor(request)
        return {
            "labels": call(protocol.export_gold, who),
            "audit": call(protocol.audit_export, who),
        }

    return protocol
