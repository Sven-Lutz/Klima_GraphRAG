"""Paired lexical ablation on the 21 previously exposed development questions only."""

import argparse
import hashlib
import json
import resource
import shutil
import time
from pathlib import Path

from crawler.core.retrieval import RetrievalMode
from crawler.web.bavaria import create_bavaria_service
from crawler.web.service import QueryOptions


def run(manifest, output):
    if shutil.disk_usage(".").free < 18 * 1024**3:
        raise RuntimeError("Storage write gate")
    cases = json.loads(Path("docs/product/release/rc/DEVELOPMENT_CASES.json").read_text())
    assert len(cases) == 21
    service = create_bavaria_service(manifest)
    rows = []
    for q in cases:
        arms = {}
        for mode in [RetrievalMode.LEXICAL, RetrievalMode.LEXICAL_EXPANDED]:
            start = time.monotonic()
            result = service.execute(
                QueryOptions(
                    question=q["query"],
                    mode=mode,
                    top_k=10,
                    max_evidence_items=10,
                    max_evidence_characters=20000,
                    generate_answer=False,
                    municipality_ids=(q["ags"],),
                )
            )
            arms[mode.value] = {
                "seconds": time.monotonic() - start,
                "evidence_ids": [e["evidence_id"] for e in result["evidence"]],
                "original_versions": [e["document"]["document_id"] for e in result["evidence"]],
                "source_ids": [e["source_id"] for e in result["evidence"]],
            }
        baseline = arms["lexical"]["evidence_ids"]
        expanded = arms["lexical_expanded"]["evidence_ids"]
        assert expanded[: len(baseline)] == baseline
        rows.append(
            {
                "task_id": q["task_id"],
                "query_sha256": q["query_sha256"],
                "ags": q["ags"],
                "topic": q["topic"],
                "snapshot_id": service.database_snapshot_id,
                "arms": arms,
                "baseline_preserved": True,
                "relevance": "UNASSESSED",
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    (output / "PAIRED.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    summary = {
        "questions": 21,
        "baseline_nonempty": sum(bool(r["arms"]["lexical"]["evidence_ids"]) for r in rows),
        "expanded_nonempty": sum(bool(r["arms"]["lexical_expanded"]["evidence_ids"]) for r in rows),
        "baseline_preserved": all(r["baseline_preserved"] for r in rows),
        "confirmation_queries_executed": 0,
        "human_relevance": False,
        "model_calls": 0,
        "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.manifest, a.output)
