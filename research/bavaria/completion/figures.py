import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

p = Path("research/bavaria/completion/release")
d = p / "figures"
d.mkdir(exist_ok=True)
plt.rcParams.update(
    {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"}
)
rows = [
    r
    for r in csv.DictReader((p / "analysis01/retrieval_summary.csv").open())
    if r["subset"] == "frozen_evaluation_positive"
]
sel = [
    "bm25_context",
    "semantic",
    "semantic_windows",
    "hybrid_rrf",
    "bm25_same_relation_information",
    "sql_relations",
    "graph_traversal",
]
rows = [next(r for r in rows if r["method"] == m) for m in sel]
fig, ax = plt.subplots(figsize=(9, 4.8))
ax.barh(
    [r["method"] for r in rows],
    [int(r["known_bundles"]) for r in rows],
    color=["#246b83"] * 4 + ["#c17e21"] * 3,
)
ax.set_xlim(0, 30)
ax.set_xlabel("Complete known bundles / 28 positive exposed tasks")
ax.invert_yaxis()
ax.set_title("Provisional references; curated-information arms in ochre")
for i, r in enumerate(rows):
    ax.text(int(r["known_bundles"]) + 0.2, i, r["known_bundles"], va="center")
fig.tight_layout()
fig.savefig(d / "retrieval.svg")
fig.savefig(d / "retrieval.png", dpi=140)
plt.close(fig)
fig, axs = plt.subplots(1, 2, figsize=(9, 3.5))
years = [2020, 2021, 2022]
axs[0].plot(years, [12.7, 15.3, 13.2], marker="o")
axs[0].set_ylabel("Municipal-property heat (GWh)")
axs[1].plot(years, [418000, 455000, 427000], marker="o", color="#c17e21")
axs[1].set_ylabel("Territorial energy GHG (t THG)")
for ax in axs:
    ax.set_xticks(years)
    ax.set_xlabel("Report reference year")
    ax.grid(alpha=0.2)
fig.suptitle("Amberg: as-published inventories; different scopes; no causal attribution")
fig.tight_layout()
fig.savefig(d / "temporal.svg")
fig.savefig(d / "temporal.png", dpi=140)
plt.close(fig)
fig, ax = plt.subplots(figsize=(7, 3.8))
labels = ["Rules", "Qwen3 1.7B", "Hybrid 1.7B", "Qwen2.5 3B", "Hybrid 3B"]
vals = [0.133333, 0.385542, 0.270270, 0.333333, 0.133333]
ax.bar(labels, vals, color="#246b83")
ax.axhline(
    0.5,
    color="#b44037",
    linestyle="--",
    label="Pre-output F1 gate (also requires unknown/role gates)",
)
ax.set_ylim(0, 0.65)
ax.set_ylabel("Strict field F1")
ax.set_title("Same eight original-source qualification cases; provisional")
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(d / "ie.svg")
fig.savefig(d / "ie.png", dpi=140)
print("6 small scientific figure assets written")

# Normalize Matplotlib path formatting for repository whitespace checks.
for svg in d.glob("*.svg"):
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
