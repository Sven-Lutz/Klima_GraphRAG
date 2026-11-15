"""Local product evaluation dashboard with sealed holdout and shared resource gates."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from crawler.core.retrieval import RetrievalMode
from crawler.web.service import QueryOptions
from research.gold_studio.source_bindings import required_claim_context_ids
from research.gold_studio.store import digest
from research.product_evaluation.catalog import load_questions, public_manifest
from research.product_evaluation.review_bridge import reference_from_review_export
from research.product_evaluation.runner import execute

ASSETS = Path(__file__).with_name("static")
BROWSABLE = {"development", "regression", "human_candidates", "adversarial"}
RUNNABLE = {"development", "regression", "adversarial"}


def normalize_product_result(payload):
    retrieval = payload.get("retrieval_result", {})
    answer = payload.get("answer_result")
    generation = (answer or {}).get("generation", {})
    result = dict(
        ranked_ids=[e["source_id"] for e in payload.get("evidence", [])],
        snapshot_id=payload.get("snapshot"),
        query_sha256=hashlib.sha256(retrieval.get("query", "").encode()).hexdigest(),
        claims=[
            {
                **claim,
                "context_source_ids": required_claim_context_ids(
                    claim.get("source_ids", []), retrieval.get("items", [])
                ),
            }
            for claim in payload.get("answer_assessment", {}).get("claims", [])
        ],
        retrieval_digest=digest(retrieval.get("items", [])),
        scope_ids=payload.get("query_plan", {}).get("municipality_ids", []),
        provider=generation.get("provider"),
        model=generation.get("model"),
        prompt_version=generation.get("prompt_version"),
        parameters=generation.get("parameters", {}),
        input_tokens=generation.get("input_tokens"),
        output_tokens=generation.get("output_tokens"),
        provider_cost=0
        if generation.get("provider") in {"local_extractive", "local-extractive", "ollama_grounded"}
        else None,
    )
    if answer:
        result["abstained"] = answer.get("status") == "insufficient_evidence"
        result["answer_status"] = answer.get("status")
        result["answer_text_sha256"] = hashlib.sha256(
            answer.get("answer_text", "").encode()
        ).hexdigest()
        result["errors"] = {
            "generation_failed": ["answer_synthesis"],
            "grounding_failed": ["citation"],
        }.get(answer.get("status"), [])
    if answer is None:
        result.update(
            provider="none", model="none", input_tokens=0, output_tokens=0, provider_cost=0
        )
    assessment = payload.get("answer_assessment") or {}
    result["synthesis_protocol"] = assessment.get("method")
    result["synthesis_prompt_sha256"] = (assessment.get("model_validation") or {}).get(
        "prompt_sha256"
    )
    result["ranked_id_namespace"] = "source_unit_id"
    return result


def attach_evaluation_dashboard(app, service, directory, *, run_directory=None):
    directory = Path(directory).resolve()
    runs = (
        Path(run_directory).resolve()
        if run_directory
        else directory.with_name(directory.name + "-runs")
    )
    app.state.evaluation_directory = directory
    app.state.evaluation_run_directory = runs
    pending_jobs = {}

    def available():
        if not (directory / "manifest.json").exists():
            raise HTTPException(503, "Question catalog not generated; see PRODUCT_EVALUATION.md")
        manifest = public_manifest(directory)
        if manifest.get("snapshot_id") != service.database_snapshot_id:
            raise HTTPException(409, "Evaluation catalog belongs to another snapshot")
        return manifest

    def operator(request):
        protocol = getattr(app.state, "review_protocol", None)
        if protocol is None:
            raise HTTPException(
                503, "Independent review accounts are required for evaluation writes"
            )
        try:
            who = protocol.session(request.cookies.get("review_protocol_session"))["username"]
            protocol.require(who, "admin", "adjudicator")
            return who
        except PermissionError as exc:
            raise HTTPException(403, "Evaluation operator login required") from exc

    def is_admin(request):
        protocol = getattr(app.state, "review_protocol", None)
        if protocol is None:
            return False
        try:
            return (
                protocol.session(request.cookies.get("review_protocol_session"))["role"] == "admin"
            )
        except PermissionError:
            return False

    def has_references(summary):
        # Treat absent/legacy reference metadata as private; only a verified
        # empty reference map can be shown without revealing prior judgments.
        return summary.get("configuration", {}).get("reference_sha256") != digest({})

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Structured JSON required") from exc
        if not isinstance(value, dict) or len(json.dumps(value).encode()) > 1024**2:
            raise HTTPException(422, "Bounded JSON object required")
        return value

    def split_rows(split):
        if split not in BROWSABLE:
            raise HTTPException(403, "Sealed holdout is excluded from product development")
        try:
            return load_questions(directory, split)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "Question split binding invalid") from exc

    @app.get("/evaluation")
    async def evaluation_page():
        return FileResponse(ASSETS / "evaluation.html")

    @app.get("/api/evaluation/catalog")
    def catalog():
        return available()

    @app.get("/api/evaluation/questions")
    def questions(split: str = "regression", offset: int = 0, limit: int = 25, search: str = ""):
        available()
        if offset < 0 or not 1 <= limit <= 100 or len(search) > 200:
            raise HTTPException(422, "Invalid bounded page")
        rows = split_rows(split)
        if search:
            rows = [
                q
                for q in rows
                if search.casefold()
                in (q["question"] + " " + q["region"] + " " + q["topic"]).casefold()
            ]
        return dict(
            total=len(rows),
            offset=offset,
            items=[
                {
                    key: q[key]
                    for key in [
                        "question_id",
                        "question",
                        "municipality_name",
                        "region",
                        "topic",
                        "question_type",
                        "complexity",
                        "hops",
                        "split",
                        "answerability",
                        "evidence_availability",
                        "judgment_origin",
                    ]
                }
                for q in rows[offset : offset + limit]
            ],
        )

    @app.get("/api/evaluation/review-feedback")
    def feedback_status(request: Request):
        operator(request)
        available()
        feedback = getattr(app.state, "review_feedback", None)
        try:
            return feedback.status() if feedback else dict(state="NOT_ATTACHED", references={})
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(503, "Automatic review feedback cannot be verified") from exc

    @app.get("/api/evaluation/review-observations")
    def reviewed_observations(
        request: Request, offset: int = 0, limit: int = 20, download: bool = False
    ):
        who = operator(request)
        if app.state.review_protocol.account(who)["role"] != "admin":
            raise HTTPException(403, "Reviewed answer judgments are private to the operator")
        if not 0 <= offset <= 100000 or not 1 <= limit <= 50:
            raise HTTPException(422, "Bounded observation page required")
        feedback = getattr(app.state, "review_feedback", None)
        if feedback is None:
            raise HTTPException(503, "Research review feedback is not attached")
        try:
            status = feedback.status()
            if status.get("state") != "READY":
                return dict(state=status["state"], cases=0, results=[])
            report = feedback.observations.report(status["gold_sha256"])
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(503, "Reviewed research observations cannot be verified") from exc
        if download:
            return JSONResponse(
                report,
                headers={
                    "Content-Disposition": (
                        'attachment; filename="reviewed-research-observations.json"'
                    )
                },
            )
        return {
            **report,
            "results": report["results"][offset : offset + limit],
            "offset": offset,
            "state": "READY",
            "page_content_is_subset": True,
        }

    @app.post("/api/evaluation/review-feedback/retry")
    async def feedback_retry(request: Request):
        who = operator(request)
        if app.state.review_protocol.account(who)["role"] != "admin":
            raise HTTPException(403, "Only the review operator may retry feedback")
        available()
        value = await body(request)
        oid = value.get("object_id")
        if not isinstance(oid, str) or oid not in app.state.review_protocol.objects():
            raise HTTPException(422, "An existing consolidated review object is required")
        feedback = getattr(app.state, "review_feedback", None)
        if feedback is None:
            raise HTTPException(503, "Automatic review feedback not attached")
        try:
            return feedback.sync(oid)
        except ValueError as exc:
            raise HTTPException(422, str(exc)[:180]) from exc

    @app.get("/api/evaluation/runs")
    def reports(request: Request):
        manifest = available()
        results = []
        if runs.exists():
            for p in sorted(
                runs.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )[:30]:
                value = json.loads(p.read_text())
                if value.get("configuration", {}).get("split") == "holdout":
                    continue
                if has_references(value) and not is_admin(request):
                    continue
                if (
                    value.get("configuration", {}).get("dataset_sha256")
                    != hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
                ):
                    continue
                results.append(dict(run_id=p.parent.name, summary=value))
        return dict(items=results, snapshot=manifest["snapshot_id"])

    @app.get("/api/evaluation/annotation-packet")
    def annotation_packet(request: Request, offset: int = 0, limit: int = 25):
        operator(request)
        available()
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, "Invalid bounded annotation page")
        rows = split_rows("human_candidates")
        objects = []
        for q in rows[offset : offset + limit]:
            seed = q["seed_evidence"]
            if seed is None:
                continue
            objects.append(
                dict(
                    kind="answer_evidence",
                    key=q["question_id"] + ":" + seed["unit_id"],
                    task_id=q["question_id"],
                    split="human_annotation_candidate",
                    version=1,
                    payload=dict(
                        title=q["question"],
                        question=q["question"],
                        proposed_value="Diese Passage ist als Beleg für die Frage geeignet.",
                        status="NOT_JUDGED",
                        generated_candidate=True,
                    ),
                    sources=[dict(sha256=seed["source_sha256"], unit_id=seed["unit_id"])],
                )
            )
        return dict(
            schema="product-annotation-packet-v1",
            snapshot_id=service.database_snapshot_id,
            human_gold=False,
            total=len(rows),
            objects=objects,
        )

    @app.get("/api/evaluation/export/{run_id}")
    def export(run_id: str, request: Request):
        available()
        if not run_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in run_id
        ):
            raise HTTPException(404, "Unknown evaluation run")
        p = runs / run_id / "summary.json"
        if not p.exists():
            raise HTTPException(404, "Unknown evaluation run")
        value = json.loads(p.read_text())
        if value.get("configuration", {}).get("split") == "holdout":
            raise HTTPException(403, "Sealed evaluation")
        if has_references(value) and not is_admin(request):
            raise HTTPException(403, "Reviewed evaluation reports are private to the operator")
        return JSONResponse(
            value, headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'}
        )

    @app.post("/api/evaluation/gold")
    async def import_gold(request: Request):
        who = operator(request)
        available()
        value = await body(request)
        split = value.get("split", "regression")
        rows = split_rows(split)
        payload = value.get("export")
        if not isinstance(payload, dict):
            raise HTTPException(422, "Versioned review export required")
        try:
            technical = app.state.review_protocol.technical
            refs = reference_from_review_export(
                payload, rows, allow_technical=technical, skip_other_tasks=True
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)[:180]) from exc
        from research.product_evaluation.runner import _write

        path = directory / "review-references" / (digest(payload) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(
            path,
            dict(
                operator=who,
                split=split,
                references=refs,
                origin="synthetic" if technical else "independent_human",
            ),
        )
        return dict(
            reference_id=path.stem,
            tasks=len(refs),
            split=split,
            origin="synthetic" if technical else "independent_human",
            complete_universe=False,
            question_ids=sorted(refs),
        )

    @app.post("/api/evaluation/run")
    async def run(request: Request):
        who = operator(request)
        available()
        value = await body(request)
        split = value.get("split", "regression")
        if split not in RUNNABLE | {"human_candidates"}:
            raise HTTPException(403, "Only development/regression/adversarial runs permitted here")
        generate_answer = value.get("generate_answer", True)
        if type(generate_answer) is not bool:
            raise HTTPException(422, "Explicit answer-generation boolean required")
        ids = value.get("question_ids")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 5
            or any(not isinstance(x, str) for x in ids)
            or len(set(ids)) != len(ids)
        ):
            raise HTTPException(422, "Select one to five distinct questions")
        rows = split_rows(split)
        selected = [q for q in rows if q["question_id"] in ids]
        if len(selected) != len(ids):
            raise HTTPException(422, "Selected question outside split")
        product = getattr(app.state, "research_product", None)
        if product is None:
            raise HTTPException(503, "Product answering pipeline not attached")
        try:
            mode = RetrievalMode(value.get("method", "lexical_expanded"))
        except ValueError as exc:
            raise HTTPException(422, "Unknown method") from exc
        if mode.value not in product.retriever.supported_modes:
            raise HTTPException(422, "Method unavailable for this snapshot")
        refs = {}
        auto_feedback = getattr(app.state, "review_feedback", None)
        if auto_feedback is not None and not value.get("reference_id"):
            try:
                automatic = auto_feedback.status()
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(503, "Automatic review feedback cannot be verified") from exc
            bound = automatic.get("references", {}).get(split)
            if bound:
                value["reference_id"] = bound["reference_id"]
        if value.get("reference_id"):
            ref_id = value["reference_id"]
            if (
                not isinstance(ref_id, str)
                or len(ref_id) != 64
                or any(c not in "0123456789abcdef" for c in ref_id)
            ):
                raise HTTPException(422, "Invalid reference identity")
            path = directory / "review-references" / (ref_id + ".json")
            if not path.exists():
                raise HTTPException(404, "Unknown reference export")
            ref = json.loads(path.read_text())
            if ref["split"] != split:
                raise HTTPException(422, "Reference split differs from evaluation")
            refs = {key: val for key, val in ref["references"].items() if key in ids}
        if split == "human_candidates" and any(key not in refs for key in ids):
            raise HTTPException(
                403, "Consolidated references are required for every annotation candidate"
            )
        if refs and not is_admin(request):
            raise HTTPException(403, "Reviewed evaluation requires the review operator")
        recent = app.state.recent_queries
        now = time.monotonic()
        lock = app.state.query_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(429, "A research/evaluation query is running")
        active = sum(now - timestamp < 60 for timestamp in recent)
        if active + len(ids) > app.state.query_budget_per_minute:
            lock.release()
            raise HTTPException(429, "Shared query budget reached")
        # Reserve shared capacity before dispatch; the running lock remains held on timeout.
        for _ in ids:
            recent.append(now)
        run_id = "product-" + uuid.uuid4().hex

        def evaluate():
            try:

                def predict(q):
                    payload = product.ask(
                        QueryOptions(
                            q["question"],
                            mode,
                            8,
                            8,
                            24000,
                            generate_answer,
                            tuple(q["municipality_ids"]),
                        ),
                        "technical_evaluation",
                    )
                    return normalize_product_result(payload)

                return execute(
                    directory,
                    runs / run_id,
                    predict,
                    split=split,
                    method=("product:" if generate_answer else "product-retrieval:") + mode.value,
                    reference_by_id=refs,
                    selected_question_ids=ids,
                )
            finally:
                lock.release()

        task = asyncio.get_running_loop().run_in_executor(None, evaluate)
        try:
            summary = await asyncio.wait_for(asyncio.shield(task), app.state.response_timeout)
            return dict(
                run_id=run_id,
                summary=summary,
                human_gold=False,
                reference_id=value.get("reference_id"),
            )
        except asyncio.TimeoutError:
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            for key in list(pending_jobs):
                if pending_jobs[key][1].done() and len(pending_jobs) >= 8:
                    pending_jobs.pop(key)
            key = uuid.uuid4().hex
            pending_jobs[key] = (who, task, run_id, value.get("reference_id"))
            return JSONResponse(
                dict(
                    status="EVALUATION_PENDING",
                    run_id=run_id,
                    poll_url="/api/evaluation/jobs/" + key,
                ),
                status_code=202,
                headers={"Retry-After": "2"},
            )

    @app.get("/api/evaluation/jobs/{job_id}")
    async def pending(job_id: str, request: Request):
        who = operator(request)
        job = pending_jobs.get(job_id)
        if job is None or job[0] != who:
            raise HTTPException(404, "Unknown evaluation job")
        task, run_id, reference_id = job[1:]
        if not task.done():
            return JSONResponse(
                dict(status="EVALUATION_PENDING", run_id=run_id),
                status_code=202,
                headers={"Retry-After": "2"},
            )
        if task.cancelled() or task.exception() is not None:
            return JSONResponse(
                dict(
                    error=dict(
                        code="evaluation_failed",
                        message="Evaluationslauf fehlgeschlagen; Laufprotokoll prüfen.",
                    ),
                    run_id=run_id,
                ),
                status_code=500,
            )
        return dict(
            run_id=run_id, summary=task.result(), human_gold=False, reference_id=reference_id
        )
