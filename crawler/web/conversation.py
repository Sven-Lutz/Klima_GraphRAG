"""Session-bound, versioned conversational adapter over the canonical research service.

No alternative retriever, E1 implementation or implicit model call. Qualification
is deliberately conservative; verbatim support is not independent entailment.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from crawler.core.retrieval import RetrievalMode
from crawler.web.conversation_planning import plan
from crawler.web.conversation_state import resolve
from crawler.web.service import QueryOptions

SCHEMA = "conversational-research-v2"
PROMPT = "verbatim-qualification-v1"
RESERVE = 15 * 1024**3
LIMIT = 64 * 1024**2
ASSETS = Path(__file__).with_name("static")
STOP = set(
    "viel viele welche welcher welches wie was wer gibt es die der das den dem ein eine einer "
    "und oder für von in im am zu zur zum auf mit ist sind wird werden wurde "
    "bitte mir dazu dort dann auch noch quelle quellen beispielstadt".split()
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def words(text):
    return set(re.findall(r"[a-zäöüß]{3,}", text.casefold())) - STOP


def qualify(question, text):
    """Aspect checks, not a retrieval ranker or entailment oracle."""
    if re.search(r"ignore|ignorier|system.?prompt|<script|javascript:", text, re.I):
        return False
    if not words(question).intersection(words(text)):
        return False
    if re.search(r"wie (?:viele|viel)|anzahl|leistung", question, re.I) and not re.search(
        r"\d[\d.,]*\s*(?:kWp|kW|MW|GWh|Euro|€|Ladepunkte|Ladesäulen)", text, re.I
    ):
        return False
    quantity_contracts = [
        (r"leistung", r"\d[\d.,]*\s*(?:kWp|kW|MW)(?!h)\b"),
        (r"ertrag|erzeugt|strommenge", r"\d[\d.,]*\s*(?:kWh|MWh|GWh)\b"),
        (r"ladepunkte", r"\d+\s+Ladepunkte\b"),
        (r"ladesäulen", r"\d+\s+Ladesäulen\b"),
        (r"kosten|förderbetrag", r"\d[\d.,]*\s*(?:Euro|€)"),
    ]
    for aspect, pattern in quantity_contracts:
        if re.search(aspect, question, re.I) and not re.search(pattern, text, re.I):
            return False
    years = re.findall(r"\b(?:19|20)\d{2}\b", question)
    if years and not all(y in text for y in years):
        return False
    if re.search(r"gemessen|tatsächlich|umgesetzt|in betrieb", question, re.I) and re.search(
        r"planen|geplant|prüft|erwartet|prognos", text, re.I
    ):
        return False
    return True


def kind(text):
    if re.search(r"prognos|erwartet|voraussichtlich", text, re.I):
        return "FORECAST"
    if re.search(r"plan|prüft|entwurf|soll", text, re.I):
        return "PLAN"
    if re.search(r"gemessen|messung", text, re.I):
        return "REPORTED_MEASUREMENT"
    if re.search(r"in betrieb|umgesetzt|abgeschlossen", text, re.I):
        return "REPORTED_IMPLEMENTATION"
    return "UNCLEAR"


class TurnInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    municipality_ids: list[str] = Field(default_factory=list, max_length=8)
    reference_year: int | None = Field(default=None, ge=1800, le=2100)
    parent_turn_id: str | None = Field(default=None, max_length=100)
    mode: RetrievalMode | None = None


class ChatStore:
    def __init__(self, directory, snapshot):
        self.directory, self.snapshot = Path(directory), snapshot
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "conversations.sqlite"
        self.budget()
        with self.conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, owner TEXT, csrf TEXT,
              expires REAL);
            CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,owner TEXT,snapshot TEXT,at TEXT);
            CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,chat TEXT,owner TEXT,parent TEXT,
              at TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS feedback(id TEXT,owner TEXT,turn TEXT,at TEXT,value TEXT);
            """)
        self.path.chmod(0o600)

    def conn(self):
        return ClosingConnection(self.path)

    def budget(self):
        if shutil.disk_usage(self.directory).free < RESERVE + LIMIT:
            raise ValueError("Chat reserve plus bounded persistence budget unavailable")
        if self.path.exists() and self.path.stat().st_size >= LIMIT:
            raise ValueError("Chat persistence budget reached")

    def purge_expired(self):
        with self.conn() as c:
            expired = [
                r[0]
                for r in c.execute("SELECT owner FROM sessions WHERE expires<=?", (time.time(),))
            ]
            for owner in expired:
                c.execute("DELETE FROM feedback WHERE owner=?", (owner,))
                c.execute("DELETE FROM turns WHERE owner=?", (owner,))
                c.execute("DELETE FROM chats WHERE owner=?", (owner,))
            c.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))

    def session(self, token):
        self.purge_expired()
        if not token:
            return None
        with self.conn() as c:
            r = c.execute(
                "SELECT owner,csrf FROM sessions WHERE token=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
        return dict(r) if r else None

    def create_session(self):
        self.budget()
        token, owner, csrf = (
            secrets.token_urlsafe(32),
            secrets.token_hex(20),
            secrets.token_urlsafe(32),
        )
        with self.conn() as c:
            if c.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 256:
                raise ValueError("Active local session budget reached")
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (digest(token), owner, csrf, time.time() + 86400),
            )
        return token, {"owner": owner, "csrf": csrf}

    def chat(self, owner, cid):
        with self.conn() as c:
            r = c.execute(
                "SELECT * FROM chats WHERE id=? AND owner=? AND snapshot=?",
                (cid, owner, self.snapshot),
            ).fetchone()
        if not r:
            raise HTTPException(404, "Conversation unavailable")
        return dict(r)

    def turns(self, owner, cid):
        self.chat(owner, cid)
        with self.conn() as c:
            return [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM turns WHERE chat=? AND owner=? ORDER BY at,id",
                    (cid, owner),
                )
            ]

    def save(self, owner, cid, result):
        self.budget()
        text = json.dumps(result, ensure_ascii=False)
        if self.path.stat().st_size + len(text.encode()) > LIMIT:
            raise ValueError("Chat persistence output budget reached")
        if len(text.encode()) > 1024**2:
            raise ValueError("Turn output budget exceeded")
        with self.conn() as c:
            c.execute(
                "INSERT INTO turns VALUES(?,?,?,?,?,?)",
                (result["turn_id"], cid, owner, result["parent_turn_id"], now(), text),
            )


