"""Convert snapshot-bound retrieval JSON into an UNVERIFIED evidence review dossier.

This is NOT an automated fact classifier. It preserves retrieved chunks and
provenance for independent checking and manual/validated structured extraction.
The downstream decision gate will return UNKNOWN until they are verified.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any


def prepare(retrieval: dict[str, Any] | list[dict[str, Any]], question_id: str,
            municipality_id: str, policy_id: str, as_of: str) -> dict[str, Any]:
    if not (len(municipality_id) == 8 and municipality_id.isdigit()):
        raise ValueError("municipality_id must be an 8-digit AGS")
    date.fromisoformat(as_of)
    if not (question_id.strip() and policy_id.strip()):
        raise ValueError("question_id and policy_id are required")
    if isinstance(retrieval, list):
        items = retrieval
    elif isinstance(retrieval, dict):
        items = retrieval.get("items", retrieval.get("evidence"))
    else:
        raise ValueError("retrieval JSON must be a list or an object containing items/evidence")
    if not isinstance(items, list):
        raise ValueError("retrieval JSON contains no items array: inspect canonical retrieval output")
    evidence, excluded = [], []
    used_ids: set[str] = set()
    for index, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"item {index} is not an object")
        doc = entry.get("document", {})
        if not isinstance(doc, dict):
            raise ValueError("document must be an object")
        ags = doc.get("municipality_ids", entry.get("municipality_ids", []))
        if isinstance(ags, str):
            ags = [ags]
        if not isinstance(ags, list) or not all(isinstance(x, str) for x in ags):
            raise ValueError("document.municipality_ids must be a list of AGS values")
        # Not just a stylistic label: do not permit cross-city evidence into the review packet.
        evidence_id = str(entry.get("evidence_id", entry.get("source_id", f"retrieval_{index}")))
        if not evidence_id or evidence_id in used_ids:
            raise ValueError(f"missing/duplicate evidence_id: {evidence_id!r}")
        used_ids.add(evidence_id)
        if municipality_id not in ags:
            excluded.append({"evidence_id": evidence_id, "reason": "out_of_scope_or_untagged"})
            continue
        text = entry.get("evidence_text", entry.get("text", ""))
        url = doc.get("source_url", entry.get("source_url", ""))
        if not isinstance(text, str) or not text.strip() or not isinstance(url, str) or not url:
            excluded.append({"evidence_id": evidence_id, "reason": "missing_text_or_url"})
            continue
        evidence.append({
            "evidence_id": evidence_id,
            "municipality_id": municipality_id,
            "policy_id": policy_id,
            "issuer_ags": None,
            "authority": "unknown",
            "claim": "mentions",
            "source_url": url,
            "document_text": text,
            "quote": text[:200].strip(),
            "published_at": None,
            "effective_from": None,
            "effective_until": None,
            "verified": False,
        })
    return {
        "question": {"question_id": question_id, "municipality_id": municipality_id,
                     "policy_id": policy_id, "as_of": as_of},
        "evidence": evidence,
        "review_metadata": {
            "requires_independent_verification": True,
            "excluded_retrieval_items": excluded,
            "instructions": (
                "Verify source identity, municipality issuer, exact policy and status, "
                "full quote, publication/effective dates and source URL independently. "
                "Do not set verified=true solely from an LLM guess. "
                "Delete unsupported claims; do not infer NO from absent snippets."
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Create an unverified decision review packet from retrieval results")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--question-id", required=True)
    ap.add_argument("--municipality-id", required=True)
    ap.add_argument("--policy-id", required=True)
    ap.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    a = ap.parse_args()
    retrieval = json.loads(a.input.read_text(encoding="utf-8"))
    dossier = prepare(retrieval, a.question_id, a.municipality_id, a.policy_id, a.as_of)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(dossier, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Wrote {len(dossier['evidence'])} unverified chunks to {a.output}")


if __name__ == "__main__":
    main()
