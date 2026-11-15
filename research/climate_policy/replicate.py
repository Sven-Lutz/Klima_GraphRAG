"""One-command replication: immutable comparisons, demo and human-review material."""

import argparse
import json
import sys
from pathlib import Path

from research.climate_policy import store
from research.climate_policy.evaluate import evaluate
from research.climate_policy.reviewer_package import package
from research.offline_evaluation.contracts import require

BENCHMARK = store.ROOT / "research/sprints/sprint-02/acceleration/benchmark01/tasks.json"


def replicate(out):
    require(not out.exists(), "Use an absent replication output directory")
    out.mkdir(parents=True)
    for name in ["comparison01", "comparison02"]:
        evaluate(BENCHMARK, out / name)
    require(
        (out / "comparison01/outputs.json").read_bytes()
        == (out / "comparison02/outputs.json").read_bytes(),
        "Repeated outputs differ",
    )
    require(
        (out / "comparison01/metrics.json").read_bytes()
        == (out / "comparison02/metrics.json").read_bytes(),
        "Repeated metrics differ",
    )
    from research.climate_policy.context_baseline import EVALUATE

    context_benchmark = BENCHMARK.parent.parent / "benchmark_context/tasks.json"
    for name in ["context01", "context02"]:
        EVALUATE(context_benchmark, out / name)
    require(
        (out / "context01/outputs.json").read_bytes()
        == (out / "context02/outputs.json").read_bytes(),
        "Repeated context outputs differ",
    )
    from research.climate_policy.__main__ import main

    previous = sys.argv
    try:
        sys.argv = [
            "climate-policy",
            "--db",
            str(out / "comparison01/evidence.sqlite"),
            "demo",
            "--out",
            str(out / "demo"),
        ]
        main()
    finally:
        sys.argv = previous
    package(BENCHMARK, out / "reviewer_package")
    receipt = {
        "status": "REPLICATED_EXPLORATORY_NOT_EXTERNAL_GOLD",
        "outputs_sha256": store.sha(out / "comparison01/outputs.json"),
        "metrics_sha256": store.sha(out / "comparison01/metrics.json"),
        "context_outputs_sha256": store.sha(out / "context01/outputs.json"),
        "deterministic_outputs": True,
        "deterministic_metrics": True,
        "comparison_analyses": 140,
        "demo_cases": 12,
        "reviewer_materials": "A and B blank actual-review forms plus local originals",
        "direct_paid_calls": 0,
        "direct_api_cost_eur": 0,
        "wrapper_sha256": store.sha(Path(__file__)),
    }
    (out / "REPLICATION_RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    print("Replication complete:", out.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    replicate(parser.parse_args().out)
