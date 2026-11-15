# ruff: noqa: E501
"""Human-adjudication adapter for source-span retrieval metrics and paired uncertainty.

This supplements the frozen offline metric contract for the new source-version
ledger; it never promotes Research Gold or treats blank/agent forms as humans.
"""

import argparse
import hashlib
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from research.bavaria.relational.build import Components

from .review import intake, write


def canonical_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def kappa(pairs):
    if not pairs:
        return {"n": 0, "agreement": None, "cohen_kappa": None}
    a, b = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    n = len(pairs)
    observed = sum(x == y for x, y in pairs) / n
    expected = sum(a[x] * b[x] for x in set(a) | set(b)) / n**2
    return {
        "n": n,
        "agreement": observed,
        "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None,
    }


def ranking_metrics(ranking, groups, k=8):
    ranking = list(dict.fromkeys(ranking))[:k]
    grades = {g["group_id"]: g.get("relevance", 2) for g in groups}
    captured = set()
    dcg = 0.0
    first = None
    relevant_units = set().union(*(set(g["eligible_units"]) for g in groups)) if groups else set()
    for rank, uid in enumerate(ranking, 1):
        matched = [
            g["group_id"]
            for g in groups
            if uid in g["eligible_units"] and g["group_id"] not in captured
        ]
        if matched:
            first = first or rank
            grade = max(grades[x] for x in matched)
            dcg += (2**grade - 1) / math.log2(rank + 1)
            captured.update(matched)
    ideal = sum(
        (2**v - 1) / math.log2(i + 2)
        for i, v in enumerate(sorted(grades.values(), reverse=True)[:k])
    )
    return {
        "precision_at_k": sum(x in relevant_units for x in ranking) / k,
        "precision_among_returned": sum(x in relevant_units for x in ranking) / len(ranking)
        if ranking
        else None,
        "source_span_group_recall": len(captured) / len(groups) if groups else None,
        "mrr": 1 / first if first else 0.0 if groups else None,
        "group_ndcg": dcg / ideal if ideal else None,
        "complete_known_bundle": float(len(captured) == len(groups)) if groups else None,
        "reference_groups": len(groups),
        "retrieved_units": len(ranking),
        "reference_groups_without_parsed_representation": sum(
            not g["eligible_units"] for g in groups
        ),
    }


def paired_bootstrap(rows, baseline, candidate, metric="complete_known_bundle", repetitions=2000):
    cells = {(r["task_id"], r["method"]): r for r in rows}
    tasks = sorted(
        t
        for t, m in cells
        if m == baseline
        and (t, candidate) in cells
        and cells[t, baseline].get(metric) is not None
        and cells[t, candidate].get(metric) is not None
    )
    if not tasks:
        return {"pairs": 0, "interval": None}
    components = Components()
    for t in tasks:
        components.union(
            "task:" + t, *["cluster:" + x for x in cells[t, baseline]["component_ids"]]
        )
    clusters = defaultdict(list)
    for t in tasks:
        clusters[components.find("task:" + t)].append(t)
    blocks = list(clusters.values())
    rng = random.Random(42)
    values = []
    for _ in range(repetitions):
        sample = [t for _ in blocks for t in rng.choice(blocks)]
        values.append(
            sum(cells[t, candidate][metric] - cells[t, baseline][metric] for t in sample)
            / len(sample)
        )
    values.sort()
    point = sum(cells[t, candidate][metric] - cells[t, baseline][metric] for t in tasks) / len(
        tasks
    )
    return {
        "pairs": len(tasks),
        "components": len(blocks),
        "difference": point,
        "percentile_95_interval": [
            values[int(0.025 * repetitions)],
            values[min(repetitions - 1, int(0.975 * repetitions))],
        ],
        "seed": 42,
        "resamples": repetitions,
        "warning": "Conditional cluster bootstrap only; few clusters and unknown dependencies preclude population inference.",
    }


