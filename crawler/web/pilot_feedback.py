"""Validate manually returned pilot feedback; never imports into Human Gold."""

import argparse
import hashlib
import json
from pathlib import Path

CATEGORIES = {"bug", "ux", "source", "fact", "feature", "science"}


def validate(path):
    path = Path(path)
    if not 0 < path.stat().st_size <= 64 * 1024:
        raise ValueError("Feedback size outside 64 KiB budget")
    raw = path.read_bytes()
    data = json.loads(raw)
    allowed = {
        "schema",
        "technical_test",
        "category",
        "message",
        "created_at",
        "software",
        "snapshot_id",
        "projection_id",
        "context_included",
        "context",
        "transfer",
        "research_gold",
    }
    if set(data) - allowed:
        raise ValueError("Unexpected feedback fields")
    if (
        data.get("schema") != "klima-pilot-feedback-v1"
        or data.get("category") not in CATEGORIES
        or data.get("research_gold") is not False
        or data.get("transfer") != "MANUAL_ONLY"
        or not isinstance(data.get("message"), str)
        or not data["message"].strip()
        or len(data["message"]) > 4000
        or not isinstance(data.get("context_included"), bool)
    ):
        raise ValueError("Invalid feedback schema")
    if not data["context_included"] and data.get("context") is not None:
        raise ValueError("Context present without opt-in")
    if data["context_included"] and not isinstance(data.get("context"), dict):
        raise ValueError("Missing selected context")
    return {
        "schema": "klima-pilot-feedback-receipt-v1",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "category": data["category"],
        "context_included": data["context_included"],
        "research_gold": False,
        "storage": "not_persisted_by_validator",
        "technical_test": data.get("technical_test", False),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file", type=Path)
    a = p.parse_args()
    print(json.dumps(validate(a.file), indent=2))
