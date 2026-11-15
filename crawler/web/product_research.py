"""Persistent local research conversations and canonical source-grounded answers."""

import asyncio
import hashlib
import json
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field

from crawler.core.answer_generation import CanonicalAnswerGenerator, JsonlAnswerRunRecorder
from crawler.core.retrieval import RetrievalMode
from crawler.web.evidence_synthesis import MODEL, EvidenceSynthesisProvider, assess_answer
from crawler.web.product_retrieval import ProductRetriever
from crawler.web.query_planning import plan_query
from crawler.web.service import (
    ProviderAcceptance,
    ProviderAvailability,
    ProviderGate,
    QueryOptions,
    ResearchApplicationService,
)
from crawler.web.source_structure import SourceStructureInspector


class ResearchHistory:
    def __init__(self, directory, snapshot):
        directory.mkdir(parents=True, exist_ok=True)
        self.path, self.snapshot = directory / "research-history.sqlite", snapshot
        if shutil.disk_usage(directory).free < 15 * 1024**3:
            raise ValueError("Research history requires existing 15GiB reserve")
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT, "
                "snapshot TEXT, at TEXT, query TEXT, response TEXT)"
            )
            c.commit()
        self.path.chmod(0o600)

    def history(self, owner, limit=30):
        with closing(sqlite3.connect(self.path)) as c:
            return [
                dict(id=r[0], at=r[1], question=r[2])
                for r in c.execute(
                    "SELECT id,at,query FROM conversations WHERE owner=? AND snapshot=? "
                    "ORDER BY at DESC,id LIMIT ?",
                    (owner, self.snapshot, limit),
                )
            ]

    def get(self, owner, key):
        with closing(sqlite3.connect(self.path)) as c:
            row = c.execute(
                "SELECT response FROM conversations WHERE id=? AND owner=? AND snapshot=?",
                (key, owner, self.snapshot),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, owner, response):
        text = json.dumps(response, ensure_ascii=False)
        if len(text.encode()) > 2 * 1024**2:
            raise ValueError("Research result exceeds bounded persistence budget")
        if self.path.stat().st_size + len(text.encode()) > 256 * 1024**2:
            raise ValueError(
                "Research history budget reached; export before creating another store"
            )
        if shutil.disk_usage(self.path.parent).free < 15 * 1024**3 + len(text.encode()):
            raise ValueError("Research history reserve unavailable")
        with closing(sqlite3.connect(self.path)) as c:
            c.execute(
                "INSERT INTO conversations VALUES (?,?,?,?,?,?)",
                (
                    response["result_id"],
                    owner,
                    self.snapshot,
                    datetime.now(timezone.utc).isoformat(),
                    response["query_plan"]["original"],
                    text,
                ),
            )
            c.commit()


