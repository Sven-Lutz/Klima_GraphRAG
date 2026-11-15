"""Bound independent-review exports to evaluation questions without inventing complete qrels."""

from __future__ import annotations

import hashlib
import re

from research.gold_studio.source_bindings import claim_source_partition
from research.gold_studio.store import digest


def reference_from_review_export(
    export: dict,
    questions: list[dict],
    *,
    allow_technical=False,
    skip_other_tasks=False,
    bind_observations=False,
):
    """Return task-id-keyed partial graded references; complete-universe metrics stay NA.

    Requires export content integrity and exact task/snapshot/split bindings. Technical
    fixtures can exercise the workflow but are permanently tagged synthetic. Source-only
    and unjudged review objects never become passage relevance labels.
    """
    if export.get("schema") != "klima-independent-review-v1":
        raise ValueError("Unsupported independent-review export")
    body = {key: value for key, value in export.items() if key != "content_sha256"}
    if not export.get("content_sha256") or digest(body) != export["content_sha256"]:
        raise ValueError("Gold export content checksum mismatch")
    technical = export.get("technical") is True
    if technical and not allow_technical:
        raise ValueError("Technical review export cannot enter scientific evaluation")
    if not technical and export.get("human_gold") is not True:
        raise ValueError("Independent human gold is not established")
    if not export.get("gold_version"):
        raise ValueError("A versioned consolidated export is required")
    allowed = {row["question_id"]: row for row in questions}
    references = {}
    for label in export.get("labels", []):
        task_id = label.get("task_id")
        if task_id is None and bind_observations:
            task_id = (label.get("payload") or {}).get("review_observation_id")
        if task_id not in allowed:
            if skip_other_tasks:
                continue
            raise ValueError("Review task outside selected evaluation split")
        task = allowed[task_id]
        split = {
            "human_annotation_candidate": "human_candidates",
            "independent_holdout": "holdout",
            "adversarial_negative": "adversarial",
        }.get(label.get("split"), label.get("split"))
        if export.get("snapshot_id") != task["snapshot_id"] or split != task["split"]:
            raise ValueError("Review snapshot/split differs from evaluation task")
        expected_origin = "synthetic_fixture" if technical else "independent_human"
        if label.get("label_origin") != expected_origin or label.get("human_gold") is not (
            not technical
        ):
            raise ValueError("Review label origin differs from export status")
        if not label.get("final_event_hash") or len(set(label.get("review_hashes", []))) < 2:
            raise ValueError("Independent reviews and final consolidation provenance required")
        kind = label.get("kind")
        if kind not in {"claim", "answer_evidence"}:
            continue
        if kind == "answer_evidence" and label.get("relevance") is None:
            continue
        ids = []
        for source in label.get("sources", []):
            evidence_id = source.get("unit_id") or source.get("evidence_id")
            if evidence_id and source.get("sha256"):
                ids.append(evidence_id)
        if not ids:
            raise ValueError("Passage judgment requires exact source and evidence/unit identity")
        bound = None
        if kind == "claim":
            payload = label.get("payload") or {}
            ids, context_ids = claim_source_partition(payload, label.get("sources", []))
            judgments = label.get("metric_judgments") or {}
            if not isinstance(judgments, dict):
                raise ValueError("Structured claim metric judgments required")
            if not judgments or not any(value is not None for value in judgments.values()):
                continue
            fields = {
                "supported",
                "citation_correct",
                "scope_correct",
                "temporal_valid",
                "hallucinated",
            }
            if set(judgments) - fields or any(
                value is not None and type(value) is not bool for value in judgments.values()
            ):
                raise ValueError("Claim metric judgments must be explicit booleans or null")
            output_hash = payload.get("output_sha256", "")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", output_hash)
                or not payload.get("claim_id")
                or not isinstance(payload.get("proposed_text"), str)
                or payload.get("question") != task["question"]
                or label.get("original_inspection_confirmed") is not True
            ):
                raise ValueError("Exact inspected claim/answer/question binding required")
            bound = dict(
                answer_text_sha256=output_hash,
                query_sha256=hashlib.sha256(task["question"].encode()).hexdigest(),
                snapshot_id=task["snapshot_id"],
                claim_id=payload["claim_id"],
                text=re.sub(r"\s*\[C[0-9]+\]", "", payload["proposed_text"]).strip().rstrip("."),
                source_ids=sorted(set(ids)),
                context_source_ids=context_ids,
                judgments=judgments,
                final_event_hash=label["final_event_hash"],
                object_id=label.get("object_id"),
            )
        else:
            grade = label["relevance"]
            if type(grade) is not int or grade not in (0, 1, 2):
                raise ValueError("Review relevance must be 0,1,2 or explicitly unjudged")
        ref = references.setdefault(
            task_id,
            dict(
                label_origin="synthetic" if technical else "independent_human",
                complete_universe=False,
                source_gold_version=export["gold_version"],
                source_export_sha256=export["content_sha256"],
                review_provenance=[],
            ),
        )
        if bound is not None:
            ref.setdefault("bound_claim_reviews", []).append(bound)
            ref["review_provenance"].append(label["final_event_hash"])
            continue
        ref.setdefault("qrels", {})
        for evidence_id in ids:
            if evidence_id in ref["qrels"] and ref["qrels"][evidence_id] != grade:
                raise ValueError("Conflicting consolidated qrels for same evidence")
            ref["qrels"][evidence_id] = grade
        ref["review_provenance"].append(label["final_event_hash"])
    return references
