# ruff: noqa: E501
"""Strict reference-driven IE/relation scoring; labels only from owner-frozen human reviews."""

import json
from collections import defaultdict
from pathlib import Path

from ..store import digest
from .analysis import paired_cluster_interval

FIELDS = ["actor", "actor_role", "measure", "status", "amount", "unit", "amount_kind", "valid_time"]


def value(x):
    if x is None or str(x).strip().upper() in ("", "UNKNOWN", "UNKLAR"):
        return None
    return " ".join(str(x).strip().casefold().split())


def field_value(key, x):
    normalized = value(x)
    if normalized is None:
        return None
    # SI prefixes are case-sensitive: MW != mW. No implicit unit conversion.
    return " ".join(str(x).strip().split()) if key == "unit" else normalized


def cells(fields):
    return {
        (k, field_value(k, fields.get(k)))
        for k in FIELDS
        if field_value(k, fields.get(k)) is not None
    }


def relations(items):
    return {
        (value(r.get("subject")), value(r.get("predicate")), value(r.get("object")))
        for r in items
        if all(value(r.get(k)) is not None for k in ["subject", "predicate", "object"])
    }


def counts(reference, prediction):
    tp = len(reference & prediction)
    fp = len(prediction - reference)
    fn = len(reference - prediction)
    return tp, fp, fn


def scores(tp, fp, fn):
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def validate_outputs(data, bundle):
    if (
        data.get("schema") != "bavaria-study-method-outputs-v1"
        or data.get("selection_sha256") != digest(bundle["selection"])
        or data.get("snapshot_id") != bundle.get("snapshot_id")
    ):
        raise ValueError("Frozen method-output binding mismatch")
    if not isinstance(data.get("methods"), dict) or not data["methods"]:
        raise ValueError("Method arms missing")
    allowed = set(bundle["selection"]["main"])
    for arm in data["methods"].values():
        outputs = arm.get("outputs")
        if (
            not isinstance(arm.get("config"), dict)
            or not arm["config"]
            or not isinstance(outputs, list)
            or digest(outputs) != arm.get("output_sha256")
        ):
            raise ValueError("Versioned method configuration/outputs missing")
        ids = [p.get("task_id") for p in outputs]
        if len(set(ids)) != len(ids) or not set(ids).issubset(allowed):
            raise ValueError("Duplicate or foreign method task")
    return digest(data)


