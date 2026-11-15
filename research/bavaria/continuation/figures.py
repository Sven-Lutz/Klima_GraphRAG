"""Render frozen exploratory comparisons; never recompute labels or model outputs."""

import argparse
import json
from pathlib import Path


def render(release: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ie = json.loads((release / "ie/comparison02/report.json").read_text())
    # The report's evaluation_components table is the frozen source-component split.
    evaluation = ie["evaluation_components"]
    print("evaluation keys", list(evaluation))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), layout="constrained")
    labels = ["Rules", "Qwen3 0.6b", "Validated hybrid"]
    values = [evaluation[m]["micro"]["f1"] for m in ("rules", "model", "hybrid")]
    axes[0].bar(labels, values, color=["#2166ac", "#b2182b", "#4393c3"])
    axes[0].set(
        ylim=(0, 1),
        ylabel="Strict micro field F1",
        title="A  IE: 22 evaluation cases / 97 positive slots",
    )
    for i, value in enumerate(values):
        axes[0].text(i, value + 0.025, f"{value:.3f}", ha="center")
    report = json.loads((release / "relational/comparison02/report.json").read_text())
    summary = report["summary_evaluation_provisional_positive"]
    methods = ["bm25_context", "semantic", "hybrid_rrf", "sql_relations", "graph_traversal"]
    counts = [summary[m]["complete_known_bundles"] for m in methods]
    axes[1].barh(
        ["BM25 + context", "Semantic", "Hybrid RRF", "SQL + relations", "Graph + relations"],
        counts,
        color="#2166ac",
    )
    axes[1].set(
        xlim=(0, 15),
        xlabel="Complete known evidence bundles / 14 tasks",
        title="B  Curated relation information supplied",
    )
    axes[1].invert_yaxis()
    for i, value in enumerate(counts):
        axes[1].text(value + 0.15, i, str(value), va="center")
    fig.suptitle("Exploratory agent references; zero independent human labels", fontsize=12)
    for suffix in ["svg", "png"]:
        fig.savefig(release / f"comparison.{suffix}", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    render(parser.parse_args().release)
