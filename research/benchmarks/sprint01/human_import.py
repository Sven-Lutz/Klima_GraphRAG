"""Import the supplied human-review handoff without upgrading its evidence basis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.offline_evaluation.contracts import json_bytes, require

from .review import validate
from .review_log import append_decisions, digest


def prepare(
    handoff: bytes, candidates: Path, expected_reviewer: str, **source_paths
) -> tuple[list[dict], dict]:
    """Validate the complete handoff before any decision is written."""
    package, manifest, _ = validate(candidates, **source_paths)
    supplied = json_bytes(handoff)
    require(
        isinstance(supplied, dict)
        and supplied.get("artifact_type") == "human_review_decisions_transcribed_from_chat",
        "Unsupported human handoff",
    )
    require(supplied.get("human_gold_release") is False, "Handoff cannot authorize gold")
    rows = supplied.get("decisions")
    require(
        isinstance(rows, list) and len(rows) == supplied.get("decision_count"),
        "Decision count mismatch",
    )
    by_id = {item["id"]: item for item in package["candidates"]}
    require(all(isinstance(row, dict) for row in rows), "Decision must be an object")
    require(len({row.get("case_id") for row in rows}) == len(rows), "Duplicate case decision")
    require({row.get("case_id") for row in rows} == set(by_id), "Incomplete candidate coverage")
    access = supplied.get("source_access_summary")
    if access is not None:
        require(isinstance(access, dict), "Malformed source access summary")
        direct_ids = access.get("reviewer_direct_original_opened_cases", [])
        chat_ids = access.get("chat_rendered_source_content_cases", [])
        require(
            isinstance(direct_ids, list) and isinstance(chat_ids, list),
            "Malformed source access case lists",
        )
        require(
            not set(direct_ids) & set(chat_ids) and set(direct_ids) | set(chat_ids) == set(by_id),
            "Source access summary does not partition the reviewed cases",
        )
        require(
            set(direct_ids)
            == {
                row["case_id"]
                for row in rows
                if row.get("original_access_attestation") == "reviewer_yes"
            },
            "Per-case original-access testimony contradicts summary",
        )
    records = []
    for row in rows:
        cid = row["case_id"]
        require(row.get("reviewer_id") == expected_reviewer, "Reviewer mismatch")
        require(
            row.get("candidate_sha256") == manifest["candidate_sha256"][cid],
            "Canonical candidate hash mismatch: " + cid,
        )
        source_hashes = row.get("archived_source_sha256_list")
        if source_hashes is None:
            source_hashes = [row.get("archived_source_sha256")]
        require(
            isinstance(source_hashes, list)
            and len(source_hashes) == len(set(source_hashes))
            and set(source_hashes) == {s["raw_sha256"] for s in by_id[cid]["source_versions"]},
            "Source version mismatch: " + cid,
        )
        require(
            row.get("decision") in {"accept", "revise", "reject", "unsure"},
            "Unsupported human decision",
        )
        require(
            isinstance(row.get("reason"), str) and row["reason"].strip(),
            "Actual human explanation required",
        )
        attestation = row.get("original_access_attestation")
        require(
            attestation
            in {"reviewer_yes", "reviewer_did_not_open_html", "reviewer_did_not_open_originals"},
            "Unknown original-access basis",
        )
        direct = attestation == "reviewer_yes"
        if not direct:
            require(
                isinstance(row.get("read_basis"), str) and row["read_basis"].strip(),
                "Chat-text review needs actual source basis",
            )
        records.append(
            {
                "schema_version": "klima-human-decision/2",
                "origin": "HUMAN_REVIEWER_SUPPLIED",
                "reviewer_id": expected_reviewer,
                "candidate_id": cid,
                "candidate_sha256": manifest["candidate_sha256"][cid],
                "decision": row["decision"].upper(),
                "reasoning": row["reason"],
                "acceptance_scope": "REFERENCE_ANSWER_TEXT_ONLY",
                "source_basis": {
                    "original_access_attestation": attestation,
                    "source_directly_opened": direct,
                    "material": "ARCHIVED_ORIGINAL_FILE"
                    if direct
                    else "CHAT_EDITORIAL_SOURCE_TEXT",
                    "read_basis_verbatim": row.get("read_basis"),
                    "archived_source_sha256": source_hashes,
                },
                "reviewed_at": None,
                "reviewed_at_status": "NOT_RECORDED_BY_REVIEWER",
                "independent_answer_before_comparison_attestation": row.get(
                    "independent_answer_before_comparison_attestation"
                ),
                "fully_blind_review": "NOT_ASSERTED",
                "human_testimony": row,
                "gold_release": "NOT_AUTHORIZED",
            }
        )
    return records, manifest


def import_handoff(
    handoff: Path,
    candidates: Path,
    log: Path,
    expected_sha256: str,
    expected_reviewer: str,
    **source_paths,
) -> dict:
    payload = Path(handoff).read_bytes()
    require(digest(payload) == expected_sha256, "Handoff input hash mismatch")
    records, manifest = prepare(payload, candidates, expected_reviewer, **source_paths)
    require(Path(handoff).read_bytes() == payload, "Handoff changed during validation")
    result = append_decisions(log, records, manifest, digest(payload))
    result["source_basis_counts"] = {
        "direct_original": sum(r["source_basis"]["source_directly_opened"] for r in records),
        "chat_editorial_text": sum(
            not r["source_basis"]["source_directly_opened"] for r in records
        ),
    }
    result["reviewer_reading_times_inferred"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--handoff-sha256", required=True)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    require(not args.receipt.exists(), "Use a fresh receipt path")
    result = import_handoff(
        args.handoff, args.candidates, args.log, args.handoff_sha256, args.reviewer
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
