"""Independent field-level verification and two-temp-directory pilot rebuild.

No label changes, new annotations, policy runs or source payload publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
EXPECTED_RELEASE = "73ef258f8286ed6844d55ac9fae4e57de6b61303f5e225fbd119b2480e9c51ba"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(out: Path) -> None:
    from research.benchmarks.sprint01 import pilot_release, release_gates, review, review_log
    from research.offline_evaluation.contracts import ValidationError, canonical, require

    require(not out.exists(), "Use an absent independent verification output directory")
    sys.dont_write_bytecode = True
    candidates = ROOT / "research/benchmarks/sprint01/review_packet/candidates.json"
    human_log = candidates.parent / "human_reviews.jsonl"
    authorization = ROOT / "research/sprints/sprint-01/PILOT_AUTHORIZATION.json"
    release = ROOT / "research/benchmarks/sprint01/releases/pilot-gold-v1/manifest.json"
    protected = [candidates, human_log, authorization, release]
    before = {str(path.relative_to(ROOT)): sha(path) for path in protected}
    receipt = pilot_release.verify_release(
        release, EXPECTED_RELEASE, candidates, human_log, authorization
    )
    manifest = json.loads(release.read_bytes())
    package, identities, _ = review.validate(candidates)
    rows = {row["id"]: row for row in package["candidates"]}
    events = review_log.verify_log(human_log.read_bytes())
    by_event = {event["decision"]["candidate_id"]: event for event in events}
    authority = json.loads(authorization.read_bytes())
    decision_record = ROOT / authority["decision_source"]["retained_record"]
    require(
        sha(decision_record) == authority["decision_source"]["retained_record_sha256"],
        "Retained owner-decision record hash mismatch",
    )
    protected.append(decision_record)
    before[str(decision_record.relative_to(ROOT))] = sha(decision_record)
    require(len(manifest["cases"]) == len(rows) == 10, "Ten exact approved cases required")
    require(
        {case["question_id"] for case in manifest["cases"]} == set(rows),
        "Release cases differ from frozen input scope",
    )
    checks = []
    overlays = {
        item["candidate_id"]: item
        for item in release_gates.semantics_proposal(
            package["candidates"], identities["candidate_sha256"]
        )["cases"]
    }
    for case in manifest["cases"]:
        cid = case["question_id"]
        row = rows[cid]
        event = by_event[cid]
        decision = event["decision"]
        require(case["question"] == row["question"], "Question changed: " + cid)
        require(case["task_family"] == row["task_family"], "Task family changed: " + cid)
        require(case["query_target_ags"] == row["municipalities"], "Query AGS changed: " + cid)
        require(case["reference_answer"] == row["proposed_answer"], "Answer changed: " + cid)
        require(
            case["reference_answer_sha256_utf8"]
            == hashlib.sha256(row["proposed_answer"].encode()).hexdigest(),
            "Answer UTF-8 hash changed: " + cid,
        )
        require(
            case["candidate_sha256"]
            == case["reference_answer_version"]
            == identities["candidate_sha256"][cid],
            "Candidate/version hash changed: " + cid,
        )
        require(case["source_versions"] == row["source_versions"], "Source scope changed: " + cid)
        require(case["evidence_spans"] == row["evidence"], "Evidence changed: " + cid)
        human = case["human_review"]
        require(human["event_sha256"] == event["event_sha256"], "Event mismatch: " + cid)
        require(
            human["decision_sha256"] == event["decision_sha256"],
            "Decision hash mismatch: " + cid,
        )
        for key in (
            "reviewer_id",
            "decision",
            "acceptance_scope",
            "source_basis",
            "reasoning",
            "reviewed_at",
            "reviewed_at_status",
            "fully_blind_review",
        ):
            require(human[key] == decision[key], "Human record field changed: " + cid + "/" + key)
        require(human["reviewed_at"] is None, "Reading time was inferred: " + cid)
        metadata = case["answerability_metadata"]
        require(
            metadata["historical_answerability"] == row["answerability"]
            and metadata["historical_candidate_unchanged"] is True
            and metadata["human_answer_acceptance_is_not_blanket_label_adjudication"] is True,
            "Historical label or review scope changed: " + cid,
        )
        if cid in overlays:
            require(
                canonical(metadata["orthogonal_interpretation_proposal"])
                == canonical(overlays[cid]),
                "Pending overlay changed: " + cid,
            )
            require(
                metadata["historical_answerability"] == "PARTIAL"
                and metadata["orthogonal_interpretation_proposal"]["adjudication"]
                == "PENDING_NOT_A_HUMAN_LABEL",
                "Overlay silently upgraded historical label: " + cid,
            )
        else:
            require(
                "orthogonal_interpretation_proposal" not in metadata,
                "Unexpected semantic amendment: " + cid,
            )
        checks.append(
            {
                "candidate_id": cid,
                "candidate_sha256": case["candidate_sha256"],
                "human_event_sha256": event["event_sha256"],
                "question_answer_and_source_scope_identical": True,
                "human_record_fields_identical": True,
                "historical_answerability_unchanged": row["answerability"],
                "pending_overlay_only": cid in overlays,
            }
        )
    expected_sources = {
        (source["acquisition_ags"], source["source_url"], source["raw_sha256"])
        for row in rows.values()
        for source in row["source_versions"]
    }
    require(len(expected_sources) == len(manifest["sources"]) == 8, "Eight exact sources required")
    require(
        expected_sources
        == {
            (source["acquisition_ags"], source["source_url"], source["raw_sha256"])
            for source in manifest["sources"]
        },
        "Release source metadata differs from admitted versions",
    )
    for source in manifest["sources"]:
        raw = review.EXPORT / "raw" / source["raw_sha256"]
        require(sha(raw) == source["raw_sha256"], "Original source changed")
        require(raw.stat().st_size == source["bytes"], "Original byte length changed")
        protected.append(raw)
        before[str(raw.relative_to(ROOT))] = sha(raw)
    dependencies = manifest["dependencies"]
    require(
        dependencies["source_version_groups"]
        == release_gates.dependency_groups(list(rows.values()), False)
        and len(dependencies["source_version_groups"]) == 7,
        "Source dependency membership changed",
    )
    require(
        dependencies["conservative_municipality_source_groups"]
        == release_gates.dependency_groups(list(rows.values()), True)
        and len(dependencies["conservative_municipality_source_groups"]) == 2,
        "Conservative dependency membership changed",
    )
    release_gates.validate_split(list(rows.values()), dependencies["assignments"])
    require(
        set(dependencies["assignments"].values()) == {"DEVELOPMENT_EXPOSED"}
        and dependencies["independent_held_out_test_items"] == 0
        and dependencies["random_question_split_permitted"] is False,
        "Release introduces held-out/independence claims",
    )
    available = {key for key, value in manifest["metric_permissions"].items() if value["available"]}
    require(
        available == {"answer_exact_text_match", "citation_locator_integrity"},
        "Only literal-reference and structural-anchor metrics may be available",
    )
    require(
        manifest["metric_permissions"]["answer_exact_text_match"]["scope"]
        == "LITERAL_REFERENCE_ONLY"
        and manifest["metric_permissions"]["citation_locator_integrity"]["scope"]
        == "STRUCTURAL_ANCHORS_ONLY",
        "Metric scope expanded beyond actual ground truth",
    )
    metric_checks = {}
    for metric in manifest["metric_permissions"]:
        try:
            pilot_release.require_pilot_metric(manifest, metric)
        except ValidationError:
            require(metric not in available, "Declared available metric was unexpectedly blocked")
            metric_checks[metric] = "DENIED_MISSING_GROUND_TRUTH"
        else:
            require(
                metric in available, "Unavailable scientific metric guard incorrectly permits use"
            )
            metric_checks[metric] = "PERMITTED_WITH_DECLARED_LIMITED_SCOPE"
    require(
        manifest["status"] == "PILOT_GOLD_APPROVED"
        and manifest["research_gold_status"] == "RESEARCH_GOLD_PENDING_EXTERNAL_VALIDATION"
        and manifest["m4_m5_authorized"] is False,
        "Release scope exceeds project-owner authorization",
    )
    summary = manifest["review_summary"]
    require(
        summary["direct_original_cases"] == ["SR-01"]
        and len(summary["chat_editorial_text_cases"]) == 9
        and summary["distinct_human_reviewers"] == 1
        and summary["independent_double_annotation_performed"] is False
        and summary["complete_blinding_asserted"] is False,
        "Human work/review basis overstated",
    )
    with tempfile.TemporaryDirectory(prefix="klima-independent-pilot-rebuild-") as temp:
        first = Path(temp) / "first"
        second = Path(temp) / "second"
        rebuilds = [
            pilot_release.freeze(candidates, human_log, authorization, directory)
            for directory in (first, second)
        ]
        require(
            (first / "manifest.json").read_bytes()
            == (second / "manifest.json").read_bytes()
            == release.read_bytes(),
            "Two independent output directories do not reproduce the exact release manifest",
        )
        require(
            (first / "SHA256SUMS").read_bytes() == (second / "SHA256SUMS").read_bytes(),
            "Rebuilt checksum files differ",
        )
    after = {str(path.relative_to(ROOT)): sha(path) for path in protected}
    require(before == after, "Protected inputs changed during independent verification")
    out.mkdir(parents=True)
    result = {
        "schema_version": "klima-pilot-release-independent-check/1",
        "status": "PASS_SCOPE_LIMITED_PILOT_RELEASE_VERIFICATION",
        "verified_manifest_sha256": EXPECTED_RELEASE,
        "existing_verify_release_receipt": receipt,
        "independent_case_field_checks": checks,
        "source_count": 8,
        "evidence_span_count": sum(len(case["evidence_spans"]) for case in manifest["cases"]),
        "historical_partial_overlay_case_ids": sorted(overlays),
        "source_dependency_groups": 7,
        "conservative_dependency_groups": 2,
        "held_out_test_items": 0,
        "metric_guard_actual_results": metric_checks,
        "two_temp_rebuild_receipts": rebuilds,
        "both_rebuilds_manifest_byte_identical_to_release": True,
        "both_rebuilds_checksum_files_byte_identical": True,
        "source_review_basis_and_timestamps_not_upgraded": True,
        "original_owner_decision_record_hash_verified": True,
        "protected_inputs_unchanged": True,
        "protected_input_sha256": after,
        "human_annotations_added": 0,
        "method_runs_executed": 0,
        "research_gold_approved": False,
        "m4_m5_authorized": False,
        "limitations": "Mechanical independent code/output audit; human identity/work and "
        "source semantic correctness are not newly certified. No Research-Gold release.",
    }
    (out / "release_verification.json").write_text(json.dumps(result, indent=2) + "\n")
    modules = [
        Path(__file__),
        Path(pilot_release.__file__),
        Path(review.__file__),
        Path(review_log.__file__),
        Path(release_gates.__file__),
    ]
    runtime = {
        "command": (
            "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python "
            "research/sprints/sprint-01/pilot_gold_validation/integrity/check_release.py --out "
        )
        + str(out.relative_to(ROOT)),
        "python": platform.python_version(),
        "source_sha256": {str(path.relative_to(ROOT)): sha(path) for path in modules},
        "output_sha256": sha(out / "release_verification.json"),
        "temporary_rebuilds": "Two distinct absent directories; removed after byte comparison",
    }
    (out / "run_manifest.json").write_text(json.dumps(runtime, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "manifest_sha256": EXPECTED_RELEASE}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    main(parser.parse_args().out.resolve())
