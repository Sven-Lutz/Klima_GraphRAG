"""Single FastAPI entry point for the GraphRAG_AL research interface."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crawler.core.answer_generation import AnswerGenerationError
from crawler.core.retrieval import ProjectionMismatchError, RetrievalError, RetrievalMode
from crawler.core.snapshots import SnapshotManifestError
from crawler.web.service import QueryOptions, ResearchApplicationService

ASSET_DIR = Path(__file__).with_name("static")


class QueryPayload(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    mode: RetrievalMode = RetrievalMode.SEMANTIC
    top_k: int = Field(default=8, ge=1, le=100)
    max_evidence_items: int = Field(default=8, ge=1, le=100)
    max_evidence_characters: int = Field(default=24_000, ge=1, le=500_000)
    generate_answer: bool = False
    municipality_ids: list[str] = Field(default_factory=list, max_length=2056)
    topic: str = Field(default="", max_length=100)
    reference_year: int | None = Field(default=None, ge=1800, le=2100)


def _default_service() -> ResearchApplicationService:
    if os.environ.get("KLIMA_UI_FIXTURE") == "1":
        from crawler.web.fixture import create_fixture_service

        configured = os.environ.get("KLIMA_UI_FIXTURE_DIR")
        runtime_dir = (
            Path(configured).expanduser().resolve()
            if configured
            else Path(tempfile.mkdtemp(prefix="graphrag-al-ui-fixture-"))
        )
        return create_fixture_service(runtime_dir)
    from crawler.web.runtime import create_production_service

    return create_production_service()


def _safe_error(exc: Exception) -> tuple[int, str, str]:
    if (
        isinstance(exc, ValueError)
        and str(exc) == "Ambiguous municipality name; supply explicit --ags"
    ):
        return (
            422,
            "ambiguous_municipality",
            "Gemeindename mehrdeutig. Bitte die konkrete Gemeinde mit AGS im Filter auswählen.",
        )
    if isinstance(exc, ProjectionMismatchError):
        return 409, "snapshot_mismatch", "Graph projection does not match the selected snapshot."
    if isinstance(exc, SnapshotManifestError):
        return 409, "snapshot_mismatch", "The selected snapshot manifest is invalid or stale."
    if isinstance(exc, RetrievalError):
        message = str(exc)
        if message in {
            "snapshot_changed",
            "method_unavailable_for_snapshot",
            "graph_year_filter_not_supported",
        }:
            return (
                409 if message == "snapshot_changed" else 422,
                message,
                "The selected snapshot or setting is unavailable.",
            )
        code = "neo4j_unavailable" if "Neo4j" in message else "backend_validation_failure"
        return 422, code, "The selected retrieval method or snapshot is unavailable."
    if isinstance(exc, AnswerGenerationError):
        return 422, exc.code, exc.safe_message
    if str(exc) == "answer_generation_not_configured":
        return 503, "provider_unavailable", "Answer generation is not configured."
    return 500, "backend_failure", "The research request could not be completed safely."


def create_app(
    service: ResearchApplicationService | None = None,
    *,
    response_timeout: float = 15,
    query_budget_per_minute: int = 12,
    allowed_origin: str | None = None,
) -> FastAPI:
    if allowed_origin:
        parsed_origin = urlsplit(allowed_origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
            or parsed_origin.username
            or parsed_origin.password
        ):
            raise ValueError("Pilot origin must be an explicit HTTPS origin")
    application_service = service or _default_service()
    build_files = [
        Path(__file__),
        *[
            Path(__file__).with_name(name)
            for name in [
                "bavaria.py",
                "service.py",
                "graph_explorer.py",
                "pilot.py",
                "pilot_bundle.py",
                "minimal_policy.py",
                "minimal_service.py",
                "minimal_external.py",
                "quick_gateway.py",
                "local_product.py",
                "local_index.py",
                "validation.py",
                "query_planning.py",
                "product_retrieval.py",
                "product_research.py",
                "research_restore.py",
                "evidence_synthesis.py",
                "semantic_index.py",
                "grounded_provider.py",
                "scope_conditions.py",
                "review_feedback.py",
                "review_observations.py",
                "review_protocol.py",
                "evaluation_dashboard.py",
                "coverage_analysis.py",
                "preview.py",
            ]
        ],
        *sorted((Path(__file__).parents[1] / "core").glob("*.py")),
        *sorted((Path(__file__).parents[2] / "research" / "product_evaluation").glob("*.py")),
        *sorted(ASSET_DIR.glob("*.js")),
        *sorted(ASSET_DIR.glob("*.css")),
        *sorted(ASSET_DIR.glob("*.html")),
    ]
    release_id = (
        "preview-sha256:"
        + hashlib.sha256(
            b"".join(
                str(path.relative_to(Path(__file__).parents[2])).encode()
                + b"\0"
                + path.read_bytes()
                for path in build_files
            )
        ).hexdigest()
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        result_store = getattr(application_service, "store", None)

        async def cleanup_results():
            while True:
                await asyncio.sleep(60)
                if result_store is not None:
                    result_store.purge_expired()

        task = (
            asyncio.create_task(cleanup_results())
            if getattr(result_store, "ttl_seconds", None)
            else None
        )
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            close = getattr(application_service, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="GraphRAG_AL Research",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.research_service = application_service
    app.state.release_id = release_id
    query_lock = threading.Lock()
    if not 1 <= query_budget_per_minute <= 240:
        raise ValueError("Query budget must be in 1..240")
    recent_queries: deque[float] = deque(maxlen=query_budget_per_minute)
    recent_originals: deque[float] = deque(maxlen=24)
    app.state.query_lock = query_lock
    app.state.recent_queries = recent_queries
    app.state.query_budget_per_minute = query_budget_per_minute
    app.state.response_timeout = response_timeout

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        expected_origin = allowed_origin or str(request.base_url).rstrip("/")
        host_allowed = (
            request.headers.get("host") == urlsplit(allowed_origin).netloc
            if allowed_origin
            else host in {"localhost", "127.0.0.1", "::1", "testserver"}
        )
        if not host_allowed or (origin and origin != expected_origin):
            return JSONResponse(
                {"error": {"code": "local_access_only", "message": "Local preview only."}},
                status_code=403,
            )
        if request.method == "POST":
            total = 0
            parts = []
            async for part in request.stream():
                total += len(part)
                limit = (
                    8 * 1024**2
                    if hasattr(app.state, "validation")
                    and request.url.path
                    in {
                        "/api/validation/import",
                        "/api/review-protocol/import",
                    }
                    else 24 * 1024
                )
                if total > limit:
                    return JSONResponse(
                        {"error": {"code": "request_too_large", "message": "Request too large."}},
                        status_code=413,
                    )
                parts.append(part)
            request._body = b"".join(parts)
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'self'"
            ),
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=ASSET_DIR), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "malformed_configuration",
                    "message": "Query settings are incomplete or outside supported bounds.",
                }
            },
        )

    @app.get("/")
    async def index():
        return FileResponse(ASSET_DIR / "index.html")

    @app.get("/methodik")
    async def methodology():
        return FileResponse(ASSET_DIR / "methodology.html")

    @app.get("/api/status")
    async def status():
        return {**application_service.status(), "release_id": release_id}

    @app.post("/api/query")
    async def query(payload: QueryPayload):
        try:
            now = time.monotonic()
            if len(recent_queries) == query_budget_per_minute and now - recent_queries[0] < 60:
                return JSONResponse(
                    {
                        "error": {
                            "code": "rate_limited",
                            "message": "Query budget reached. Try again later.",
                        }
                    },
                    status_code=429,
                )
            if not query_lock.acquire(blocking=False):
                return JSONResponse(
                    {"error": {"code": "busy", "message": "A query is still running."}},
                    status_code=429,
                )
            recent_queries.append(now)
            options = QueryOptions(
                question=payload.question.strip(),
                mode=payload.mode,
                top_k=payload.top_k,
                max_evidence_items=payload.max_evidence_items,
                max_evidence_characters=payload.max_evidence_characters,
                generate_answer=payload.generate_answer,
                municipality_ids=tuple(payload.municipality_ids),
                topic=payload.topic.strip(),
                reference_year=payload.reference_year,
            )

            def execute_bounded():
                try:
                    return application_service.execute(options)
                finally:
                    query_lock.release()

            task = asyncio.get_running_loop().run_in_executor(None, execute_bounded)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=response_timeout)
            except asyncio.TimeoutError:
                task.add_done_callback(
                    lambda done: done.exception() if not done.cancelled() else None
                )
                return JSONResponse(
                    {
                        "error": {
                            "code": "timeout",
                            "message": "Response timeout. A single worker may still be completing.",
                        }
                    },
                    status_code=504,
                )
        except Exception as exc:
            status_code, code, message = _safe_error(exc)
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": code, "message": message}},
            )

    @app.get("/api/graph")
    async def graph(
        root: str | None = None,
        depth: int = 1,
        predicate: str = "",
        entity_type: str = "",
        year: int | None = None,
        target: str | None = None,
    ):
        from crawler.web.graph_explorer import GraphExplorer

        workspace = getattr(application_service, "workspace", None)
        if workspace is None or not getattr(workspace, "relations_db", None):
            return JSONResponse({"error": {"code": "graph_unavailable"}}, status_code=503)
        try:
            checker = getattr(application_service.retriever, "check_identity", None)
            if checker:
                checker()
            graph_view = getattr(application_service, "graph_view", None)
            if graph_view is not None:
                return graph_view(root, depth, predicate, entity_type, year, target)
            explorer = GraphExplorer(workspace)
            return (
                explorer.subgraph(root, depth, predicate, entity_type, year, target)
                if root
                else explorer.overview()
            )
        except (ValueError, RetrievalError):
            return JSONResponse({"error": {"code": "invalid_graph_scope"}}, status_code=422)

    @app.get("/api/graph/evidence/{relation_id}")
    async def graph_evidence(relation_id: str):
        reader = getattr(application_service, "graph_evidence", None)
        if reader is None:
            return JSONResponse({"error": {"code": "graph_unavailable"}}, status_code=503)
        if not query_lock.acquire(blocking=False):
            return JSONResponse({"error": {"code": "busy"}}, status_code=429)

        def read_edge():
            try:
                return reader(relation_id)
            finally:
                query_lock.release()

        task = asyncio.get_running_loop().run_in_executor(None, read_edge)
        try:
            return await asyncio.wait_for(asyncio.shield(task), response_timeout)
        except Exception:
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            return JSONResponse({"error": {"code": "graph_evidence_unavailable"}}, status_code=422)

    @app.get("/api/source/{result_id}/{evidence_id}")
    async def original(result_id: str, evidence_id: str, raw: bool = False):
        reader = getattr(application_service, "original", None)
        if reader is None:
            return JSONResponse({"error": {"code": "original_not_available"}}, status_code=404)
        now = time.monotonic()
        if len(recent_originals) == 24 and now - recent_originals[0] < 60:
            return JSONResponse({"error": {"code": "rate_limited"}}, status_code=429)
        if not query_lock.acquire(blocking=False):
            return JSONResponse({"error": {"code": "busy"}}, status_code=429)
        recent_originals.append(now)

        def read():
            try:
                return reader(result_id, evidence_id, raw)
            finally:
                query_lock.release()

        task = asyncio.get_running_loop().run_in_executor(None, read)
        try:
            data, media, filename = await asyncio.wait_for(asyncio.shield(task), response_timeout)
            return Response(
                data,
                media_type=media,
                headers={
                    "Content-Disposition": ("attachment" if raw else "inline")
                    + '; filename="'
                    + filename
                    + '"',
                    "Content-Security-Policy": (
                        "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
                        if media == "application/pdf"
                        else "sandbox; default-src 'none'; style-src 'unsafe-inline'"
                    ),
                },
            )
        except Exception:
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            return JSONResponse(
                {
                    "error": {
                        "code": "original_not_available",
                        "message": "Archived original unavailable or identity check failed.",
                    }
                },
                status_code=404,
            )

    @app.get("/api/export/{result_id}.{format_name}")
    async def export(result_id: str, format_name: str):
        if format_name not in {"json", "md"}:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "unsupported_export", "message": "Unknown export."}},
            )
        rendered = application_service.export(
            result_id, "json" if format_name == "json" else "markdown"
        )
        if rendered is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "result_not_found", "message": "Result expired."}},
            )
        media_type = "application/json" if format_name == "json" else "text/markdown"
        return PlainTextResponse(
            rendered,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="graphrag-result.{format_name}"'
            },
        )

    return app