class ResearchProduct:
    def __init__(self, service, directory, semantic=None, generation_config=None):
        self.service, self.semantic = service, semantic
        root = Path(__file__).resolve().parents[2]
        paths = [
            *Path(__file__).parent.glob("*.py"),
            *(root / "crawler/core").glob("*.py"),
            *(root / "research/bavaria").rglob("*.py"),
            *(root / "research/product_evaluation").glob("*.py"),
        ]
        inventory = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))
        }
        self.implementation = dict(
            schema="research-implementation-v1",
            files=inventory,
            sha256=hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
        )
        self.retriever = ProductRetriever(service.retriever, semantic)
        provider = EvidenceSynthesisProvider(
            service.workspace.municipalities,
            semantic._encoder
            if semantic is not None and semantic.status().get("complete")
            else None,
            source_structure=SourceStructureInspector(service.workspace, service.original_roots),
        )
        self.generative = generation_config is not None
        model = MODEL
        parameters = None
        if generation_config is not None:
            from crawler.web.grounded_provider import EvidenceBoundGenerationProvider

            provider = EvidenceBoundGenerationProvider(
                generation_config, audit_path=directory / "grounded-provider-runs.jsonl"
            )
            model = generation_config.model
            parameters = generation_config.parameters
        self.provider = provider
        self.generation_config = generation_config
        self.history = ResearchHistory(directory, service.database_snapshot_id)
        self.engine = ResearchApplicationService(
            retriever=self.retriever,
            generator=CanonicalAnswerGenerator(
                retriever=self.retriever,
                providers={provider.name: provider},
                run_recorder=JsonlAnswerRunRecorder(directory / "answer-runs.jsonl"),
            ),
            manifest_path=service.manifest_path,
            database_snapshot_id=service.database_snapshot_id,
            projection_id=service.projection_id,
            store=service.store,
            **({"generation_parameters": parameters} if parameters is not None else {}),
            provider_gate=ProviderGate(
                ProviderAvailability.ANSWER_GENERATION_READY,
                ProviderAcceptance.NOT_APPLICABLE,
                (
                    "Explicit local model; semantic quality requires content assessment."
                    if self.generative
                    else "Deterministic local extractive synthesis, not LLM inference."
                ),
                provider.name,
                model,
                (
                    "Experimentelle lokale Modellsynthese; nicht unabhängig validiert."
                    if self.generative
                    else "Quellengebundene extraktive Synthese verfügbar."
                ),
            ),
        )

    def ask(self, options, owner, previous_id=None):
        history = []
        if previous_id:
            previous = self.history.get(owner, previous_id)
            if previous is None:
                raise ValueError("Unknown conversation or conversation belongs to another session")
            history.append(
                dict(
                    question=previous["query_plan"]["rewritten"],
                    municipality_ids=previous["query_plan"]["municipality_ids"],
                )
            )
        plan = plan_query(
            self.service.workspace, options.question, options.municipality_ids, history
        )
        if plan.clarification:
            return dict(
                status="CLARIFICATION_REQUIRED",
                candidates=list(plan.clarification),
                query_plan=plan.to_dict(),
                message="Welchen Ort meinen Sie? Bitte eine Gemeinde auswählen.",
            )
        options = replace(options, question=plan.rewritten, municipality_ids=plan.municipality_ids)
        if self.generation_config:
            options = replace(
                options,
                max_evidence_items=min(
                    options.max_evidence_items, self.generation_config.max_evidence_items
                ),
                max_evidence_characters=min(
                    options.max_evidence_characters, self.generation_config.max_evidence_characters
                ),
            )
        payload = self.engine.execute(options)
        payload = self.service.enrich(payload, options.mode, options.reference_year)
        payload["query_plan"] = plan.to_dict()
        payload["answer_assessment"] = assess_answer(payload, plan)
        if not self.generative and payload.get("answer_result"):
            payload["answer_assessment"]["sentence_selection"] = self.provider.selection_for(
                payload["answer_result"].get("answer_text", ""), options.question
            )
        if self.generative:
            from crawler.web.grounded_provider import assess_model_answer

            payload["answer_assessment"] = assess_model_answer(payload, plan, self.provider)
        payload["research_product"] = True
        payload["parent_result_id"] = previous_id
        payload["snapshot"] = self.service.database_snapshot_id
        payload["schema"] = "research-conversation-v1"
        payload["implementation"] = self.implementation
        self.history.save(owner, payload)
        return payload


