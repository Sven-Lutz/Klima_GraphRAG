"""Deterministic, scope-limited pilot freeze; not a research-gold release or experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from research.offline_evaluation.contracts import ValidationError, canonical, json_bytes, require

from .release_gates import dependency_groups, semantics_proposal, validate_split
from .review import EXPORT, validate
from .review_log import verify_log

RELEASE_SCHEMA = "klima-pilot-gold/1"
RESEARCH_STATUS = "RESEARCH_GOLD_PENDING_EXTERNAL_VALIDATION"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def metric_permissions() -> dict:
    """Do not turn an approved answer text into missing qrels, fields or claim judgments."""
    return {
        "answer_exact_text_match": {"available": True, "scope": "LITERAL_REFERENCE_ONLY"},
        "citation_locator_integrity": {"available": True, "scope": "STRUCTURAL_ANCHORS_ONLY"},
        "retrieval_precision_recall": {
            "available": False,
            "reason": "NO_COMPLETE_REVIEWED_QREL_UNIVERSE",
        },
        "extraction_field_precision_recall": {
            "available": False,
            "reason": "NO_TYPED_FIELD_LEVEL_GOLD",
        },
        "citation_semantic_correctness": {
            "available": False,
            "reason": "NO_CLAIM_CITATION_ENTAILMENT_JUDGMENTS",
        },
        "answer_factual_accuracy": {
            "available": False,
            "reason": "NO_ATOMIC_CLAIM_RUBRIC_OR_ADJUDICATED_SYSTEM_OUTPUT",
        },
        "answerability_accuracy": {
            "available": False,
            "reason": "HISTORIC_LABEL_SEMANTICS_NOT_ADJUDICATED",
        },
    }


def require_pilot_metric(manifest: dict, metric: str) -> dict:
    """Conservative public guard for consumers; approval status never supplies a denominator."""
    require(manifest.get("schema_version") == RELEASE_SCHEMA, "Unsupported pilot manifest")
    require(manifest.get("status") == "PILOT_GOLD_APPROVED", "Pilot approval required")
    permission = metric_permissions().get(metric)
    if permission is None:
        raise ValidationError("Unknown pilot metric")
    require(permission["available"], "Pilot metric unavailable: " + str(permission.get("reason")))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValidationError("Metric requires actual case ground truth")
    require(all(isinstance(case, dict) for case in cases), "Malformed case ground truth")
    if metric == "answer_exact_text_match":
        require(
            all(
                isinstance(case.get("reference_answer"), str)
                and bool(case["reference_answer"].strip())
                and digest(case["reference_answer"].encode("utf-8"))
                == case.get("reference_answer_sha256_utf8")
                for case in cases
            ),
            "Literal matching requires intact reference-answer text",
        )
    else:
        require(
            all(
                isinstance(case.get("evidence_spans"), list) and case["evidence_spans"]
                for case in cases
            ),
            "Locator checks require actual evidence anchors",
        )
    return permission


def build_manifest(candidates: Path, review_log: Path, authorization: Path, **source_paths) -> dict:
    """Verify the approved versions and source records, preserving all earlier bytes."""
    paths = {
        "candidates": Path(candidates),
        "review_log": Path(review_log),
        "authorization": Path(authorization),
    }
    before = {name: path.read_bytes() for name, path in paths.items()}
    package, identity, _ = validate(candidates, **source_paths)
    authority = json_bytes(before["authorization"])
    require(
        isinstance(authority, dict)
        and authority.get("schema_version") == "klima-pilot-authorization/1",
        "Explicit pilot authorization required",
    )
    require(
        authority.get("status") == "PILOT_GOLD_APPROVED"
        and authority.get("research_gold_approved") is False
        and authority.get("m4_m5_authorized") is False,
        "Authorization must distinguish pilot, research gold and Sprint 2",
    )
    require(
        isinstance(authority.get("decision_source"), dict)
        and authority["decision_source"].get("kind") == "EXPLICIT_PROJECT_OWNER_DECISION"
        and authority["decision_source"].get("url"),
        "Decision provenance required",
    )
    rows = sorted(package["candidates"], key=lambda row: row["id"])
    require(
        len(authority.get("case_ids", [])) == len(rows)
        and set(authority["case_ids"]) == {row["id"] for row in rows},
        "Pilot authorization case scope mismatch",
    )
    require(
        authority.get("candidate_file_sha256") == identity["candidate_file_sha256"],
        "Candidate package is not the owner-approved version",
    )
    require(
        authority.get("corpus_sha256") == identity["corpus_sha256"]
        and authority.get("catalog_sha256") == identity["catalog_sha256"],
        "Source catalog or parser corpus differs from authorized input freeze",
    )
    require(
        authority.get("human_log_sha256") == digest(before["review_log"]),
        "Human log is not the owner-approved history",
    )
    events = verify_log(before["review_log"])
    latest = {}
    for event in events:
        latest[event["decision"]["candidate_id"]] = event
    require(set(latest) == {row["id"] for row in rows}, "Human decision coverage mismatch")
    require(
        events and events[-1]["event_sha256"] == authority.get("human_log_chain_tip"),
        "Human log chain tip differs from authorization",
    )
    overlays = semantics_proposal(rows, identity["candidate_sha256"])
    by_overlay = {row["candidate_id"]: row for row in overlays["cases"]}
    catalog_path = Path(source_paths.get("catalog", EXPORT / "catalog.jsonl"))
    raw_dir = Path(source_paths.get("raw_dir", EXPORT / "raw"))
    catalog = {
        (row["url"], row["body_sha256"]): row
        for row in map(json_bytes, catalog_path.read_bytes().splitlines())
        if row
    }
    sources = {}
    cases = []
    for row in rows:
        event = latest[row["id"]]
        decision = event["decision"]
        require(
            decision.get("candidate_sha256") == identity["candidate_sha256"][row["id"]],
            "Human decision targets a different candidate version",
        )
        require(
            decision.get("decision") == "ACCEPT"
            and decision.get("acceptance_scope") == "REFERENCE_ANSWER_TEXT_ONLY",
            "Pilot case requires actual answer-text acceptance",
        )
        basis = decision.get("source_basis")
        require(
            isinstance(basis, dict) and type(basis.get("source_directly_opened")) is bool,
            "Actual per-case source-review basis required",
        )
        require(
            set(basis.get("archived_source_sha256", []))
            == {source["raw_sha256"] for source in row["source_versions"]},
            "Reviewed-source scope differs from candidate",
        )
        require(
            basis.get("material")
            == (
                "ARCHIVED_ORIGINAL_FILE"
                if basis["source_directly_opened"]
                else "CHAT_EDITORIAL_SOURCE_TEXT"
            ),
            "Source-access mode contradicts original access",
        )
        for source in row["source_versions"]:
            key = (source["source_url"], source["raw_sha256"])
            original = (raw_dir / key[1]).read_bytes()
            require(digest(original) == key[1], "Original source changed during freeze")
            record = catalog[key]
            sources[key] = {
                **source,
                "bytes": len(original),
                "source_publisher": record.get("publishing_institution"),
                "acquisition_time": record.get("acquisition_time"),
                "document_date": record.get("document_date"),
                "cutoff_eligibility": record.get("cutoff_eligibility"),
                "claim_jurisdiction": record.get("territorial_scope"),
                "classification": "LOCAL_ORIGINAL_NOT_BUNDLED_NOT_CLEARED_FOR_REDISTRIBUTION",
            }
        metadata = {
            "schema_version": "klima-pilot-answerability-overlay/1",
            "historical_answerability": row["answerability"],
            "historical_candidate_unchanged": True,
            "human_answer_acceptance_is_not_blanket_label_adjudication": True,
        }
        if row["id"] in by_overlay:
            metadata["orthogonal_interpretation_proposal"] = by_overlay[row["id"]]
        cases.append(
            {
                "question_id": row["id"],
                "question": row["question"],
                "task_family": row["task_family"],
                "query_target_ags": row["municipalities"],
                "candidate_sha256": identity["candidate_sha256"][row["id"]],
                "reference_answer": row["proposed_answer"],
                "reference_answer_sha256_utf8": digest(row["proposed_answer"].encode("utf-8")),
                "reference_answer_version": identity["candidate_sha256"][row["id"]],
                "source_versions": row["source_versions"],
                "evidence_spans": row["evidence"],
                "human_review": {
                    "event_sha256": event["event_sha256"],
                    "decision_sha256": event["decision_sha256"],
                    "reviewer_id": decision["reviewer_id"],
                    "decision": decision["decision"],
                    "acceptance_scope": decision["acceptance_scope"],
                    "source_basis": basis,
                    "reasoning": decision["reasoning"],
                    "reviewed_at": decision["reviewed_at"],
                    "reviewed_at_status": decision["reviewed_at_status"],
                    "fully_blind_review": decision["fully_blind_review"],
                },
                "answerability_metadata": metadata,
                "limitations": row["limitations"],
            }
        )
    assignments = {row["id"]: "DEVELOPMENT_EXPOSED" for row in rows}
    validate_split(rows, assignments)
    manifest = {
        "schema_version": RELEASE_SCHEMA,
        "release_id": authority["release_id"],
        "status": "PILOT_GOLD_APPROVED",
        "research_gold_status": RESEARCH_STATUS,
        "approval_scope": "TEN_EXISTING_REFERENCE_ANSWER_TEXTS_ONLY",
        "decision_authority": authority,
        "inputs": {
            **{name + "_sha256": digest(data) for name, data in before.items()},
            "corpus_sha256": identity["corpus_sha256"],
            "catalog_sha256": identity["catalog_sha256"],
            "human_log_chain_tip": events[-1]["event_sha256"],
        },
        "generator": {
            "module": "research.benchmarks.sprint01.pilot_release",
            "source_sha256": digest(Path(__file__).read_bytes()),
            "serialization": "repository canonical JSON UTF-8 plus one LF",
        },
        "cases": cases,
        "sources": [sources[key] for key in sorted(sources)],
        "review_summary": {
            "distinct_human_reviewers": len(
                {case["human_review"]["reviewer_id"] for case in cases}
            ),
            "direct_original_cases": [
                case["question_id"]
                for case in cases
                if case["human_review"]["source_basis"]["source_directly_opened"]
            ],
            "chat_editorial_text_cases": [
                case["question_id"]
                for case in cases
                if not case["human_review"]["source_basis"]["source_directly_opened"]
            ],
            "independent_double_annotation_performed": False,
            "complete_blinding_asserted": False,
        },
        "dependencies": {
            "source_version_groups": dependency_groups(rows, False),
            "conservative_municipality_source_groups": dependency_groups(rows, True),
            "assignments": assignments,
            "independent_held_out_test_items": 0,
            "random_question_split_permitted": False,
        },
        "metric_permissions": metric_permissions(),
        "permitted_use": ["SMOKE_TEST", "TECHNICAL_REGRESSION", "EXPLORATORY_PILOT"],
        "prohibited_claims": [
            "INDEPENDENT_HELD_OUT_SCIENTIFIC_EVALUATION",
            "NATIONAL_REPRESENTATIVENESS",
            "ARCHITECTURE_SUPERIORITY",
        ],
        "m4_m5_authorized": False,
        "restoration": "Restore original source/catalog/corpus bytes from the owner-held "
        "M0 backup and existing corpus manifest; live refetch is not byte reproducible.",
    }
    require(
        all(path.read_bytes() == before[name] for name, path in paths.items()),
        "Release input changed during build",
    )
    return manifest


def freeze(
    candidates: Path, review_log: Path, authorization: Path, out: Path, **source_paths
) -> dict:
    manifest = build_manifest(candidates, review_log, authorization, **source_paths)
    require(not out.exists() and not out.is_symlink(), "Use a fresh pilot release directory")
    payload = canonical(manifest) + b"\n"
    out.mkdir(parents=True)
    (out / "manifest.json").write_bytes(payload)
    (out / "SHA256SUMS").write_text(digest(payload) + "  manifest.json\n")
    return {
        "status": manifest["status"],
        "research_gold_status": RESEARCH_STATUS,
        "manifest_sha256": digest(payload),
        "case_count": len(manifest["cases"]),
        "source_count": len(manifest["sources"]),
    }


def verify_release(
    manifest_file: Path,
    expected_sha256: str,
    candidates: Path,
    review_log: Path,
    authorization: Path,
    **source_paths,
) -> dict:
    supplied = manifest_file.read_bytes()
    require(digest(supplied) == expected_sha256, "Pilot manifest hash mismatch")
    expected = build_manifest(candidates, review_log, authorization, **source_paths)
    require(supplied == canonical(expected) + b"\n", "Pilot manifest content/version mismatch")
    return {
        "status": "PASS",
        "manifest_sha256": expected_sha256,
        "case_count": len(expected["cases"]),
        "source_count": len(expected["sources"]),
        "research_gold_status": RESEARCH_STATUS,
        "m4_m5_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--review-log", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("freeze")
    create.add_argument("--out", required=True, type=Path)
    check = sub.add_parser("verify")
    check.add_argument("--manifest", required=True, type=Path)
    check.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if args.operation == "freeze":
        receipt = freeze(args.candidates, args.review_log, args.authorization, args.out)
    else:
        receipt = verify_release(
            args.manifest,
            args.manifest_sha256,
            args.candidates,
            args.review_log,
            args.authorization,
        )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
