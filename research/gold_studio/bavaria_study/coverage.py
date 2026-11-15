"""Metadata coverage for every official AGS, without municipal policy absence claims."""

import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from research.bavaria.federation import source_connection

from .inventory import gate


def write_coverage(study, manifest, output):
    root, output = Path(study), Path(output)
    m = json.loads(Path(manifest).read_text())
    c = source_connection(m["artifacts"]["ledger"]["path"], m["source_ledger_order"])
    try:
        frame = {
            r["ags"]: json.loads(r["record"]) for r in c.execute("select * from municipalities")
        }
        inv = {
            r["sha256"]: r
            for r in map(json.loads, (root / "inventory.jsonl").read_text().splitlines())
        }
        agsdocs = defaultdict(set)
        for a in c.execute("select ags,sha256 from associations"):
            agsdocs[a["ags"]].add(a["sha256"])
        selected = Counter(t["ags"] for t in json.loads((root / "cases.json").read_text()))
        fields = [
            "ags",
            "name",
            "region",
            "district",
            "population",
            "population_date",
            "municipality_type",
            "associated_originals",
            "originals_with_text",
            "screened_candidates",
            "deferred_text_candidates",
            "selected_cases",
            "claim_scope_qualification",
        ]
        text = io.StringIO(newline="")
        w = csv.DictWriter(text, fieldnames=fields)
        w.writeheader()
        for ags, r in sorted(frame.items()):
            docs = agsdocs[ags]
            w.writerow(
                {
                    **{k: r.get(k) for k in fields[:7]},
                    "associated_originals": len(docs),
                    "originals_with_text": sum(inv[h]["text_units"] > 0 for h in docs),
                    "screened_candidates": sum(inv[h]["status"] == "CANDIDATE" for h in docs),
                    "deferred_text_candidates": sum(
                        inv[h]["status"].startswith("DEFERRED_STORAGE") for h in docs
                    ),
                    "selected_cases": selected[ags],
                    "claim_scope_qualification": "HUMAN_PENDING_NO_POLICY_ABSENCE_INFERENCE",
                }
            )
        result = text.getvalue()
        if output.exists():
            if output.read_bytes().decode() != result:
                raise ValueError("Refuse changed coverage; choose a new version")
        else:
            gate(output.parent)
            with output.open("x", newline="") as f:
                f.write(result)
        return {
            "municipalities": len(frame),
            "with_associated_originals": sum(bool(agsdocs[a]) for a in frame),
            "with_text": sum(any(inv[h]["text_units"] for h in agsdocs[a]) for a in frame),
            "units": c.execute("select count(*) from units").fetchone()[0],
        }
    finally:
        c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--study", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    print(json.dumps(write_coverage(a.study, a.manifest, a.output), indent=2))
