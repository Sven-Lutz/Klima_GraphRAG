"""Explicit graded-reference and claim metrics, with labels and missingness preserved."""

from __future__ import annotations

import math
from collections import Counter
from statistics import mean

ORIGINS = {"heuristic", "llm_judge", "synthetic", "independent_human"}


def _metric(value, origin, reason=None, denominator=None):
    return dict(value=value, label_origin=origin, missing_reason=reason, denominator=denominator)


def _judged_ratio(values, origin, name):
    known = [v for v in values if v is not None]
    if any(type(v) is not bool for v in known):
        raise ValueError("Explicit boolean judgments required: " + name)
    return _metric(
        mean(known) if known else None,
        origin,
        None if known else "NO_EXPLICIT_JUDGMENTS",
        len(known),
    )


def bound_claim_judgments(output, reviews):
    """Bind immutable judgments to the exact answer, question, source set and claim.

    A changed or partly reviewed answer retains missing judgments. No source-truth
    verdict implies citation, temporal or hallucination correctness automatically.
    """
    if not isinstance(reviews, list):
        raise ValueError("Bound claim reviews must be a list")
    aligned = []
    for claim in output.get("claims", []):
        matched = []
        for review in reviews:
            if not isinstance(review, dict) or not isinstance(review.get("judgments"), dict):
                raise ValueError("Structured bound claim judgments required")
            fields = ("answer_text_sha256", "query_sha256", "snapshot_id")
            if (
                all(output.get(key) and review.get(key) == output[key] for key in fields)
                and claim.get("id")
                and review.get("claim_id") == claim["id"]
                and isinstance(claim.get("text"), str)
                and review.get("text") == claim["text"]
                and isinstance(claim.get("source_ids"), list)
                and bool(claim["source_ids"])
                and set(review.get("source_ids", [])) == set(claim["source_ids"])
                and set(review.get("context_source_ids", []))
                == set(claim.get("context_source_ids", []))
            ):
                matched.append(review["judgments"])
        if matched and any(value != matched[0] for value in matched):
            raise ValueError("Conflicting consolidated judgments on exact answer claim")
        aligned.append(matched[0] if matched else {})
    return aligned


