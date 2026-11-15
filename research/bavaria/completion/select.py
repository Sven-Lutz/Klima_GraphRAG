# ruff: noqa: E501
"""Freeze a new stratified original sample excluding all historical review municipalities."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

SEED = "completion-20261108-v1"


def select(db, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Frozen selection exists")
    excluded = {"09163000", "09263000", "09471122", "09780147"}
    # Read identifiers only, never confirmation questions, answers or method outputs.
    paths = [
        Path("research/bavaria/release/reviewer-packet/A/tasks.jsonl"),
        Path("research/bavaria/continuation/release/ie/sample-v2/cases.jsonl"),
    ]
    for p in paths:
        for line in p.read_text().splitlines():
            r = json.loads(line)
            excluded.update(r.get("source_frame_ags", []))
            excluded.update(
                r.get("ags", [])
                if isinstance(r.get("ags"), list)
                else [r["ags"]]
                if r.get("ags")
                else []
            )
    prior = json.loads(Path("research/bavaria/qualification/results/selection.json").read_text())
    excluded.update(r["ags"] for r in prior["selection"])
    relations = Path("research/bavaria/continuation/release/relational/dataset/relations.jsonl")
    for line in relations.read_text().splitlines():
        excluded.update(json.loads(line)["claim_scope_ags"])
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    records = {
        r["ags"]: json.loads(r["record"]) for r in conn.execute("SELECT * FROM municipalities")
    }
    pdf = {
        r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM units WHERE page_ref IS NOT NULL")
    }
    cues = re.compile(
        r"beauftragt|beschloss|Gemeinderat|Stadtwerke|Förderprogramm|Klimaschutzkonzept|installiert|fertiggestellt",
        re.I,
    )
    eligible_sources = {
        r["sha256"]
        for r in conn.execute("SELECT sha256,text FROM units")
        if len(r["text"]) >= 150 and cues.search(r["text"])
    }
    candidates = [
        dict(r)
        for r in conn.execute(
            "SELECT a.*,d.raw_path,d.raw_bytes FROM associations a JOIN documents d USING(sha256)"
        )
        if r["ags"] not in excluded and r["sha256"] in eligible_sources
    ]
    selected, seen_ags, seen_sha, counts = [], set(), set(), {}
    for region in sorted({r["region"] for r in records.values()}):
        for stratum in ("HTML_small", "HTML_medium", "HTML_large", "PDF"):
            eligible = []
            for row in candidates:
                r = records[row["ags"]]
                size = (
                    "small"
                    if r["population"] < 5000
                    else "medium"
                    if r["population"] < 20000
                    else "large"
                )
                if r["region"] != region or row["ags"] in seen_ags or row["sha256"] in seen_sha:
                    continue
                if (
                    row["sha256"] in pdf
                    if stratum == "PDF"
                    else row["sha256"] not in pdf and stratum == "HTML_" + size
                ):
                    eligible.append(row)
            counts[region + "/" + stratum] = len(eligible)
            if not eligible:
                continue
            chosen = min(
                eligible,
                key=lambda x: hashlib.sha256((SEED + x["ags"] + x["sha256"]).encode()).hexdigest(),
            )
            selected.append(
                {
                    **chosen,
                    "region": region,
                    "stratum": stratum,
                    "name": records[chosen["ags"]]["name"],
                    "population": records[chosen["ags"]]["population"],
                    "id": f"S{len(selected) + 1:02}",
                }
            )
            seen_ags.add(chosen["ags"])
            seen_sha.add(chosen["sha256"])
    conn.close()
    result = {
        "status": "FROZEN_BEFORE_ORIGINAL_REVIEW",
        "seed": SEED,
        "database": str(Path(db).resolve()),
        "database_sha256": hashlib.sha256(Path(db).read_bytes()).hexdigest(),
        "excluded_ags": sorted(excluded),
        "exclusion": "All original 200-review municipalities, prior quality sample, IE and relation scope groups; metadata only.",
        "design": "Four strata per seven regions, deterministic hash selection among previously unreviewed relation-cue candidates. Enriched quota sample, no population precision estimate.",
        "eligible_counts": counts,
        "selection": selected,
        "independent_human_gold": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("Frozen sources", len(selected), "excluded municipalities", len(excluded))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    select(a.db, a.output)
