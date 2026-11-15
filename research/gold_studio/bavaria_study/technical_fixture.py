"""Isolated synthetic capacity fixture. NEVER use its judgments for research."""

import argparse
import json
from pathlib import Path
from typing import Any

from ..portable import export_package, import_result, source_assets
from ..prepare import PACKET
from ..store import Store


def run(folder, action):
    folder = Path(folder)
    folder.mkdir(exist_ok=True)
    path = folder / "bundle.json"
    if action == "prepare":
        if path.exists():
            raise ValueError("Fresh technical workspace required")
        sources = json.loads((PACKET / "source_manifest.json").read_text())
        sources = [
            min(
                (s for s in sources if s["file"].endswith(ext)),
                key=lambda s: (PACKET / s["file"]).stat().st_size,
            )
            for ext in [".html", ".pdf"]
        ]
        tasks: list[dict[str, Any]] = []
        for i in range(40):
            stage = ["source", "fields", "retrieval"][i % 3]
            source = sources[i % 2]
            tasks.append(
                {
                    "task_id": f"TECHNICAL_ONLY_{i:02}",
                    "question": f"SYNTHETIC SOFTWARE TEST {i:02}: no human research case",
                    "effective_split": "DEVELOPMENT_EXPOSED",
                    "required_stages": [stage],
                    "cohort": "TECHNICAL_SIMULATION",
                    "allowed_original_sha256": [source["sha256"]],
                    "pool": [],
                    "components": [str(i)],
                }
            )
        b = {
            "selection": {"calibration": [t["task_id"] for t in tasks], "main": []},
            "tasks": tasks,
            "sources": sources,
            "answers": [],
        }
        assets = source_assets(b, folder / "source-cache")
        for task in tasks:
            task["focus"] = {
                "text": assets[task["allowed_original_sha256"][0]]["pages"]["1"]["text"][:160],
                "page_ref": 1,
                "heading": "TECHNICAL FOCUS ONLY",
            }
        path.write_text(json.dumps(b))
    b = json.loads(path.read_text())
    store = Store(folder / "annotations.sqlite", b, True)
    if action == "prepare":
        for role in ["A", "B"]:
            export_package(store, role, folder / (role + ".zip"), folder / "source-cache")
    elif action in ["A", "B", "C"]:
        receipt = import_result(store, json.loads((folder / (action + "-return.json")).read_text()))
        print(json.dumps(receipt))
        if action == "B":
            assert store.locked("A") and store.locked("B")
            export_package(store, "C", folder / "C.zip", folder / "source-cache")
        if action == "C":
            assert all(x["resolution"] for x in store.conflicts())
            assert store.report()["simulation"] and store.report()["gold_status"] == "NOT_RELEASED"
            (folder / "ACCEPTANCE.json").write_text(
                json.dumps(
                    {
                        "simulation": True,
                        "human_gold": False,
                        "cases": 40,
                        "paired_reviews": 80,
                        "conflicts": len(store.conflicts()),
                        "remaining_conflicts": 0,
                        "audit_head": store.audit(),
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder")
    p.add_argument("action")
    a = p.parse_args()
    run(a.folder, a.action)