def evaluate_case(output: dict, reference: dict | None = None, *, k=8):
    """Unjudged outputs have NA quality metrics, never automatic negative labels.

    reference: label_origin, qrels:{id:0..3}, complete_universe, answerable:bool,
    claim_reviews:[{supported,citation_correct,scope_correct,temporal_valid,hallucinated}],
    required_evidence_groups:[[alternative_ids]], expected_scope:[ags].
    Claim reviews must align with output claims, not silently omit failed claims.
    """
    if type(k) is not int or k < 1:
        raise ValueError("Positive integer k required")
    reference = reference or {}
    origin = reference.get("label_origin")
    if reference and origin not in ORIGINS:
        raise ValueError("References require explicit judgment origin")
    ranked = output.get("ranked_ids", [])
    if not isinstance(ranked, list) or any(not isinstance(i, str) for i in ranked):
        raise ValueError("Ranked identities must be strings")
    if len(ranked) != len(set(ranked)):
        raise ValueError("Duplicate evidence in ranked output")
    selected = ranked[:k]
    metrics = {
        name: _metric(None, origin, "NO_REFERENCE")
        for name in (
            "precision_at_k",
            "precision_returned",
            "recall_at_k",
            "mrr",
            "ndcg_at_k",
            "hit_at_k",
            "answer_support",
            "faithfulness",
            "citation_correctness",
            "abstention_quality",
            "scope_correctness",
            "temporal_validity",
            "multi_hop_success",
            "hallucination_rate",
        )
    }
    qrels = reference.get("qrels")
    if qrels is not None:
        if not isinstance(qrels, dict) or any(
            not isinstance(key, str) or type(grade) is not int or grade not in range(4)
            for key, grade in qrels.items()
        ):
            raise ValueError("Explicit grades 0..3 required")
        # Unjudged is missing, not zero, including when ranking contains foreign items.
        judged = all(key in qrels for key in selected)
        if judged:
            relevant = sum(qrels[key] > 0 for key in selected)
            metrics["precision_at_k"] = _metric(relevant / k, origin, denominator=k)
            metrics["precision_returned"] = _metric(
                relevant / len(selected) if selected else None,
                origin,
                None if selected else "EMPTY_RETURN",
                len(selected),
            )
            metrics["hit_at_k"] = _metric(int(relevant > 0), origin, denominator=1)
            first = next((rank for rank, key in enumerate(selected, 1) if qrels[key] > 0), None)
            metrics["mrr"] = _metric(1 / first if first else 0.0, origin, denominator=1)
        else:
            for name in ["precision_at_k", "precision_returned", "hit_at_k", "mrr"]:
                metrics[name] = _metric(None, origin, "UNJUDGED_RETRIEVED_ITEMS")
        if reference.get("complete_universe") is True and judged:
            positives = sum(g > 0 for g in qrels.values())
            metrics["recall_at_k"] = _metric(
                relevant / positives if positives else None,
                origin,
                None if positives else "NO_POSITIVE_REFERENCE",
                positives,
            )
            dcg = sum(
                (2 ** qrels[key] - 1) / math.log2(rank + 1) for rank, key in enumerate(selected, 1)
            )
            ideal = sum(
                (2**grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(sorted(qrels.values(), reverse=True)[:k], 1)
            )
            metrics["ndcg_at_k"] = _metric(
                dcg / ideal if ideal else None, origin, None if ideal else "NO_POSITIVE_REFERENCE"
            )
        else:
            for name in ["recall_at_k", "ndcg_at_k"]:
                metrics[name] = _metric(None, origin, "INCOMPLETE_OR_UNJUDGED_REFERENCE_UNIVERSE")
    reviews = reference.get("claim_reviews")
    if "bound_claim_reviews" in reference:
        if reviews is not None:
            raise ValueError("Use one explicit claim review binding protocol")
        reviews = bound_claim_judgments(output, reference["bound_claim_reviews"])
    if reviews is not None:
        claims = output.get("claims", [])
        if not isinstance(reviews, list) or len(reviews) != len(claims):
            raise ValueError("Every output claim needs an explicit review or null judgments")
        for metric, field in [
            ("answer_support", "supported"),
            ("faithfulness", "supported"),
            ("citation_correctness", "citation_correct"),
            ("scope_correctness", "scope_correct"),
            ("temporal_validity", "temporal_valid"),
            ("hallucination_rate", "hallucinated"),
        ]:
            metrics[metric] = _judged_ratio([r.get(field) for r in reviews], origin, field)
    if "answerable" in reference:
        if type(reference["answerable"]) is not bool or type(output.get("abstained")) is not bool:
            raise ValueError("Explicit answerability and abstention booleans required")
        metrics["abstention_quality"] = _metric(
            int(output["abstained"] != reference["answerable"]), origin, denominator=1
        )
    if "required_evidence_groups" in reference:
        groups = reference["required_evidence_groups"]
        if not isinstance(groups, list) or not groups or any(not group for group in groups):
            raise ValueError("Multi-hop reference requires nonempty alternative evidence groups")
        metrics["multi_hop_success"] = _metric(
            int(all(set(group) & set(selected) for group in groups)),
            origin,
            denominator=len(groups),
        )
    if "expected_scope" in reference and "scope_ids" in output:
        metrics["scope_identity_match"] = _metric(
            int(set(output["scope_ids"]) <= set(reference["expected_scope"])),
            origin,
            "IDENTITY_CHECK_ONLY_NOT_CLAIM_VALIDITY",
        )
    for name in ["latency_ms", "input_tokens", "output_tokens", "provider_cost"]:
        value = output.get(name)
        if value is not None and (type(value) not in (int, float) or value < 0):
            raise ValueError("Nonnegative measured resource values required")
        metrics[name] = _metric(value, "measured", None if value is not None else "NOT_RECORDED")
    return metrics


def summarize_results(results):
    """Never average independently annotated, synthetic, judge and heuristic scores together."""
    groups = {}
    errors = Counter()
    for row in results:
        for name, metric in row.get("metrics", {}).items():
            key = (row.get("method", "unknown"), name, metric.get("label_origin"))
            group = groups.setdefault(key, {"values": [], "missing": Counter(), "total": 0})
            group["total"] += 1
            if metric["value"] is None:
                group["missing"][metric.get("missing_reason") or "UNKNOWN"] += 1
            else:
                group["values"].append(metric["value"])
        for error in row.get("errors", []):
            errors[error] += 1
    metrics = []
    for (method, name, origin), group in sorted(groups.items(), key=lambda x: str(x[0])):
        metrics.append(
            dict(
                method=method,
                metric=name,
                label_origin=origin,
                value=mean(group["values"]) if group["values"] else None,
                evaluated=len(group["values"]),
                total=group["total"],
                missing=dict(group["missing"]),
            )
        )
    return dict(
        metrics=metrics,
        errors=dict(errors),
        independent_gold_available=any(
            m["label_origin"] == "independent_human" and m["evaluated"] for m in metrics
        ),
    )
