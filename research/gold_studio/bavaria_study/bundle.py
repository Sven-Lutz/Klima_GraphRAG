"""Adapter from prospective candidates into canonical Studio forms, not another store."""

import gzip
import hashlib
import json
import shutil
from pathlib import Path

from ..prepare import PACKET, ROOT, prepare
from ..store import RESERVE, digest


def source_path(record):
    if "raw_path" not in record:
        path = (PACKET / record["file"]).resolve(strict=True)
        if PACKET.resolve() not in path.parents:
            raise ValueError("Source escapes historical packet")
    else:
        raw = Path(record["raw_path"]).resolve(strict=True)
        roots = [
            ROOT / "generated/bavaria",
            ROOT / "research/experiments/e2_v11_acquisition_20261024/export/raw",
        ]
        if not any(root.resolve() in raw.parents for root in roots):
            raise ValueError("Source outside registered corpus")
        # Historical acquisition keeps uncompressed originals. Reuse verified bytes
        # directly rather than duplicating them into a generated cache.
        if raw.suffix != ".gz":
            if (
                raw.stat().st_size != record["raw_bytes"]
                or hashlib.sha256(raw.read_bytes()).hexdigest() != record["sha256"]
            ):
                raise ValueError("Archived original mismatch")
            return raw
        cache = ROOT / "generated/gold-studio-original-cache"
        path = cache / Path(record["file"]).name
        if not path.exists():
            if (
                shutil.disk_usage(ROOT).free
                < max(RESERVE, 16 * 1024**3) + record["raw_bytes"] + 32 * 1024**2
            ):
                raise ValueError("Phase C: 16-GiB write floor prevents original materialization")
            cache.mkdir(exist_ok=True)
            body = gzip.decompress(raw.read_bytes()) if raw.suffix == ".gz" else raw.read_bytes()
            if hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise ValueError("Archived original mismatch")
            with path.open("xb") as f:
                f.write(body)
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Original integrity failure")
    return path


def load_bundle(study):
    folder = Path(study)
    report = json.loads((folder / "REPORT.json").read_text())
    for name, expected in report["files"].items():
        path = (folder / name).resolve(strict=True)
        if (
            folder.resolve() not in path.parents
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Candidate study identity mismatch")
    cases = json.loads((folder / "cases.json").read_text())
    old = prepare()
    calibration = old["selection"]["calibration"]
    tasks = [t for t in old["tasks"] if t["task_id"] in calibration]
    for task in tasks:
        task["quality_review"] = True
        if task.get("pool"):
            candidate = task["pool"][0]
            task["focus"] = {
                "text": candidate["text"],
                "page_ref": candidate.get("page"),
                "heading": candidate.get("heading"),
            }
    sources = {
        s["sha256"]: s
        for s in old["sources"]
        if any(s["sha256"] in t["allowed_original_sha256"] for t in tasks)
    }
    for case in cases:
        task = {k: v for k, v in case.items() if k != "source_record"}
        task["cohort"] = "bavaria_prospective_v1"
        tasks.append(task)
        r = case["source_record"]
        sources[r["sha256"]] = {
            "sha256": r["sha256"],
            "raw_path": r["raw_path"],
            "raw_bytes": r["raw_bytes"],
            "file": "originals/" + r["sha256"] + (".pdf" if r["format"] == "PDF" else ".html"),
            "url": r["url"],
            "frame_ags": r["all_frame_ags"],
            "claim_scope": "REVIEW_REQUIRED_DO_NOT_INHERIT_FRAME",
        }
    return {
        "snapshot_id": report["snapshot_id"],
        "selection": {
            **old["selection"],
            "schema": "research-gold-studio-bavaria-selection-v2",
            "review_contract": "focused-primary-and-source-quality-v2",
            "review_content_sha256": digest({"tasks": tasks, "answers": old["answers"]}),
            "main": [t["task_id"] for t in cases],
            "main_notes": "Prospective candidates; historical 70/130 unchanged and excluded.",
            "study_report_sha256": hashlib.sha256(
                (folder / "REPORT.json").read_bytes()
            ).hexdigest(),
            "release_status": "PENDING_HUMAN_CALIBRATION_DEPENDENCY_CLEARANCE_AND_RIGHTS",
        },
        "batches": json.loads((folder / "batches.json").read_text()),
        "tasks": tasks,
        "sources": list(sources.values()),
        "answers": old["answers"],
        "admin_pool_map": [],
    }
