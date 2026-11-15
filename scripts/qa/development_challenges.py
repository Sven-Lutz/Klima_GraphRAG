"""Bounded technical smoke of new exposed-frame questions; no relevance labels."""

import argparse
import hashlib
import json
import resource
import shutil
import signal
import time
from pathlib import Path

from crawler.core.retrieval import RetrievalMode
from crawler.web.bavaria import create_bavaria_service
from crawler.web.service import QueryOptions


def run(manifest: Path, questions: Path) -> dict:
    if shutil.disk_usage(".").free < 15 * 1024**3:
        raise RuntimeError("15 GiB SSD reserve required")
    spec = json.loads(questions.read_text())
    if spec["split"] != "development_diagnostic" or spec["human_gold"] is not False:
        raise ValueError("Development-only contract required")
    exposed = json.loads(Path("docs/product/graph-release/CATALOG.json").read_text())
    frames = {ags for c in exposed for ags in c["municipality_ids"]}
    cases = spec["cases"]
    if not 1 <= len(cases) <= 12 or any(c["ags"] not in frames for c in cases):
        raise ValueError("Only 1-12 already exposed frames allowed")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate challenge IDs")

    def deadline(_signum, _frame):
        raise TimeoutError("Technical challenge deadline")

    old_handler = signal.signal(signal.SIGALRM, deadline)
    started = time.monotonic()
    rows = []
    try:
        signal.alarm(35)
        service = create_bavaria_service(manifest)
        for case in cases:
            for mode in (RetrievalMode.LEXICAL, RetrievalMode.LEXICAL_EXPANDED):
                if time.monotonic() - started > 95:
                    raise TimeoutError("Total challenge budget")
                signal.alarm(15)
                before = time.monotonic()
                result = service.execute(
                    QueryOptions(
                        question=case["question"],
                        mode=mode,
                        top_k=5,
                        max_evidence_items=5,
                        max_evidence_characters=10000,
                        generate_answer=False,
                        municipality_ids=(case["ags"],),
                        reference_year=case["reference_year"],
                    )
                )
                rows.append(
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "method": mode.value,
                        "query_sha256": hashlib.sha256(case["question"].encode()).hexdigest(),
                        "seconds": round(time.monotonic() - before, 6),
                        "evidence_ids": [e["evidence_id"] for e in result["evidence"]],
                        "original_versions": [
                            e["document"]["document_id"] for e in result["evidence"]
                        ],
                        "search_scope": result["retrieval_result"]["search_scope"],
                        "requested_ags": case["ags"],
                        "reference_year": case["reference_year"],
                        "semantic_relevance": "UNASSESSED",
                        "generation": "disabled",
                    }
                )
        return {
            "schema": "klima-development-challenge-receipt-v1",
            "status": "PASS",
            "scope": (
                "Technical service execution and availability only; not answerability or relevance"
            ),
            "snapshot_id": service.database_snapshot_id,
            "questions_sha256": hashlib.sha256(questions.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "seconds": round(time.monotonic() - started, 6),
            "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "model_calls": 0,
            "confirmation_queries_executed": 0,
            "human_gold": False,
            "records": rows,
        }
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--questions", type=Path, default=Path("docs/pilot/DEVELOPMENT_CHALLENGE_QUESTIONS.json")
    )
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.questions), ensure_ascii=False, indent=2))