def evaluate(version_path, prediction_path, bundle):
    version = json.loads(Path(version_path).read_text())
    expected = version.pop("content_sha256")
    if digest(version) != expected or version["report"]["simulation"]:
        raise ValueError("Actual owner-frozen human review version required")
    if version.get("release") != "OWNER_ATTESTED_HUMAN_REVIEW_VERSION_NOT_AUTOMATIC_RESEARCH_GOLD":
        raise ValueError("Owner release missing")
    if not all(
        version.get("owner_gates", {}).get(k)
        for k in ["owner_verified_identities", "dependency_clearance", "rights_confirmed"]
    ):
        raise ValueError("Human identity/dependency/rights gates missing")
    predictions = json.loads(Path(prediction_path).read_text())
    validate_outputs(predictions, bundle)
    if predictions.get("schema") != "bavaria-study-method-outputs-v1" or predictions.get(
        "selection_sha256"
    ) != digest(bundle["selection"]):
        raise ValueError("Frozen method-output selection binding required")
    if predictions.get("snapshot_id") != bundle.get("snapshot_id") or not predictions.get(
        "methods"
    ):
        raise ValueError("Method/snapshot/configuration provenance missing")
    if not any(
        e["role"] == "C"
        and e["kind"] == "method_outputs_freeze"
        and e["payload"].get("outputs_sha256") == digest(predictions)
        for e in version.get("events", [])
    ):
        raise ValueError("Method outputs must be frozen in the review audit before main judgments")
    main_ids = set(bundle["selection"]["main"])
    first_main = min(
        (
            e["seq"]
            for e in version.get("events", [])
            if e["kind"] == "lock" and e["task"] in main_ids
        ),
        default=float("inf"),
    )
    if not any(
        e["kind"] == "method_outputs_freeze"
        and e["payload"].get("outputs_sha256") == digest(predictions)
        and e["seq"] < first_main
        for e in version.get("events", [])
    ):
        raise ValueError("Method freeze must precede main review exposure in the audit")
    tasks = {t["task_id"]: t for t in bundle["tasks"]}
    references = {
        j["task_id"]: j["final_reference"] for j in version["adjudication"] if j["final_reference"]
    }
    out = {
        "reference_sha256": expected,
        "prediction_sha256": digest(predictions),
        "reference_status": "OWNER_ATTESTED_HUMAN_REVIEW_NOT_AUTOMATIC_RESEARCH_GOLD",
        "methods": {},
        "paired_cluster_intervals": {},
    }
    paired = {}
    for method, arm in predictions["methods"].items():
        if (
            not arm.get("config")
            or not arm.get("output_sha256")
            or digest(arm.get("outputs")) != arm["output_sha256"]
        ):
            raise ValueError("Versioned arm configuration/output identity missing")
        outputs = arm["outputs"]
        if len({p["task_id"] for p in outputs}) != len(outputs):
            raise ValueError("Duplicate method task")
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        missing: dict[str, int] = defaultdict(int)
        per_case = {}
        unknown = {
            "reference_unknown_cells": 0,
            "recognized_unknown_cells": 0,
            "unsupported_values_on_unknown": 0,
        }
        unqualified = 0
        for p in outputs:
            tid = p["task_id"]
            if tid not in tasks:
                raise ValueError("Unknown method task")
            family = tasks[tid].get("primary_purpose")
            if family not in ["fields", "relation"]:
                continue
            ref = references.get(tid)
            if not ref:
                missing[family] += 1
                continue
            if family not in ["fields", "relation"]:
                continue
            reference_fields = ref.get("fields", {})
            if (
                family == "fields"
                and cells(reference_fields)
                and ref.get("studio_stages", {}).get("fields", {}).get("judgment") != "supported"
            ):
                unqualified += 1
                continue
            if family == "fields":
                for key in FIELDS:
                    if value(reference_fields.get(key)) is None:
                        unknown["reference_unknown_cells"] += 1
                        unknown["recognized_unknown_cells"] += (
                            value(p.get("fields", {}).get(key)) is None
                        )
                        unknown["unsupported_values_on_unknown"] += (
                            value(p.get("fields", {}).get(key)) is not None
                        )
            target = (
                cells(ref.get("fields", {}))
                if family == "fields"
                else relations(ref.get("relations", []))
            )
            proposed = (
                cells(p.get("fields", {}))
                if family == "fields"
                else relations(p.get("relations", []))
            )
            n = counts(target, proposed)
            totals[family] = [x + y for x, y in zip(totals[family], n)]
            case_score = (
                sum(
                    field_value(k, ref.get("fields", {}).get(k))
                    == field_value(k, p.get("fields", {}).get(k))
                    for k in FIELDS
                )
                / len(FIELDS)
                if family == "fields"
                else float(target == proposed)
            )
            per_case[tid] = {"value": case_score, "component": ref["component_ids"][0]}
        for tid, task in tasks.items():
            if task.get("primary_purpose") in ["fields", "relation"] and tid not in {
                p["task_id"] for p in outputs
            }:
                missing[task["primary_purpose"]] += 1
        out["methods"][method] = {
            "unknown_recognition": unknown,
            "unqualified_nonempty_field_references_excluded": unqualified,
            "paired_metric": "mean field-cell correctness / exact relation-set correctness; includes UNKNOWN",
            "by_primary_purpose": {f: scores(*n) for f, n in totals.items()},
            "missing_or_unresolved_tasks": dict(missing),
            "matching": "strict text normalization; SI unit case preserved; no fuzzy identity or numeric unit conversion",
            "retrieval_metrics": "NOT_COMPUTED_WITHOUT_FROZEN_MULTI_SYSTEM_JUDGMENT_POOL",
        }
        paired[method] = per_case
    names = sorted(paired)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for family in ["fields", "relation"]:
                ids = [t for t in tasks if tasks[t].get("primary_purpose") == family]
                rows = [
                    {
                        "a": paired[a].get(t, {}).get("value"),
                        "b": paired[b].get(t, {}).get("value"),
                        "component": paired[a].get(t, {}).get("component"),
                    }
                    for t in ids
                ]
                out["paired_cluster_intervals"][f"{family}:{a}−{b}"] = paired_cluster_interval(rows)
    return out
