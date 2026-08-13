"""Single FastAPI entry point for the GraphRAG_AL research interface."""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crawler.core.answer_generation import AnswerGenerationError
from crawler.core.retrieval import ProjectionMismatchError, RetrievalError, RetrievalMode
from crawler.core.snapshots import SnapshotManifestError
from crawler.web.fixture import create_fixture_service
from crawler.web.runtime import create_production_service
from crawler.web.service import QueryOptions, ResearchApplicationService

ASSET_DIR = Path(__file__).with_name("static")


class QueryPayload(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    mode: RetrievalMode = RetrievalMode.SEMANTIC
    top_k: int = Field(default=8, ge=1, le=100)
    max_evidence_items: int = Field(default=8, ge=1, le=100)
    max_evidence_characters: int = Field(default=24_000, ge=1, le=500_000)
    generate_answer: bool = False


def _default_service() -> ResearchApplicationService:
    if os.environ.get("KLIMA_UI_FIXTURE") == "1":
        configured = os.environ.get("KLIMA_UI_FIXTURE_DIR")
        runtime_dir = (
            Path(configured).expanduser().resolve()
            if configured
            else Path(tempfile.mkdtemp(prefix="graphrag-al-ui-fixture-"))
        )
        return create_fixture_service(runtime_dir)
    return create_production_service()


def _safe_error(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, ProjectionMismatchError):
        return 409, "snapshot_mismatch", "Graph projection does not match the selected snapshot."
    if isinstance(exc, SnapshotManifestError):
        return 409, "snapshot_mismatch", "The selected snapshot manifest is invalid or stale."
    if isinstance(exc, RetrievalError):
        message = str(exc)
        code = "neo4j_unavailable" if "Neo4j" in message else "backend_validation_failure"
        return 422, code, message
    if isinstance(exc, AnswerGenerationError):
        return 422, exc.code, exc.safe_message
    if str(exc) == "answer_generation_not_configured":
        return 503, "provider_unavailable", "Answer generation is not configured."
    return 500, "backend_failure", "The research request could not be completed safely."


def create_app(service: ResearchApplicationService | None = None) -> FastAPI:
    application_service = service or _default_service()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
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

    @app.get("/api/status")
    async def status():
        return application_service.status()

    @app.post("/api/query")
    async def query(payload: QueryPayload):
        try:
            return application_service.execute(
                QueryOptions(
                    question=payload.question.strip(),
                    mode=payload.mode,
                    top_k=payload.top_k,
                    max_evidence_items=payload.max_evidence_items,
                    max_evidence_characters=payload.max_evidence_characters,
                    generate_answer=payload.generate_answer,
                )
            )
        except Exception as exc:
            status_code, code, message = _safe_error(exc)
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": code, "message": message}},
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
