"""Capability and controlled-comparison contracts; no quality oracle or new retriever."""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from crawler.core.retrieval import RetrievalMode

LABELS = {
    "lexical": "BM25 / Lexical",
    "semantic": "Dense Retrieval",
    "hybrid": "Hybrid Retrieval",
    "graph": "Graph Traversal",
    "sql": "SQL / Structured Retrieval",
    "graphrag": "GraphRAG (qualifizierte Gesamtpipeline)",
}


def capabilities(service):
    supported = getattr(service.retriever, "supported_modes", None)
    if supported is None:
        supported = ["semantic"]
        if getattr(service.retriever, "graph_backend", None) is not None:
            supported += ["graph", "hybrid"]
    supported = [getattr(mode, "value", mode) for mode in supported]
    return [
        {
            "id": key,
            "label": label,
            "available": key in supported,
            "qualification": "SYNTHETIC_CONTRACT_ONLY" if service.demo_mode else "UNVALIDATED",
            "reason": (
                "Kontrollierte synthetische Vektoren/Graphantworten; kein realer Methodenbenchmark."
                if service.demo_mode and key in supported
                else "Aktiver Adapter; fachliche Qualität nicht unabhängig validiert."
                if key in supported
                else (
                    "Kein lexikalischer Adapter am aktiven Snapshot; "
                    "Bayern-Adapter hängt an ungeklärtem E1-Code."
                )
                if key == "lexical"
                else "Kein freigegebener SQL-Queryadapter für diesen Snapshot."
                if key == "sql"
                else (
                    "Keine qualifizierte Retrieval-Graph-Generator-Gesamtpipeline. "
                    "Graph Traversal ist separat."
                )
                if key == "graphrag"
                else getattr(service.retriever, "unavailable_reasons", {}).get(
                    key, "Kein kompatibler Adapter/Index am aktiven Snapshot konfiguriert."
                )
            ),
        }
        for key, label in LABELS.items()
    ]


class CompareInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    methods: list[RetrievalMode] = Field(min_length=2, max_length=5)
    parent_turn_id: str | None = Field(default=None, max_length=100)
    municipality_ids: list[str] = Field(default_factory=list, max_length=8)
    reference_year: int | None = Field(default=None, ge=1800, le=2100)


def attach_workbench(app, store, engine, actor, charge):
    from crawler.web.local_annotations import LocalEvents

    records = LocalEvents(store.directory / "comparisons", store.budget)

    @app.get("/api/chat/conversations/{cid}/comparisons")
    def comparisons(cid: str, request: Request):
        owner = actor(request)
        store.chat(owner, cid)
        return [r for r in records.read(owner) if r["conversation_id"] == cid]

    @app.get("/api/chat/experiments")
    def experiments(request: Request):
        return {
            "experiments": records.read(actor(request)),
            "quality_metrics": {
                name: {
                    "value": None,
                    "denominator": None,
                    "status": "NOT_EVALUATED",
                    "reason": "No independent frozen qrels or answer labels bound to these runs",
                }
                for name in [
                    "Recall@k",
                    "Precision@k",
                    "Evidence Bundle Completion",
                    "Claim Support",
                    "Answer Relevance",
                    "Aspect Completeness",
                    "False Abstention",
                ]
            },
            "reference_status": "UNVALIDATED",
            "historical_studies": "Separate frozen RQ1–RQ8/H1–H6/T1–T9; never pooled here",
            "timing_limits": (
                "Sequential local observation; CPU is process CPU, per-arm RAM not measured"
            ),
            "graph_advantage": "NOT_ESTABLISHED",
        }

    @app.get("/api/chat/methods")
    def methods(request: Request):
        actor(request)
        return {"snapshot": store.snapshot, "methods": capabilities(engine.service)}

    @app.post("/api/chat/conversations/{cid}/compare")
    async def compare(cid: str, data: CompareInput, request: Request):
        from starlette.concurrency import run_in_threadpool

        from crawler.web.conversation import TurnInput, now

        owner = actor(request)
        store.chat(owner, cid)
        available = {m["id"] for m in capabilities(engine.service) if m["available"]}
        chosen = [m.value for m in data.methods]
        if len(set(chosen)) != len(chosen) or not set(chosen) <= available:
            raise HTTPException(422, "Distinct available methods required")
        charge(owner, len(chosen))
        config = {
            "snapshot": store.snapshot,
            "question": data.question,
            "parent_turn_id": data.parent_turn_id,
            "municipality_ids": data.municipality_ids,
            "reference_year": data.reference_year,
            "generation": engine.generation["metadata"] if engine.generation else "extractive-v1",
            "evidence_budget": {"top_k": 8, "items": 8, "characters": 24000},
            "verification": "verbatim-qualification-v1; no independent entailment",
            "factor": "NATIVE_RETRIEVAL_PIPELINE",
            "isolated_graph_format_effect": False,
            "information_access": (
                "Native text/vector/curated-relation indexes; not an information ablation"
            ),
        }
        group = {
            "id": secrets.token_hex(16),
            "at": now(),
            "configuration": config,
            "configuration_sha256": hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest(),
            "order": chosen,
            "validation": "UNVALIDATED",
            "human_gold": False,
            "limits": [
                "Einzelmessung: Reihenfolge, Cache und Aufwärmen beeinflussen die Laufzeit.",
                "Trefferzahl misst weder Relevanz noch Vollständigkeit oder Richtigkeit.",
                "Ohne unabhängige Referenzlabels bleiben Qualitätsmetriken NOT_EVALUATED.",
                "Generator unverändert; Graphrelationen können zusätzliche Information enthalten.",
                "Dieser Vergleich isoliert keinen Graphformat- oder IE-Effekt.",
            ],
        }
        results = []
        for method in data.methods:
            if engine.service.database_snapshot_id != config["snapshot"]:
                raise HTTPException(409, "Snapshot changed; comparison invalid")
            started = time.perf_counter()
            cpu = time.process_time()
            try:
                turn = await run_in_threadpool(
                    engine.answer,
                    owner,
                    cid,
                    TurnInput(**data.model_dump(exclude={"methods"}), mode=method),
                    group,
                )
                results.append({"method": method.value, "status": "COMPLETED", "turn": turn})
            except HTTPException as exc:
                if exc.status_code in {401, 404, 410}:
                    raise
                results.append(
                    {"method": method.value, "status": "ERROR", "error": "METHOD_FAILED"}
                )
            except Exception:
                results.append(
                    {"method": method.value, "status": "ERROR", "error": "METHOD_FAILED"}
                )
            results[-1].update(
                elapsed_seconds=time.perf_counter() - started,
                process_cpu_seconds=time.process_time() - cpu,
                peak_ram_bytes=None,
                ram_status="NOT_MEASURED_PER_ARM",
                cost_status="NO_EXTERNAL_PAID_PROVIDER",
                independent_quality="UNVALIDATED",
            )
        receipt = {**group, "conversation_id": cid, "results": results}
        store.chat(owner, cid)
        return records.append(owner, receipt)
