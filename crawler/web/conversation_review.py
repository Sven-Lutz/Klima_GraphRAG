"""Focused transport for the existing blinded, append-only ReviewProtocol.

No chat feedback ingestion. Original-opening receipt is a necessary interaction
check, never proof that a person read or understood a source.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from crawler.web.review_protocol import ReviewProtocol


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
    def queue(request: Request, kind: str = "", status: str = "", q: str = ""):
        return call(protocol.queue, actor(request), kind=kind, status=status, q=q)

    @app.get("/api/chat-review/objects/{oid}")
    def view(oid: str, request: Request):
        return call(protocol.view, actor(request), oid)

    @app.get("/api/chat-review/objects/{oid}/original")
    def original(oid: str, request: Request):
        who = actor(request)
        obj = call(protocol.view, who, oid)
        call(protocol._event, who, oid, "original_opened", {"snapshot": snapshot})
        return {"sources": obj["sources"], "notice": "Opening is not proof of human understanding"}

    @app.post("/api/chat-review/objects/{oid}/review")
    async def review(oid: str, request: Request):
        who, data = actor(request), await request.json()
        call(protocol.view, who, oid)
        if not data.get("draft") and not any(
            e["role"] == who for e in protocol._events("original_opened", oid)
        ):
            raise HTTPException(422, "Open the bound original before submitting")
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
