"""Diagnostic dependence and method proposals; never grant scientific release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.offline_evaluation.contracts import require

from .review import validate


def dependency_groups(candidates: list[dict], include_municipalities: bool) -> list[list[str]]:
    """Connected source/version/explicit-link groups, optionally conservative municipality joins."""
    by_id = {row["id"]: row for row in candidates}
    require(len(by_id) == len(candidates), "Duplicate candidate ID")
    neighbors: dict[str, set[str]] = {cid: set() for cid in by_id}
    owners: dict[tuple[str, str], str] = {}
    for row in candidates:
        cid = row["id"]
        keys = {
            (kind, source[field])
            for source in row["source_versions"]
            for kind, field in (("body", "raw_sha256"), ("version_family", "source_url"))
        }
        keys.update(("unit", anchor["unit_id"]) for anchor in row["evidence"])
        if include_municipalities:
            # Query targets also count: a deliberate wrong-target question can link
            # municipalities even when its supported claim concerns a different place.
            keys.update(("municipality", ags) for ags in row["municipalities"])
            keys.update(
                ("municipality", s["acquisition_ags"])
                for s in row["source_versions"]
                if "acquisition_ags" in s
            )
        for key in keys:
            if key in owners:
                other = owners[key]
                neighbors[cid].add(other)
                neighbors[other].add(cid)
            owners[key] = cid
        for other in row.get("linked_candidates", []):
            require(other in by_id, "Missing linked candidate")
            neighbors[cid].add(other)
            neighbors[other].add(cid)
    groups: list[list[str]] = []
    unseen = set(by_id)
    while unseen:
        queue = [min(unseen)]
        component = set()
        while queue:
            cid = queue.pop()
            if cid in component:
                continue
            component.add(cid)
            queue.extend(neighbors[cid] - component)
        unseen -= component
        groups.append(sorted(component))
    return sorted(groups)


def validate_split(candidates: list[dict], assignments: dict[str, str]) -> None:
    require(set(assignments) == {row["id"] for row in candidates}, "Incomplete split assignment")
    require(
        all(
            value in {"DEVELOPMENT_EXPOSED", "TEST", "QUARANTINE"} for value in assignments.values()
        ),
        "Unsupported split",
    )
    for group in dependency_groups(candidates, include_municipalities=True):
        require(
            len({assignments[cid] for cid in group}) == 1,
            "Leakage: connected source/municipality component crosses splits",
        )


def semantics_proposal(candidates: list[dict], hashes: dict[str, str]) -> dict:
    by_id = {row["id"]: row for row in candidates}
    claims = {
        "SR-02": ("Das Konzept wurde bis 2023 fertiggestellt.", "ANSWERED"),
        "WB-03": (
            "Ein abgeschlossener kommunaler Wärmeplan oder konkreter nächster "
            "Umsetzungsschritt ist nachgewiesen.",
            "ANSWERED",
        ),
        "WB-04": (
            "Der vollständige Dorfheizungs-Anschlussstand 2026 ist bekannt.",
            "PARTIALLY_ANSWERED",
        ),
    }
    require(claims.keys() <= by_id.keys(), "Required semantic review cases missing")
    return {
        "schema_version": "klima-orthogonal-semantics-proposal/1",
        "status": "PROPOSED_PENDING_EXPLICIT_HUMAN_ADJUDICATION",
        "changes_to_frozen_candidates": False,
        "gold_release": "NOT_AUTHORIZED",
        "cases": [
            {
                "candidate_id": cid,
                "parent_candidate_sha256": hashes[cid],
                "original_answerability_unchanged": by_id[cid]["answerability"],
                "question_response_status": response,
                "claim": claim,
                "claim_support_in_specified_source": "INSUFFICIENT_EVIDENCE",
                "evidence_scope": "ARCHIVED_DOCUMENT",
                "real_world_status": "UNKNOWN",
                "source_versions": by_id[cid]["source_versions"],
                "adjudication": "PENDING_NOT_A_HUMAN_LABEL",
            }
            for cid, (claim, response) in claims.items()
        ],
        "rule": "No proof in a specified source is not proof of real-world nonexistence. "
        "Answer-text acceptance never implicitly approves a semantic-label amendment.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    require(not args.out.exists(), "Use a fresh gate-analysis directory")
    package, manifest, _ = validate(args.candidates)
    rows = package["candidates"]
    assignments = {row["id"]: "DEVELOPMENT_EXPOSED" for row in rows}
    validate_split(rows, assignments)
    groups = {
        "schema_version": "klima-pilot-dependency-freeze/1",
        "candidate_file_sha256": manifest["candidate_file_sha256"],
        "source_version_groups": dependency_groups(rows, False),
        "conservative_municipality_source_groups": dependency_groups(rows, True),
        "assignments": assignments,
        "held_out_test_items": 0,
        "grouping_is_independence_proof": False,
        "limitations": "Exposed convenience sample. No new prospective test set. "
        "Near-duplicate/content-family audit remains separately required.",
        "gold_release": "NOT_AUTHORIZED",
        "m4_m5": "NOT_AUTHORIZED",
    }
    args.out.mkdir(parents=True)
    (args.out / "dependency_groups.json").write_text(json.dumps(groups, indent=2) + "\n")
    (args.out / "semantics_proposal.json").write_text(
        json.dumps(
            semantics_proposal(rows, manifest["candidate_sha256"]), ensure_ascii=False, indent=2
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
