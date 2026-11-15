# ruff: noqa: E501
"""Post-hoc diagnostic of JSON null versus explicit UNKNOWN; not confirmatory rescue."""

import hashlib
import json
import shutil
import time
from pathlib import Path

import requests

from research.bavaria.continuation.ie_compare import FIELDS, SYSTEM, schema, summary


def run():
    root = Path("generated/bavaria/completion")
    out = root / "unknown-ablation01"
    if out.exists():
        raise ValueError("Do not repeat completed model calls")
    cases = [
        json.loads(line)
        for line in (root / "model-qualification/cases.jsonl").read_text().splitlines()
    ]
    selected = [c for c in cases if c["case_id"] in ["S09", "S17", "S24", "S05"]]
    definition = schema(cases)
    for field in FIELDS:
        definition["properties"][field]["type"] = "string"
        if "enum" in definition["properties"][field]:
            definition["properties"][field]["enum"] = [
                "UNKNOWN" if x is None else x for x in definition["properties"][field]["enum"]
            ]
    system = SYSTEM.replace("null", '"UNKNOWN"')
    out.mkdir()
    (out / "FREEZE.json").write_text(
        json.dumps(
            {
                "status": "FROZEN_BEFORE_ABLATION_CALLS_POSTHOC_DEVELOPMENT",
                "hypothesis": "Nullable-schema unknown failure may partly depend on null representation. Explicit string sentinel changes no target semantics.",
                "cases": [c["case_id"] for c in selected],
                "schema": definition,
                "system": system,
                "max_calls": 4,
                "wall_seconds": 300,
                "model": "qwen3:1.7b",
                "options": {
                    "temperature": 0,
                    "seed": 42,
                    "num_ctx": 2048,
                    "num_predict": 384,
                    "num_thread": 1,
                },
                "input_sha256": hashlib.sha256(
                    (root / "model-qualification/cases.jsonl").read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    rows = []
    started = time.monotonic()
    for c in selected:
        if (
            time.monotonic() - started > 300
            or shutil.disk_usage(root).free < 15 * 1024**3 + 128 * 1024**2
        ):
            raise RuntimeError("Resource gate")
        payload = {
            "model": "qwen3:1.7b",
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": "Fokus: "
                    + c["focus"]
                    + "\nQuelltext:\n"
                    + c["heading"]
                    + "\n"
                    + c["text"],
                },
            ],
            "format": definition,
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_ctx": 2048,
                "num_predict": 384,
                "num_thread": 1,
            },
        }
        response = requests.post("http://127.0.0.1:11435/api/chat", json=payload, timeout=90)
        response.raise_for_status()
        raw = response.json()
        with (out / "responses.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    {"case_id": c["case_id"], "request": payload, "response": raw},
                    ensure_ascii=False,
                )
                + "\n"
            )
        output = json.loads(raw["message"]["content"])
        for field in FIELDS:
            if output[field] == "UNKNOWN":
                output[field] = None
        rows.append(
            {
                "case_id": c["case_id"],
                "component": c["component"],
                "split": c["split"],
                "method": "explicit_unknown",
                "reference": c["provisional_expected"],
                "output": output,
            }
        )
        print(c["case_id"], "completed", flush=True)
    report = {
        "status": "POSTHOC_DIAGNOSTIC_NOT_INDEPENDENT_VALIDATION",
        "cases": len(rows),
        "metrics": summary(rows),
        "seconds": time.monotonic() - started,
    }
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    )
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    run()