class ClosingConnection:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.c = sqlite3.connect(self.path, timeout=5)
        self.c.row_factory = sqlite3.Row
        return self.c

    def __exit__(self, typ, value, trace):
        try:
            self.c.rollback() if typ else self.c.commit()
        finally:
            self.c.close()


class ConversationEngine:
    def __init__(self, service, store, generation=None):
        self.service, self.store = service, store
        self.generation = generation
        self.lock = threading.Lock()

    def answer(self, owner, cid, data):
        started = time.perf_counter()
        history = self.store.turns(owner, cid)
        parent = next((t for t in history if t["turn_id"] == data.parent_turn_id), None)
        if data.parent_turn_id and parent is None:
            raise HTTPException(404, "Parent turn unavailable")
        question = data.question.strip()
        if not question:
            raise HTTPException(422, "Nonempty question required")
        state, clarification = resolve(question, parent, data.municipality_ids, data.reference_year)
        scope = state["scope"]
        inherited = bool(parent and not data.municipality_ids and scope["municipality_ids"])
        effective = state["effective_question"]
        if parent and words(question) <= {"leistung", "jahr", "wann", "wieviel", "umfang"}:
            effective = question + " " + state["topic_question"]
            state["effective_question"] = effective
        selected_mode, query_plan = plan(effective, data.mode, self.service.retriever)
        result = dict(
            schema=SCHEMA,
            conversation_state=state,
            query_plan=query_plan,
            turn_id=secrets.token_hex(16),
            conversation_id=cid,
            parent_turn_id=data.parent_turn_id,
            question=question,
            answer_status="CLARIFICATION_REQUIRED" if clarification else "INSUFFICIENT_EVIDENCE",
            claims=[],
            missing_aspects=[],
            conflicts=[],
            context_scope=scope,
            inherited_scope=inherited,
            effective_question=effective,
            dataset_snapshot=self.store.snapshot,
            generation_provider="deterministic_extractive",
            model_version=PROMPT,
            prompt_version=PROMPT,
            run_id=secrets.token_hex(16),
            at_utc=now(),
            human_gold=False,
            validation_origin="TECHNICAL_NOT_HUMAN_VALIDATED",
            answer=clarification or "Im gebundenen Bestand fehlt eine antworttragende Belegstelle.",
            method_trace_id=None,
            trace=None,
            graph={"nodes": [], "edges": []},
        )
        if not clarification and not state["unresolved_place"]:
            if not self.lock.acquire(blocking=False):
                raise HTTPException(429, "Research worker busy; retry later")
            try:
                payload = self.service.execute(
                    QueryOptions(
                        question=effective,
                        mode=selected_mode,
                        generate_answer=False,
                        municipality_ids=(
                            () if self.service.demo_mode else tuple(scope["municipality_ids"])
                        ),
                        reference_year=None if self.service.demo_mode else scope["reference_year"],
                        top_k=8,
                        max_evidence_items=8,
                        max_evidence_characters=24000,
                    )
                )
            finally:
                self.lock.release()
            state["retrieval_filter_policy"] = (
                "FIXTURE_POSTFILTER_ONLY_TOPK_NOT_COMPLETE"
                if self.service.demo_mode
                else "SCOPE_BOUND_IN_RETRIEVAL_REQUEST"
            )
            result["method_trace_id"] = payload["result_id"]
            result["trace"] = payload
            for e in payload["evidence"][:8]:
                if scope["municipality_ids"] and not set(scope["municipality_ids"]).intersection(
                    e["document"]["municipality_ids"]
                ):
                    continue
                text = e["evidence_text"]
                if scope["reference_year"] and str(scope["reference_year"]) not in text:
                    continue
                if not qualify(effective, text):
                    continue
                # Unknown original-body hashes stay unknown; passage hashes are a separate identity.
                anchor = dict(
                    evidence_id=e["evidence_id"],
                    document_id=e["document"]["document_id"],
                    source_url=e["document"]["source_url"],
                    source_id=e["source_id"],
                    segment_id=e["segment_id"],
                    chunk_id=e["chunk_id"],
                    extraction_run_ids=e["extraction_run_ids"],
                    graph_artifact_ids=e["graph_artifact_ids"],
                    database_snapshot_id=e["database_snapshot_id"],
                    citation=e["citation"],
                    source_sha256=None,
                    source_sha256_status="NOT_AVAILABLE_IN_CONTRACT",
                    passage_sha256=digest(text),
                    quote=text,
                    offset_start=0,
                    offset_end=len(text),
                    page=None,
                    locator_kind="EXTRACTED_PASSAGE_OFFSETS_NOT_ORIGINAL_PAGE",
                    municipality_ids=e["document"]["municipality_ids"],
                )
                result["claims"].append(
                    dict(
                        claim_id="claim:" + digest(self.store.snapshot + e["evidence_id"] + text),
                        text=text,
                        evidence=[anchor],
                        evidence_kind=kind(text),
                        facts={
                            "quantities": re.findall(r"\d[\d.,]*\s*(?:kWp|kW|MW|GWh|€)", text),
                            "reference_year": None,
                            "event_time": None,
                            "actor_role": "UNRESOLVED",
                        },
                        support_status="VERBATIM_SOURCE_STATEMENT_NOT_INDEPENDENT_ENTAILMENT",
                        validation_origin="TECHNICAL_NOT_HUMAN_VALIDATED",
                    )
                )
            if result["claims"]:
                # No completeness oracle: compound/unresolved aspects remain explicitly partial.
                partial = bool(
                    re.search(r"\bund\b|aktuell|heute|vergleich|warum|wirkung", question, re.I)
                )
                result["answer_status"] = "PARTIAL" if partial else "ANSWERED"
                result["answer"] = "\n".join(c["text"] for c in result["claims"])
                if partial:
                    result["missing_aspects"] = [
                        "Nicht alle erfragten Aspekte sind unabhängig geprüft."
                    ]
                result["graph"] = payload.get("graph", result["graph"])
                selected_ids = {c["evidence"][0]["evidence_id"] for c in result["claims"]}
                all_edges = [
                    e
                    for e in result["graph"].get("edges", [])
                    if selected_ids.intersection(e.get("evidence_ids", []))
                ]
                edges = all_edges[:24]
                node_ids = {n for e in edges for n in (e["source"], e["target"])}
                result["graph"]["edges"] = edges
                result["graph"]["nodes"] = [
                    n for n in result["graph"]["nodes"] if n["id"] in node_ids
                ][:32]
                retained = {n["id"] for n in result["graph"]["nodes"]}
                bounded_edges = [e for e in edges if {e["source"], e["target"]} <= retained]
                result["graph"]["edges"] = bounded_edges
                result["graph"]["cutoff"] = len(all_edges) > len(bounded_edges)
                for edge in edges:
                    edge["validation_status"] = (
                        "TECHNICAL_PROVENANCE_LINK"
                        if edge["kind"] == "evidence_link"
                        else "EXTRACTED_UNVERIFIED"
                    )
                scope["source_ids"] = [e["evidence"][0]["evidence_id"] for e in result["claims"]]
            else:
                result["missing_aspects"] = [
                    "Keine zum gefragten Aspekt passende Quellenpassage qualifiziert."
                ]
        if self.generation and result["claims"]:
            from crawler.web.conversation_generation import PROMPT as GENERATION_PROMPT
            from crawler.web.conversation_generation import synthesize

            result["generation_provider"] = self.generation["metadata"]["provider"]
            result["prompt_version"] = GENERATION_PROMPT
            result["model_version"] = self.generation["metadata"]["model"]
            result["generation_configuration"] = dict(self.generation["metadata"])
            if not self.lock.acquire(blocking=False):
                raise HTTPException(429, "Research worker busy")
            try:
                generated, receipt = synthesize(
                    self.generation["provider"],
                    self.generation["metadata"]["model"],
                    effective,
                    result["claims"],
                )
                result["generation_receipt"] = receipt
                result["claims"] = generated
                result["missing_aspects"] = [
                    "Semantische Tragfähigkeit und vollständige Aspektabdeckung "
                    "nicht unabhängig geprüft."
                ]
                generated_ids = {e["evidence_id"] for c in generated for e in c["evidence"]}
                result["graph"]["edges"] = [
                    e
                    for e in result["graph"].get("edges", [])
                    if set(e.get("evidence_ids", [])) <= generated_ids
                ]
                retained_ids = {
                    n for e in result["graph"]["edges"] for n in (e["source"], e["target"])
                }
                result["graph"]["nodes"] = [
                    n for n in result["graph"].get("nodes", []) if n["id"] in retained_ids
                ]
                result["answer"] = "\n".join(c["text"] for c in generated)
                result["answer_status"] = "PARTIAL" if generated else "INSUFFICIENT_EVIDENCE"
                if not generated:
                    result["answer"] = "Das Modell konnte keine beleggebundene Antwort formulieren."
            except Exception:
                result["claims"] = []
                result["graph"] = {"nodes": [], "edges": []}
                result["answer_status"] = "ERROR"
                result["answer"] = "Generierung fehlgeschlagen; keine Modellantwort freigegeben."
                result["generation_receipt"] = {"status": "FAILED", "fallback": False}
            finally:
                self.lock.release()
        result["finished_at_utc"] = now()
        result["elapsed_seconds"] = time.perf_counter() - started
        result["implementation_file_sha256"] = hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest()
        self.store.save(owner, cid, result)
        return result


