"""Reproduce selected IDs from saved inventory; no original download or inference."""

import argparse
import json
import sqlite3
from pathlib import Path

from .inventory import digest, select


def reproduce(folder, ledger):
    folder = Path(folder)
    report = json.loads((folder / "REPORT.json").read_text())
    import hashlib

    for name, h in report["files"].items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != h:
            raise ValueError("Inventory input integrity mismatch")
    rows = [json.loads(x) for x in (folder / "inventory.jsonl").read_text().splitlines()]
    exposure = set(
        json.loads((folder / "EXPOSURE_AUDIT.json").read_text())["additional_exposed_sources"]
    )
    for row in rows:
        if row["sha256"] in exposure:
            row["other_development_exposure"] = "KNOWN_DEVELOPMENT_EXPOSED"
    c = sqlite3.connect(Path(ledger).resolve().as_uri() + "?mode=ro", uri=True)
    frame = {
        ags: json.loads(record)
        for ags, record in c.execute("SELECT ags,record FROM municipalities")
    }
    c.close()
    selected = select(rows, frame, report.get("deduplicate_same_purpose_focus", False))
    ids = []
    for r in selected:
        focus = r["family_focus"].get(r["primary_purpose"]) or r["focus"]
        ids.append(
            "bavaria400:" + digest([r["sha256"], r["primary_purpose"], focus["unit_id"]])[:24]
        )
    expected = [t["task_id"] for t in json.loads((folder / "cases.json").read_text())]
    if ids != expected:
        raise ValueError("Selection not reproducible")
    return {
        "status": "IDENTICAL",
        "cases": len(ids),
        "municipalities": len({r["ags"] for r in selected}),
        "selected_originals": len({r["sha256"] for r in selected}),
        "network_calls": 0,
        "model_calls": 0,
        "additional_known_exposure_excluded": sum(r["sha256"] in exposure for r in rows),
        "human_gold": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--study", required=True)
    p.add_argument("--ledger", required=True)
    a = p.parse_args()
    print(json.dumps(reproduce(a.study, a.ledger), indent=2))