def evaluate(packet, db, runs, output):
    packet = Path(packet)
    status = intake(packet)
    if not all(x["completed"] == x["expected"] for x in status["forms"].values()):
        write(
            output,
            {
                "status": status,
                "metrics": None,
                "reason": "Independent human references absent; no run file read or held-out metric computed.",
            },
        )
        return
    reviewer_a = {
        x["task_id"]: x
        for x in [
            json.loads(line) for line in (packet / "A/annotations.jsonl").read_text().splitlines()
        ]
    }
    reviewer_b = {
        x["task_id"]: x
        for x in [
            json.loads(line) for line in (packet / "B/annotations.jsonl").read_text().splitlines()
        ]
    }
    judgments = [
        json.loads(line) for line in (packet / "adjudication.jsonl").read_text().splitlines()
    ]
    tasks = {x["task_id"]: x for x in json.loads((packet / "questions.json").read_text())}
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    evidence_query = (
        "SELECT evidence_id AS unit_id,text,page_ref FROM evidence WHERE source_sha256=?"
        if "evidence" in tables
        else "SELECT unit_id,text,page_ref FROM units WHERE sha256=?"
    )
    accepted = {}
    pairs = []
    for j in judgments:
        if j["final_reference"] is None:
            continue
        tid = j["task_id"]
        a, b = reviewer_a[tid], reviewer_b[tid]
        if not (a["reviewer_id"] and b["reviewer_id"] and j["adjudicator_id"]):
            raise ValueError("Missing actual reviewers/adjudicator")
        if j["adjudicator_id"] in [a["reviewer_id"], b["reviewer_id"]]:
            raise ValueError("Adjudicator must be distinct")
        if j["reviewer_A_record_sha256"] != canonical_hash(a) or j[
            "reviewer_B_record_sha256"
        ] != canonical_hash(b):
            raise ValueError("Unbound reviewer judgment")
        if not j["dependency_clearance"] or not j["rationale"]:
            raise ValueError("Keep unresolved dependencies in quarantine")
        ref = j["final_reference"]
        if not ref.get("source_scope_review_complete"):
            raise ValueError(
                "Incomplete relevance inventory cannot score full retrieval precision/recall"
            )
        groups = []
        for index, span in enumerate(ref["evidence_spans"]):
            sha = span["source_sha256"]
            if sha not in tasks[tid]["allowed_original_sha256"]:
                raise ValueError("Wrong original scope")
            quote = " ".join(span["verbatim_quote"].split())
            # The source-only packet contains the exact originals. Verify the supplied span
            # before mapping to pipeline units; do not silently drop original-only evidence.
            original = next(
                (
                    packet / x["file"]
                    for x in json.loads((packet / "source_manifest.json").read_text())
                    if x["sha256"] == sha
                ),
                None,
            )
            if original is None or hashlib.sha256(original.read_bytes()).hexdigest() != sha:
                raise ValueError("Original integrity")
            if not quote:
                raise ValueError("Empty evidence span")
            if original.suffix == ".pdf":
                import subprocess

                pages = (
                    subprocess.run(
                        ["pdftotext", "-layout", str(original), "-"],
                        capture_output=True,
                        check=True,
                    )
                    .stdout.decode()
                    .split("\f")
                )
                visible = pages[span["physical_page"] - 1]
            else:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(original.read_bytes(), "html.parser")
                for n in soup(["script", "style", "noscript"]):
                    n.decompose()
                visible = soup.get_text(" ", strip=True)
            if quote not in " ".join(visible.split()):
                raise ValueError("Reference quote absent from original")
            units = [
                r["unit_id"]
                for r in conn.execute(evidence_query, (sha,))
                if quote in " ".join(r["text"].split())
                and (
                    not span.get("physical_page")
                    or int(r["page_ref"] or 0) == span["physical_page"]
                )
            ]
            if span.get("relevance", 2) <= 0:
                continue
            groups.append(
                {
                    "group_id": span.get("required_bundle", str(index)),
                    "eligible_units": units,
                    "relevance": span.get("relevance", 2),
                }
            )
        # Merge alternative spans within the same required group.
        merged = {}
        for g in groups:
            if g["group_id"] not in merged:
                merged[g["group_id"]] = g
            else:
                merged[g["group_id"]]["eligible_units"] = sorted(
                    set(merged[g["group_id"]]["eligible_units"]) | set(g["eligible_units"])
                )
        if not ref.get("component_ids"):
            raise ValueError("Final dependency component IDs missing")
        accepted[tid] = {
            "groups": list(merged.values()),
            "component_ids": ref["component_ids"],
            "task_class": ref["task_class"],
        }
        pairs.append((a["answerability"], b["answerability"]))
    conn.close()
    run_rows = [json.loads(line) for line in Path(runs).read_text().splitlines()]
    scored = []
    for r in run_rows:
        task_id = r["task_id"]
        if task_id not in accepted and "completion:" + task_id in accepted:
            task_id = "completion:" + task_id
        if task_id not in accepted:
            continue
        ref = accepted[task_id]
        scored.append(
            {
                "task_id": task_id,
                "method": r["method"],
                "component_ids": ref["component_ids"],
                "task_class": ref["task_class"],
                **ranking_metrics(r["selected_evidence"], ref["groups"]),
            }
        )
    methods = sorted({r["method"] for r in scored})
    comparisons = {}
    if methods:
        for candidate in methods[1:]:
            comparisons[methods[0] + " vs " + candidate] = paired_bootstrap(
                scored, methods[0], candidate
            )
    write(
        output,
        {
            "status": "METRICS_ON_DECLARED_HUMAN_REFERENCES_OWNER_SCIENTIFIC_RELEASE_PENDING",
            "accepted_tasks": len(accepted),
            "total_tasks": len(tasks),
            "rows": scored,
            "answerability_IAA": kappa(pairs),
            "paired_comparisons": comparisons,
            "identity_limit": "Code checks declarations and provenance, not actual human independence. No automatic Research Gold release.",
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--packet", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--runs", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    evaluate(a.packet, a.db, a.runs, a.output)


def claim_metrics(judgments):
    """Summarize adjudicated atomic assertions; absent human judgments stay NA.

    This pure helper does not authenticate people, resolve evidence, or release
    Gold. Its caller must first run the packet integrity/adjudication gates.
    Nonassertions, abstentions and unreviewed candidates are separate denominators.
    """
    rows = [r for r in judgments if r.get("assertion_or_question") == "assertion"]
    reviewed = [r for r in rows if isinstance(r.get("supported"), bool)]
    if not reviewed:
        return {
            "reviewed_assertions": 0,
            "claim_support": None,
            "unsupported_claim_rate": None,
            "citation_faithfulness": None,
        }
    citations = [r for r in reviewed if isinstance(r.get("citation_attribution_correct"), bool)]
    return {
        "reviewed_assertions": len(reviewed),
        "unreviewed_assertions": len(rows) - len(reviewed),
        "claim_support": sum(r["supported"] for r in reviewed) / len(reviewed),
        "unsupported_claim_rate": sum(not r["supported"] for r in reviewed) / len(reviewed),
        "citation_faithfulness": sum(r["citation_attribution_correct"] for r in citations)
        / len(citations)
        if citations
        else None,
        "citation_denominator": len(citations),
        "independent_research_gold_released": False,
    }
