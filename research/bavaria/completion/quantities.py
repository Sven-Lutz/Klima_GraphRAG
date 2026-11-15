# ruff: noqa: E501
"""Generic unit/section/year binding on the three source-versioned Amberg reports.

References are provisional development labels. This diagnostic does not claim
population IE accuracy or infer measured/causal effects from reported inventories.
"""

import hashlib
import json
import re
import sqlite3
import types
from decimal import Decimal
from pathlib import Path

from research.climate_policy import store

NUMBER = r"\d+(?:\.\d{3})*(?:,\d+)?"
QUANTITY = re.compile(r"(?P<n>" + NUMBER + r")\s*(?P<u>GWh|MWh|kWh|t\s*THG)\b", re.I)
SCALES = {"gwh": Decimal(1), "mwh": Decimal(".001"), "kwh": Decimal(".000001")}


def candidates(text, report_year):
    rows = []
    for m in QUANTITY.finditer(text):
        before = text[max(0, m.start() - 260) : m.start()]
        near = before[-100:]
        # Section scope is a typed evidence condition, never inherited from hostname.
        scope = (
            "municipal_properties"
            if "Kommunale Liegenschaften" in text[:160]
            else "territory"
            if re.search("Gesamtstadt|Treibhausgasbilanz", text[:160])
            else "UNKNOWN"
        )
        if scope == "UNKNOWN":
            continue
        unit = m["u"].casefold().replace(" ", "")
        value = Decimal(m["n"].replace(".", "").replace(",", "."))
        if unit in SCALES:
            value *= SCALES[unit]
            uom = "GWh"
        else:
            uom = "tTHG"
        role = "REPORTED_INVENTORY_VALUE"
        year = str(report_year)
        if re.search(r"(?:Minus|Plus|Differenz|Mehrverbräuche|\bum\s|bzw\.)", near, re.I):
            role = "DELTA_OR_COMPARISON"
        if re.search(r"LED|Einsparung", near, re.I):
            role = "REPORTED_SAVINGS_ATTRIBUTION"
        baseline = re.search(r"seit\s+(20\d{2})\s+von\s*$", near, re.I)
        if baseline:
            year = baseline[1]
            role = "HISTORICAL_BASELINE"
        temporal = list(
            re.finditer(
                r"(?:Niveau von|im Jahr|\bin)\s+(20\d{2})\b|\b(20\d{2})\s+mit\s+(?:rund\s+)?$",
                near,
                re.I,
            )
        )
        if temporal:
            year = next(x for x in temporal[-1].groups() if x)
        if uom == "tTHG":
            indicator = "territorial_ghg" if scope == "territory" else "property_ghg"
        else:
            clues = [
                (hit.start(), name)
                for name, pattern in [
                    ("electricity", r"Stromverbrauch|Strombedarf"),
                    ("heat", r"Wärmebedarf|Wärmeverbrauch"),
                ]
                for hit in re.finditer(pattern, before, re.I)
            ]
            indicator = max(clues)[1] if clues else "UNKNOWN"
            indicator = (
                "municipal_" if scope == "municipal_properties" else "territorial_"
            ) + indicator
        rows.append(
            {
                "value": str(value.normalize()),
                "unit": uom,
                "scope": scope,
                "reference_year": year,
                "indicator": indicator,
                "epistemic_role": role,
                "quote": m.group(0),
                "start": m.start(),
                "end": m.end(),
            }
        )
    return rows


