"""Same-snapshot product retrieval comparison; seed diagnostics are NOT gold relevance.

Never loads sealed holdout; does not invoke an answer model or rebuild indexes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from crawler.core.retrieval import RetrievalMode, RetrievalRequest
from crawler.web.bavaria import create_bavaria_service
from crawler.web.local_index import LocalIndex
from crawler.web.product_retrieval import ProductRetriever
from crawler.web.query_planning import plan_query
from crawler.web.semantic_index import SemanticIndex
from research.bavaria.workspace.core import write_guard
from research.product_evaluation.catalog import load_questions

METHODS = ("lexical", "lexical_expanded", "semantic", "hybrid", "graph")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["method"], "all")].append(row)
        groups[(row["method"], row["question_type"])].append(row)
    result = []
    for (method, question_type), group in sorted(groups.items()):
        successful = [r for r in group if r["status"] == "succeeded"]
        result.append(
            dict(
                method=method,
                question_type=question_type,
                cases=len(group),
                failures=len(group) - len(successful),
                empty=sum(not r["items"] for r in successful),
                median_latency_ms=statistics.median(r["latency_ms"] for r in successful)
                if successful
                else None,
                source_frame_mismatches=sum(r["source_frame_mismatches"] for r in successful),
                relevance_precision=None,
                relevance_recall=None,
                label_origin="NO_INDEPENDENT_RELEVANCE_LABELS",
            )
        )
    return result


def run(manifest, catalog, state, output, *, limit=None):
    questions = load_questions(catalog, "regression")
    if limit is not None:
        if limit < 1:
            raise ValueError("Positive limit required")
        questions = questions[:limit]
    sources = [
        Path(__file__),
        Path("crawler/web/product_retrieval.py"),
        Path("crawler/web/semantic_index.py"),
        Path("crawler/web/query_planning.py"),
        Path("crawler/web/scope_conditions.py"),
        Path("crawler/web/local_index.py"),
        Path("crawler/web/research_restore.py"),
        Path("crawler/web/bavaria.py"),
        Path("crawler/core/retrieval.py"),
        Path("crawler/core/provenance.py"),
        Path("research/bavaria/workspace/core.py"),
        Path("research/bavaria/federation.py"),
        Path("research/bavaria/semantic.py"),
    ]
    configuration = dict(
        schema="same-snapshot-retrieval-comparison-v1",
        catalog_sha256=sha(catalog / "manifest.json"),
        methods=METHODS,
        split="regression",
        top_k=8,
        question_ids=[q["question_id"] for q in questions],
        implementation={str(p): sha(p) for p in sources},
        provider="none",
        provider_cost=0,
        quality_scope="EXECUTION_AND_SOURCE_FRAME_ONLY_NOT_SEMANTIC_RELEVANCE",
    )
    if (
        not (state / "terms-v1.sqlite").is_file()
        or not (state / "semantic-v1" / "index.sqlite").is_file()
    ):
        raise ValueError("Explicit existing indexes required; no implicit rebuild")
    service = create_bavaria_service(manifest)
    try:
        if any(q["snapshot_id"] != service.database_snapshot_id for q in questions):
            raise ValueError("Question catalog belongs to another snapshot")
        service.workspace.discovery_index = LocalIndex(service.workspace, state / "terms-v1.sqlite")
        semantic = SemanticIndex(service.workspace, state / "semantic-v1")
        if not semantic.status().get("complete"):
            raise ValueError("Existing complete snapshot-bound dense index required")
        configuration["snapshot_id"] = service.database_snapshot_id
        configuration["semantic_index"] = semantic.status()
        output.mkdir(parents=True, exist_ok=True)
        config_path = output / "configuration.json"
        # JSON-normalize tuple values before immutable comparison.
        configuration = json.loads(json.dumps(configuration))
        if config_path.exists() and json.loads(config_path.read_text()) != configuration:
            raise ValueError("Immutable comparison configuration changed; choose fresh output")
        write_guard(config_path, 20000)
        config_path.write_text(json.dumps(configuration, ensure_ascii=False, indent=2))
        records_path = output / "results.jsonl"
        rows = (
            [json.loads(line) for line in records_path.read_text().splitlines()]
            if records_path.exists()
            else []
        )
        done = {(r["question_id"], r["method"]) for r in rows}
        retriever = ProductRetriever(service.retriever, semantic)
        for question in questions:
            plan = plan_query(
                service.workspace, question["question"], tuple(question["municipality_ids"])
            )
            for method in METHODS:
                if (question["question_id"], method) in done:
                    continue
                started = time.perf_counter()
                record = dict(
                    question_id=question["question_id"],
                    method=method,
                    question_type=question["question_type"],
                    status="running",
                )
                try:
                    result = retriever.retrieve(
                        RetrievalRequest(
                            plan.rewritten,
                            service.manifest_path,
                            RetrievalMode(method),
                            8,
                            service.projection_id if method in {"graph", "hybrid"} else None,
                            municipality_ids=plan.municipality_ids,
                        )
                    )
                    record.update(
                        status="succeeded",
                        items=[
                            dict(
                                evidence_id=i.evidence_id,
                                source_id=i.source_id,
                                municipality_ids=list(i.document.municipality_ids),
                                source_url=i.document.source_url,
                                heading=i.document.heading_context,
                                text_preview=i.evidence_text[:800],
                                text_sha256=hashlib.sha256(i.evidence_text.encode()).hexdigest(),
                            )
                            for i in result.items
                        ],
                        source_frame_mismatches=sum(
                            bool(plan.municipality_ids)
                            and not set(plan.municipality_ids).intersection(
                                i.document.municipality_ids
                            )
                            for i in result.items
                        ),
                        diagnostics=result.diagnostics,
                    )
                except Exception as exc:
                    record.update(status="failed", error_class=type(exc).__name__)
                record["latency_ms"] = (time.perf_counter() - started) * 1000
                raw = json.dumps(record, ensure_ascii=False) + "\n"
                write_guard(records_path, len(raw.encode()))
                with records_path.open("a") as stream:
                    stream.write(raw)
                rows.append(record)
                print(question["question_id"], method, record["status"], flush=True)
        summary = dict(
            configuration=configuration,
            groups=summarize(rows),
            results_sha256=sha(records_path),
            human_gold=False,
            holdout_accessed=False,
        )
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary
    finally:
        service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["manifest", "catalog", "state", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    run(**vars(arguments))