def attach_research_product(app, service, directory, semantic=None, generation_config=None):
    from crawler.web.app import QueryPayload, _safe_error

    class ResearchPayload(QueryPayload):
        mode: RetrievalMode = RetrievalMode.LEXICAL_EXPANDED
        generate_answer: bool = True
        top_k: int = Field(default=12, ge=1, le=20)
        previous_id: str | None = Field(default=None, max_length=100)

    product = ResearchProduct(service, directory, semantic, generation_config)
    app.state.research_product = product
    lock = app.state.query_lock
    jobs = {}  # Bounded, session-owned handles; durable results live in ResearchHistory.
    # Expose capabilities while retaining the original scientific-method catalog.
    status = service.status

    def product_status():
        payload = status()
        payload["research_product"] = dict(
            version="research-product-v1",
            answer_provider=product.provider.name,
            answer_model=product.engine.provider_gate.model,
            synthesis_status="EXPERIMENTAL_NOT_PRODUCT_ACCEPTED"
            if product.generative
            else "EXTRACTIVE",
            supported_modes=product.retriever.supported_modes,
            semantic=semantic.status()
            if semantic
            else dict(complete=False, state="NOT_CONFIGURED"),
            endpoints=dict(ask="/api/research/ask", history="/api/research/history"),
            independent_validation=False,
        )
        return payload

    service.status = product_status

    def owner(request):
        cookie = request.cookies.get("klima_research_session", "")
        return (
            cookie
            if len(cookie) == 64 and all(c in "0123456789abcdef" for c in cookie)
            else secrets.token_hex(32)
        )

    def with_reviews(result, request):
        feedback = getattr(app.state, "review_feedback", None)
        if feedback is None:
            return result
        show_decisions = False
        try:
            account = feedback.protocol.session(request.cookies.get("review_protocol_session"))
            show_decisions = account["role"] == "admin"
        except PermissionError:
            pass
        try:
            return feedback.overlay(result, show_decisions=show_decisions)
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            # Preserve the immutable unvalidated result; never invent a review status.
            result = json.loads(json.dumps(result))
            result["review_feedback"] = dict(state="UNAVAILABLE", error_class=type(exc).__name__)
            if result.get("answer_assessment"):
                result["answer_assessment"]["warnings"].append(
                    "Reviewstatus derzeit nicht prüfbar; keine Humanvalidierung angenommen."
                )
            return result

    @app.post("/api/research/ask")
    async def ask(payload: ResearchPayload, request: Request):
        recent = app.state.recent_queries
        if len(recent) == app.state.query_budget_per_minute and time.monotonic() - recent[0] < 60:
            return JSONResponse(
                {"error": {"code": "rate_limited", "message": "Suchbudget belegt."}}, 429
            )
        if not lock.acquire(blocking=False):
            return JSONResponse(
                {"error": {"code": "busy", "message": "Eine Recherche läuft."}}, 429
            )
        recent.append(time.monotonic())
        who = owner(request)

        def execute():
            try:
                return product.ask(
                    QueryOptions(
                        payload.question,
                        payload.mode,
                        payload.top_k,
                        payload.max_evidence_items,
                        payload.max_evidence_characters,
                        payload.generate_answer,
                        tuple(payload.municipality_ids),
                        payload.topic,
                        payload.reference_year,
                    ),
                    who,
                    payload.previous_id,
                )
            finally:
                lock.release()

        task = asyncio.get_running_loop().run_in_executor(None, execute)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), app.state.response_timeout)
            response = JSONResponse(with_reviews(result, request))
            response.set_cookie(
                "klima_research_session",
                who,
                httponly=True,
                samesite="strict",
                secure=bool(request.url.scheme == "https"),
                max_age=30 * 86400,
            )
            return response
        except asyncio.TimeoutError:
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            if product.generative:
                # Preserve the existing HTTP response deadline and shared compute lock.
                # Polling only reads the same job; it never launches another generation.
                for key in list(jobs):
                    if jobs[key][1].done() and len(jobs) >= 8:
                        jobs.pop(key)
                key = secrets.token_hex(32)
                jobs[key] = (who, task)
                response = JSONResponse(
                    {
                        "status": "RESEARCH_PENDING",
                        "job_id": key,
                        "poll_url": "/api/research/jobs/" + key,
                    },
                    202,
                    headers={"Retry-After": "2"},
                )
                response.set_cookie(
                    "klima_research_session",
                    who,
                    httponly=True,
                    samesite="strict",
                    secure=bool(request.url.scheme == "https"),
                    max_age=30 * 86400,
                )
                return response
            return JSONResponse(
                {
                    "error": {
                        "code": "timeout",
                        "message": "Zeitbudget erreicht; Auftrag kann noch abschließen.",
                    }
                },
                504,
            )
        except ValueError as exc:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_research_context",
                        "message": str(exc)
                        if "conversation" in str(exc)
                        else "Recherchekontext oder Ressourcenbudget ungültig.",
                    }
                },
                422,
            )
        except Exception as exc:
            code, label, message = _safe_error(exc)
            return JSONResponse({"error": {"code": label, "message": message}}, code)

    @app.get("/api/research/jobs/{job_id}")
    async def pending(job_id: str, request: Request):
        job = jobs.get(job_id)
        if job is None or job[0] != owner(request):
            return JSONResponse({"error": {"code": "not_found"}}, 404)
        task = job[1]
        if not task.done():
            return JSONResponse(
                {"status": "RESEARCH_PENDING", "job_id": job_id}, 202, headers={"Retry-After": "2"}
            )
        try:
            return with_reviews(task.result(), request)
        except ValueError:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_research_context",
                        "message": "Recherchekontext ungültig.",
                    }
                },
                422,
            )
        except Exception as exc:
            code, label, message = _safe_error(exc)
            return JSONResponse({"error": {"code": label, "message": message}}, code)

    @app.get("/api/research/history")
    def history(request: Request):
        return dict(
            items=product.history.history(owner(request)), snapshot=service.database_snapshot_id
        )

    @app.get("/api/research/history/{result_id}")
    def saved(result_id: str, request: Request):
        result = product.history.get(owner(request), result_id)
        if result is None:
            return JSONResponse({"error": {"code": "not_found"}}, 404)
        if service.store.get(result_id) is None:
            from crawler.web.research_restore import restore_result
            from crawler.web.service import StoredResult

            canonical = restore_result(result, service.database_snapshot_id)
            # Restore under the same immutable ID after validating snapshot/content identity.
            service.store.restore(StoredResult(result_id, canonical))
        return with_reviews(result, request)

    @app.get("/api/research/history/{result_id}/export/{format_name}")
    def export_history(result_id: str, format_name: str, request: Request):
        result = product.history.get(owner(request), result_id)
        if result is None or format_name not in {"json", "md"}:
            return JSONResponse({"error": {"code": "not_found"}}, 404)
        result = with_reviews(result, request)
        if format_name == "json":
            text = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            lines = [
                "# " + result["query_plan"]["original"],
                "",
                (result.get("answer_result") or {}).get("answer_text", "Belegsuche"),
                "",
                "Vorläufige Quellensynthese, nicht unabhängig validiert.",
                "",
            ]
            for evidence in result["evidence"]:
                lines += [
                    "## " + (evidence.get("citation") or {}).get("label", "Beleg"),
                    evidence["evidence_text"],
                    "",
                    evidence["document"]["source_url"],
                    "Quellenfassung: " + evidence["document"]["document_id"],
                    "",
                ]
            lines += ["## Query-Plan", json.dumps(result["query_plan"], ensure_ascii=False)]
            assessment = result.get("answer_assessment") or {}
            lines += [
                "",
                "## Aussagen und Prüfstatus",
                json.dumps(assessment.get("claims", []), ensure_ascii=False),
                "",
                "## Methodentrace",
                json.dumps(
                    {
                        key: assessment.get(key)
                        for key in ("method", "sentence_selection", "model_validation", "warnings")
                    },
                    ensure_ascii=False,
                ),
            ]
            text = "\n".join(lines)
        return PlainTextResponse(
            text,
            media_type="application/json" if format_name == "json" else "text/markdown",
            headers={
                "Content-Disposition": 'attachment; filename="klima-research.' + format_name + '"'
            },
        )

    @app.get("/api/research/diagnostics")
    def diagnostics():
        return dict(
            snapshot=service.database_snapshot_id,
            pipeline={
                "acquisition": "ARCHIVED_SNAPSHOT",
                "parsing": "SOURCE_UNITS",
                "normalization": "SNAPSHOT_BOUND",
                "ie": "PROVISIONAL_PARTIAL",
                "graph": "QUOTE_BOUND_PROVISIONAL",
                "lexical": "COMPLETE",
                "semantic": semantic.status() if semantic else "NOT_CONFIGURED",
                "answer": MODEL,
                "human_gold": "NOT_CLAIMED",
            },
            resources=dict(
                free_bytes=shutil.disk_usage(directory).free, reserve_bytes=15 * 1024**3
            ),
        )

    return product