def run():
    root = Path("generated/bavaria/completion")
    out = root / "quantity-ie01"
    if out.exists():
        raise ValueError("Do not overwrite numerical study")
    out.mkdir()
    # 3 as-published versions x 4 explicitly scoped indicators, all source-verified.
    references = {
        2020: ["4", "12.7", "257", "418000"],
        2021: ["4", "15.3", "266", "455000"],
        2022: ["4", "13.2", "268", "427000"],
    }
    indicators = [
        "municipal_electricity",
        "municipal_heat",
        "territorial_electricity",
        "territorial_ghg",
    ]
    (out / "FREEZE.json").write_text(
        json.dumps(
            {
                "status": "FROZEN_BEFORE_RULE_OUTPUTS_DEVELOPMENT_EXPOSED",
                "references": references,
                "indicators": indicators,
                "cluster": "Amberg",
                "source_scope": "Three original municipal reports; no independent labels or source-disjoint test",
                "hypothesis": "Unit completeness and local scope/year binding are separate from mention detection.",
                "existing_rules_sha256": hashlib.sha256(
                    Path(store.__file__).read_bytes()
                ).hexdigest(),
                "new_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    module = types.ModuleType("isolated_amberg_rules")
    module.__file__ = store.__file__
    exec(Path(store.__file__).read_text(), module.__dict__)
    module.MUNICIPALITIES = {"09361000": "Amberg"}
    with sqlite3.connect(root / "temporal/acquisition.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        results = []
        all_candidates = []
        legacy = []
        for year, fragment in [(2020, "2020_fuer"), (2021, "2021_fuer"), (2022, "2010-2022")]:
            sha, url = conn.execute(
                "select sha256,url from associations where url like ?", ("%" + fragment + "%",)
            ).fetchone()
            units = [
                dict(r)
                for r in conn.execute("select * from units where sha256=? order by ordinal", (sha,))
            ]
            modern = []
            old = []
            for unit in units:
                modern.extend(
                    {
                        **r,
                        "source_sha256": sha,
                        "unit_id": unit["unit_id"],
                        "physical_page": unit["page_ref"],
                    }
                    for r in candidates(unit["text"], year)
                )
                old.extend(
                    module.extract(
                        {
                            "unit_id": unit["unit_id"],
                            "text": unit["text"],
                            "heading_context": unit["heading"],
                            "page_ref": unit["page_ref"],
                            "acquisition_ags": "09361000",
                            "raw_hashes": [sha],
                            "source_url": url,
                        }
                    )
                )
            all_candidates += modern
            legacy += old
            for indicator, value in zip(indicators, references[year]):
                expected = {
                    "value": str(Decimal(value).normalize()),
                    "unit": "tTHG" if indicator == "territorial_ghg" else "GWh",
                    "scope": "municipal_properties"
                    if indicator.startswith("municipal")
                    else "territory",
                    "reference_year": str(year),
                }
                found = [
                    r
                    for r in modern
                    if r["indicator"] == indicator
                    and r["reference_year"] == str(year)
                    and r["epistemic_role"] == "REPORTED_INVENTORY_VALUE"
                ]
                unique = {(r["value"], r["unit"]) for r in found}
                predicted = found[0] if len(unique) == 1 else None
                legacy_values = []
                for r in old:
                    if r["field"] != "ENERGY_OR_EMISSION":
                        continue
                    unit = r["unit"].casefold()
                    if unit in SCALES:
                        legacy_values.append(
                            (
                                str((Decimal(r["value"]) * SCALES[unit]).normalize()),
                                "GWh",
                                r["role"],
                            )
                        )
                results.append(
                    {
                        "year": year,
                        "indicator": indicator,
                        "reference": expected,
                        "prediction": predicted,
                        "normalized_joint_correct": bool(predicted)
                        and all(predicted[k] == v for k, v in expected.items()),
                        "legacy_known_value_covered": any(
                            v == expected["value"] and u == expected["unit"]
                            for v, u, _ in legacy_values
                        ),
                        "legacy_full_binding_supported": False,
                        "legacy_note": "Existing extractor has no source-year/section-scope output contract; candidate coverage is not targeted F1.",
                        "source_sha256": sha,
                        "source_url": url,
                        "candidate_ambiguity": len(unique) > 1,
                    }
                )
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    )
    (out / "candidates.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_candidates)
    )
    (out / "legacy.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in legacy)
    )
    report = {
        "references": len(results),
        "source_versions": 3,
        "municipality_clusters": 1,
        "legacy_known_value_coverage": sum(r["legacy_known_value_covered"] for r in results),
        "normalized_joint_binding_correct": sum(r["normalized_joint_correct"] for r in results),
        "ambiguous_abstentions": sum(r["candidate_ambiguity"] for r in results),
        "precision_recall_f1": None,
        "reason": "Development diagnostic, provisional requested-field references, incomplete open extraction inventory; no independent quality or population inference.",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
