"""All archived source associations and actual ledger retrieval times; no new fetches."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from research.bavaria.federation import source_connection

from .inventory import gate


def export(study, manifest, output):
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Choose a new provenance export")
    gate(output.parent)
    m = json.loads(Path(manifest).read_text())
    paths = [m["artifacts"]["ledger"]["path"], *m["source_ledger_order"]]
    c = source_connection(paths[0], paths[1:])
    inventory = {
        r["sha256"]: r
        for r in map(json.loads, (study / "inventory.jsonl").read_text().splitlines())
    }
    selected = {
        t["source_record"]["sha256"] for t in json.loads((study / "cases.json").read_text())
    }
    times: dict[str, list[str]] = defaultdict(list)
    try:
        for i in range(len(paths)):
            for h, first, last in c.execute(
                f"SELECT sha256,MIN(at),MAX(at) FROM s{i}.requests "
                "WHERE sha256 IS NOT NULL GROUP BY sha256"
            ):
                times[h].extend(x for x in [first, last] if x)
        with output.open("x") as f:
            fields = [
                "sha256",
                "ags",
                "url",
                "recorded_authority_not_qualified",
                "first_recorded_retrieval",
                "last_recorded_retrieval",
                "publication_date",
                "archive_entry_present",
                "original_hash_verified_in_screen",
                "text_units",
                "inventory_status",
                "selected_for_review",
                "claim_scope",
            ]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            count = 0
            for a in c.execute("SELECT * FROM associations ORDER BY sha256,ags,url"):
                r = inventory[a["sha256"]]
                at = times[a["sha256"]]
                w.writerow(
                    {
                        "sha256": a["sha256"],
                        "ags": a["ags"],
                        "url": a["url"],
                        "recorded_authority_not_qualified": a["authority"],
                        "first_recorded_retrieval": min(at) if at else "UNKNOWN",
                        "last_recorded_retrieval": max(at) if at else "UNKNOWN",
                        "publication_date": "HUMAN_REVIEW_REQUIRED",
                        "archive_entry_present": Path(r["raw_path"]).exists(),
                        "original_hash_verified_in_screen": r["original_sha256_verified"],
                        "text_units": r["text_units"],
                        "inventory_status": r["status"],
                        "selected_for_review": a["sha256"] in selected,
                        "claim_scope": "UNKNOWN_DO_NOT_INHERIT_HOST_OR_FRAME",
                    }
                )
                count += 1
        return {
            "associations": count,
            "unique_originals": len(inventory),
            "new_requests": 0,
            "publication_time_is_not_retrieval_time": True,
        }
    finally:
        c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["study", "manifest", "output"]:
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    print(json.dumps(export(a.study, a.manifest, a.output), indent=2))
