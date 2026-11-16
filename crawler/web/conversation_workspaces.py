"""Pure, bounded workspace contracts over already qualified answer evidence."""

import hashlib
import json
import re


def settings(snapshot, fixture, generation=None):
    return {
        "version": "model-registry-v2.1",
        "snapshot": snapshot,
        "fixture": fixture,
        "answer_generation": generation
        or {
            "provider": "deterministic_extractive",
            "model": "verbatim-qualification-v1",
            "version": "v1",
            "digest": None,
            "execution": "local",
            "role": "answer",
            "context_window": None,
            "cost_status": "NO_PAID_INFERENCE",
            "privacy": "LOCAL_SESSION",
            "availability": "AVAILABLE",
            "qualification": "TECHNICAL_ONLY_NOT_HUMAN_VALIDATED",
            "hardware": "CPU; no model weights",
            "parameters": {},
        },
        "information_extraction": {
            "active_index": snapshot,
            "model": "FIXTURE" if fixture else "SEE_SNAPSHOT_MANIFEST",
            "qualification": "NO_BROAD_LLM_IE_RELEASE",
            "extraction_version": "SEE_SNAPSHOT",
            "lifecycle": "Model selection never rewrites an index; new isolated run required",
        },
        "retrieval_ranking": {"policy": "intent-bounded-v1", "quality_ranking": "NOT_ESTABLISHED"},
        "verification_evaluation": {
            "method": "span/scope/number checks",
            "semantic_entailment": "NOT_EVALUATED",
            "human_gold": False,
        },
    }


def visualization(turn):
    """No model prose numbers, inferred sums, temporal series or cross-unit aggregation."""
    rows = []
    seen = set()
    for claim in turn["claims"]:
        for anchor in claim["evidence"]:
            if anchor["evidence_id"] in seen:
                continue
            seen.add(anchor["evidence_id"])
            for match in re.finditer(
                r"(?<![\w.,+−-])(\d+(?:[.,]\d+)?)\s*(kWp|kWh|MWh|GWh|kW|MW|Euro|€)\b",
                anchor["quote"],
            ):
                raw = match.group(1)
                # Grouping separators and decimal notation cannot be guessed.
                if re.search(r"[.,]\d{3}$", raw):
                    continue
                rows.append(
                    {
                        "value": float(raw.replace(",", ".")),
                        "original_value": match.group(0),
                        "unit": match.group(2),
                        "evidence_id": anchor["evidence_id"],
                        "passage_sha256": anchor["passage_sha256"],
                        "municipality_ids": anchor["municipality_ids"],
                        "modality": claim["evidence_kind"],
                        "quote": anchor["quote"],
                        "event_time": None,
                        "original_rights": "NOT_CLEARED_FOR_REDISTRIBUTION",
                    }
                )
    grouped = {}
    for row in rows:
        identity = (
            row["passage_sha256"],
            row["quote"],
            row["original_value"],
            tuple(row["municipality_ids"]),
            row["modality"],
        )
        if identity in grouped:
            grouped[identity]["evidence_ids"].append(row["evidence_id"])
        else:
            row["evidence_ids"] = [row["evidence_id"]]
            grouped[identity] = row
    rows = list(grouped.values())
    units = {r["unit"] for r in rows}
    scopes = {tuple(r["municipality_ids"]) for r in rows}
    modalities = {r["modality"] for r in rows}
    compatible = bool(rows) and len(units) == len(scopes) == len(modalities) == 1
    # Only a single observation is safe without a qualified metric definition.
    chart = compatible and len(rows) == 1
    spec = {
        "version": "visualization-v2.1",
        "title": "Dokumentierte Einzelwerte",
        "snapshot": turn["dataset_snapshot"],
        "turn_id": turn["turn_id"],
        "period": "UNKNOWN",
        "aggregation": "NONE",
        "rows": rows,
        "chart_type": "bar" if chart else "table",
        "chart_allowed": chart,
        "status": "SINGLE_SOURCE_OBSERVATION" if chart else "NO_COMPARABLE_SERIES",
        "caveats": [
            "Keine gemessene Entwicklung oder Wirkung ableiten.",
            "Zeit-/Kennzahldefinition und Originalrechte nicht unabhängig qualifiziert.",
        ],
        "human_validated": False,
    }
    spec["sha256"] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    return spec
