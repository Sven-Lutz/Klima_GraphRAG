"""Technical shadow bindings for actual delivery ZIPs; production imports cannot match them."""

import argparse
import json
from pathlib import Path

from ..portable import export_package, import_result
from ..prepare import ROOT
from ..store import Store
from .bundle import load_bundle


def run(folder, action, cache=None):
    folder = Path(folder)
    folder.mkdir(exist_ok=True)
    store = Store(
        folder / "annotations.sqlite",
        load_bundle(ROOT / "generated/gold-studio-bavaria-candidates-v2"),
        True,
    )
    if action == "issue":
        data = json.loads((folder / "shadow-bindings.json").read_text())
        for binding in data:
            if not binding["simulation"] or not binding["package_id"].startswith("TECH_SHADOW_"):
                raise ValueError("Technical binding only")
            if not any(
                e["payload"]["binding"] == binding for e in store.events("C", "portable_issue")
            ):
                with store.conn() as c:
                    store.event(c, "C", "", "portable", "portable_issue", {"binding": binding})
    elif action in ["A", "B", "C"]:
        result = import_result(store, json.loads((folder / (action + "-return.json")).read_text()))
        assert result["missing"] == 0
        print(json.dumps(result))
        if action == "B" and not (folder / "C.zip").exists():
            export_package(
                store, "C", folder / "C.zip", cache or ROOT / "generated/portable-source-cache-v1"
            )
        if action == "C":
            assert all(x["resolution"] for x in store.conflicts())
            assert not store.profile("A")["human"] and not store.profile("B")["human"]
            (folder / "RESULT.json").write_text(
                json.dumps(
                    {
                        "simulation": True,
                        "human_gold": False,
                        "calibration_cases": 12,
                        "originals": 35,
                        "forms_per_reviewer": 48,
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
    p.add_argument("--cache", type=Path)
    a = p.parse_args()
    run(a.folder, a.action, a.cache)