def create_conversation_app(service=None, directory=None, generation=None):
    """Loopback-only composition. External deployment requires a separate reviewed gateway."""
    if directory is None:
        directory = Path("generated/conversational-local")
    directory = Path(directory).resolve()
    if service is None:
        from crawler.web.fixture import create_fixture_service, load_fixture_service

        # A fresh immutable fixture directory per start; never overwrite an existing snapshot.
        fixture_dir = directory / "fixture"
        service = (
            load_fixture_service(fixture_dir)
            if (fixture_dir / "fixture-snapshot.sqlite.manifest.json").exists()
            else create_fixture_service(fixture_dir)
        )
    store = ChatStore(directory, service.database_snapshot_id)
    if generation:
        metadata = generation["metadata"]
        if (
            metadata.get("qualification") != "EXPLICITLY_QUALIFIED"
            or metadata.get("execution") != "local"
            or not metadata.get("qualification_artifact")
            or not metadata.get("digest")
        ):
            raise ValueError("Explicit local model qualification and digest required")
    engine = ConversationEngine(service, store, generation)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.chat_store, app.state.chat_engine = store, engine
    app.state.research_service = service
    rate = {}

    @app.middleware("http")
    async def boundary(request, call_next):
        origin = request.headers.get("origin")
        if (
            request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}
            or (origin and origin != str(request.base_url).rstrip("/"))
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse({"error": "local_same_origin_only"}, status_code=403)
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            session = store.session(request.cookies.get("klima_chat"))
            if not session or not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf"]
            ):
                return JSONResponse({"error": "csrf_or_session_required"}, status_code=403)
            body = b""
            async for part in request.stream():
                body += part
                if len(body) > 24 * 1024:
                    return JSONResponse({"error": "body_budget"}, status_code=413)
            request._body = body
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; img-src 'self' data:; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def actor(request):
        s = store.session(request.cookies.get("klima_chat"))
        if not s:
            raise HTTPException(401, "Session required")
        return s["owner"]

    @app.exception_handler(Exception)
    async def safe_failure(_request, _exc):
        return JSONResponse(
            {
                "answer_status": "ERROR",
                "error": "research_unavailable",
                "message": "Backend unavailable; no answer or validation inferred.",
            },
            status_code=503,
        )

    @app.exception_handler(ValueError)
    async def invalid(_request, _exc):
        return JSONResponse({"error": "bounded_operation_unavailable"}, status_code=422)

    @app.get("/api/chat/session")
    def bootstrap(request: Request):
        token = request.cookies.get("klima_chat")
        s = store.session(token)
        if token and not s:
            raise HTTPException(401, "Expired session; clear local site data")
        if not s:
            token, s = store.create_session()
        response = JSONResponse(
            {
                "csrf": s["csrf"],
                "schema": SCHEMA,
                "snapshot": store.snapshot,
                "fixture": service.demo_mode,
                "notice": service.demo_notice,
                "provider": (
                    generation["metadata"]["provider"] if generation else "deterministic_extractive"
                ),
                "retention": (
                    "Session 24h; logical deletion on expiry/logout or explicit delete; "
                    "backups excluded"
                ),
            }
        )
        response.set_cookie(
            "klima_chat",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=86400,
        )
        return response

    @app.delete("/api/chat/session")
    def close_session(request: Request):
        owner = actor(request)
        with store.conn() as c:
            c.execute("UPDATE sessions SET expires=0 WHERE owner=?", (owner,))
        store.purge_expired()
        response = JSONResponse({"logged_out": True, "conversations_logically_deleted": True})
        response.delete_cookie("klima_chat")
        return response

    @app.get("/api/chat/conversations")
    def chats(request: Request, q: str = ""):
        owner = actor(request)
        if len(q) > 200:
            raise HTTPException(422, "Bounded search required")
        with store.conn() as c:
            c.create_function("casefold", 1, lambda value: str(value or "").casefold())
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id,at,(SELECT json_extract(payload,'$.question') FROM turns t "
                    "WHERE t.chat=chats.id AND t.owner=chats.owner ORDER BY at,id LIMIT 1) title "
                    "FROM chats WHERE owner=? AND snapshot=? AND (?='' OR EXISTS ("
                    "SELECT 1 FROM turns t WHERE t.chat=chats.id AND t.owner=chats.owner AND "
                    "(instr(casefold(json_extract(payload,'$.question')),casefold(?))>0 OR "
                    "instr(casefold(json_extract(payload,'$.answer')),casefold(?))>0))) "
                    "ORDER BY at DESC",
                    (owner, store.snapshot, q, q, q),
                )
            ]

    @app.post("/api/chat/conversations")
    def create(request: Request):
        owner, cid = actor(request), secrets.token_hex(16)
        store.budget()
        with store.conn() as c:
            count = c.execute("SELECT count(*) FROM chats WHERE owner=?", (owner,)).fetchone()[0]
            if count >= 100:
                raise HTTPException(429, "Conversation budget reached")
            c.execute("INSERT INTO chats VALUES(?,?,?,?)", (cid, owner, store.snapshot, now()))
        return {"id": cid}

    @app.get("/api/chat/conversations/{cid}")
    def history(cid: str, request: Request):
        return store.turns(actor(request), cid)

    @app.delete("/api/chat/conversations/{cid}")
    def delete(cid: str, request: Request):
        owner = actor(request)
        store.chat(owner, cid)
        with store.conn() as c:
            c.execute(
                "DELETE FROM feedback WHERE owner=? AND turn IN "
                "(SELECT id FROM turns WHERE chat=? AND owner=?)",
                (owner, cid, owner),
            )
            c.execute("DELETE FROM turns WHERE chat=? AND owner=?", (cid, owner))
            c.execute("DELETE FROM chats WHERE id=? AND owner=?", (cid, owner))
        return {"deleted": True, "notice": "Logical deletion; filesystem backups not erased"}

    @app.post("/api/chat/conversations/{cid}/turns")
    async def ask(cid: str, data: TurnInput, request: Request):
        owner = actor(request)
        for key in list(rate):
            if not rate[key] or time.monotonic() - rate[key][-1] >= 60:
                rate.pop(key)
        if owner not in rate and len(rate) >= 256:
            raise HTTPException(429, "Active query budget reached")
        recent = rate.setdefault(owner, [])
        recent[:] = [t for t in recent if time.monotonic() - t < 60]
        if len(recent) >= 12:
            raise HTTPException(429, "12 turns per minute; retry later")
        recent.append(time.monotonic())
        try:
            return await asyncio.wait_for(
                run_in_threadpool(engine.answer, owner, cid, data), timeout=20
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                504, "Timeout; worker may still finish and preserve this turn"
            ) from exc

    @app.get("/api/chat/conversations/{cid}/export")
    def export(cid: str, request: Request):
        return {"schema": SCHEMA, "human_gold": False, "turns": store.turns(actor(request), cid)}

    @app.post("/api/chat/conversations/{cid}/feedback/{tid}")
    async def feedback(cid: str, tid: str, request: Request):
        owner = actor(request)
        if not any(t["turn_id"] == tid for t in store.turns(owner, cid)):
            raise HTTPException(404, "Turn unavailable")
        value = await request.json()
        if value not in {"helpful", "unsupported", "unclear"}:
            raise HTTPException(422, "Known feedback required")
        store.budget()
        with store.conn() as c:
            c.execute(
                "INSERT INTO feedback VALUES(?,?,?,?,?)",
                (secrets.token_hex(16), owner, tid, now(), value),
            )
        return {"saved": True, "human_gold": False, "purpose": "product_feedback_only"}

    @app.get("/api/chat/conversations/{cid}/evidence/{tid}/{eid}")
    def evidence(cid: str, tid: str, eid: str, request: Request):
        turns = store.turns(actor(request), cid)
        turn = next((t for t in turns if t["turn_id"] == tid), None)
        if turn:
            for claim in turn["claims"]:
                for anchor in claim["evidence"]:
                    if anchor["evidence_id"] == eid:
                        return anchor
        raise HTTPException(404, "Evidence unavailable")

    from crawler.web.conversation_workspaces import settings, visualization

    @app.get("/api/chat/settings")
    def active_settings(request: Request):
        actor(request)
        return settings(
            store.snapshot, service.demo_mode, generation["metadata"] if generation else None
        )

    @app.get("/api/chat/conversations/{cid}/visualization/{tid}")
    def chart_data(cid: str, tid: str, request: Request):
        turn = next((t for t in store.turns(actor(request), cid) if t["turn_id"] == tid), None)
        if turn is None:
            raise HTTPException(404, "Turn unavailable")
        return visualization(turn)

    app.mount("/static", StaticFiles(directory=ASSETS), name="static")

    @app.get("/")
    @app.get("/research")
    @app.get("/graph")
    @app.get("/visualizations")
    @app.get("/settings")
    @app.get("/coverage")
    def index():
        return FileResponse(ASSETS / "conversation.html")

    @app.get("/validation")
    def validation():
        return FileResponse(ASSETS / "conversation_validation.html")

    from crawler.web.conversation_review import attach_conversation_review

    # Review material is explicitly registered; never auto-promote chat feedback.
    app.state.review_sources = {}
    if service.demo_mode:
        from crawler.web.fixture import FIXTURE_TEXTS

        for ordinal, text in enumerate(FIXTURE_TEXTS):
            sha = digest(text)
            app.state.review_sources[sha] = {
                "sha256": sha,
                "quote": text,
                "unit_id": "synthetic-review-" + str(ordinal),
                "source_kind": "SYNTHETIC_REVIEW_DOCUMENT",
                "snapshot_id": store.snapshot,
                "page_ref": None,
                "note": "Own synthetic source bytes; not a municipal original or chat gold label",
            }
    attach_conversation_review(
        app,
        directory / "review",
        store.snapshot,
        technical=service.demo_mode,
        sources=app.state.review_sources,
    )
    if service.demo_mode:
        from crawler.web.conversation_demo import attach_demo_review

        attach_demo_review(
            app,
            directory / "review",
            app.state.conversation_review,
            app.state.review_sources,
            store,
        )
    return app


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        description="Loopback conversational research — synthetic fixture"
    )
    parser.add_argument("--directory", type=Path, default=Path("generated/conversational-local"))
    parser.add_argument("--port", type=int, default=8825)
    args = parser.parse_args()
    uvicorn.run(
        create_conversation_app(directory=args.directory),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )
