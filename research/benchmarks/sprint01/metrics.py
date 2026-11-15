"""Small pure metric contracts. No adapters, methods, labels or results are generated."""
from research.offline_evaluation.contracts import canonical, require


def retrieval_at_k(ranked_ids, qrels, k, *, judgments_complete):
    """Binary precision among returned top-k items; recall in complete explicit universe."""
    require(judgments_complete is True, "Complete explicitly reviewed qrel universe required")
    require(type(k) is int and k > 0, "Positive integer k required")
    require(isinstance(qrels, dict) and qrels and all(isinstance(key, str) and key
            and type(value) is bool for key, value in qrels.items()), "Explicit binary qrels required")
    require(isinstance(ranked_ids, list) and all(isinstance(x, str) for x in ranked_ids)
            and len(ranked_ids) == len(set(ranked_ids)), "Distinct ranked identities required")
    require(set(ranked_ids) <= qrels.keys(), "Ranked item outside judged source universe")
    returned = ranked_ids[:k]
    tp = sum(qrels[key] for key in returned)
    positives = sum(qrels.values())
    return {"k": k, "relevant_returned": tp, "returned": len(returned), "relevant_in_scope": positives,
            "precision_at_k_returned": tp / len(returned) if returned else None,
            "recall_at_k": tp / positives if positives else None,
            "precision_denominator": "ACTUAL_RETURNED_UP_TO_K",
            "precision_NA_reason": "EMPTY_RETURN" if not returned else None,
            "recall_NA_reason": "NO_POSITIVE_QRELS_IN_REVIEWED_SCOPE" if not positives else None}


def field_counts(expected, predicted):
    """Exact-value micro counts; ABSENT is a judgment, UNKNOWN is never a negative label."""
    require(isinstance(expected, dict) and expected and isinstance(predicted, dict),
            "Explicit field maps required")
    require(set(predicted) <= expected.keys(), "Prediction outside declared field schema")
    for field, row in list(expected.items()) + list(predicted.items()):
        require(isinstance(row, dict) and row.get("status") in {"PRESENT", "ABSENT", "UNKNOWN"},
                "Field status must be PRESENT/ABSENT/UNKNOWN")
        require(row["status"] != "PRESENT" or "value" in row, "Present field needs exact value")
        require(row["status"] == "PRESENT" or "value" not in row, "Absent/unknown field has no value")
    tp = fp = fn = tn = unknown_gold = unverified_absence = 0
    for field, gold in expected.items():
        output = predicted.get(field, {"status": "UNKNOWN"})
        if gold["status"] == "UNKNOWN":
            unknown_gold += 1
            continue
        if gold["status"] == "PRESENT":
            if output["status"] == "PRESENT" and canonical(output["value"]) == canonical(gold["value"]):
                tp += 1
            else:
                fn += 1
                fp += int(output["status"] == "PRESENT")
        elif output["status"] == "PRESENT":
            fp += 1
        elif output["status"] == "ABSENT":
            tn += 1
        else:
            unverified_absence += 1
    return {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_absent": tn,
            "unknown_gold_fields_excluded": unknown_gold, "absence_not_predicted": unverified_absence,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "matching": "EXACT_TYPED_JSON_VALUE_NO_UNIT_OR_NUMERIC_NORMALIZATION"}
