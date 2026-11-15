# ruff: noqa: E501
"""Provisional original-first qualification of the frozen 28-source enriched sample."""

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

# relevance, publisher/scope, exact original cue, time qualification.
REVIEWS = {
    "S01": (
        "LOCAL_STUDY",
        "Nennslingen",
        "Förderphase läuft",
        "FUNDING_INTERVAL_NOT_IMPLEMENTATION",
    ),
    "S02": (
        "LOCAL_MOBILITY_STRATEGY",
        "Roßtal",
        "13. Oktober 2020",
        "ADOPTION_2020_NOT_FULL_EXECUTION",
    ),
    "S03": (
        "PROPOSED_MOBILITY_MEASURES",
        "Roth",
        "69 Maßnahmen",
        "WORKSHOP_JULY_2026_NOT_69_IMPLEMENTED_PROJECTS",
    ),
    "S04": (
        "OTHER_MUNICIPALITY_STUDY",
        "Vorra",
        "Gemeinde Vorra",
        "REPORT_15_JULY_2026_HOST_FRAME_IS_VELDEN",
    ),
    "S05": (
        "NO_QUALIFIED_POLICY_IN_SELECTED_HOMEPAGE",
        "UNKNOWN",
        "Datenschutzbeauftragten",
        "UNKNOWN",
    ),
    "S06": (
        "LOCAL_WIND_PLANNING",
        "Riedenburg",
        "477 ha",
        "POTENTIAL_AREA_AND_CONSENT_NOT_BUILT_CAPACITY",
    ),
    "S07": (
        "CLIMATE_ADJACENT_URBAN_LED_REPORT",
        "Landshut",
        "moderner LED-Technik",
        "REPORTED_INSTALLATION_DATE_UNKNOWN_NO_MEASURED_SAVINGS",
    ),
    "S08": (
        "JOINT_MUNICIPAL_STUDY",
        "Aiterhofen|Salching",
        "31.08.2025",
        "REPORT_DATE_2025_JOINT_SCOPE",
    ),
    "S09": (
        "EXTERNAL_DISASTER_ASSISTANCE",
        "AFFECTED_HOUSEHOLDS_BAVARIA",
        "5.000 Euro je Haushalt",
        "PROGRAMME_CAP_NOT_LOCAL_PAYMENT_OR_CLIMATE_CAUSATION",
    ),
    "S10": (
        "NO_QUALIFIED_POLICY_IN_SELECTED_HOMEPAGE",
        "UNKNOWN",
        "Aktionsplans für Inklusion",
        "HOMEPAGE_DATES_NOT_CLIMATE_EVENT_DATES",
    ),
    "S11": (
        "LOCAL_STRATEGY_AND_TARGET",
        "Moosburg an der Isar",
        "Bis 2035 soll erreicht werden",
        "FUTURE_TARGET_NOT_OBSERVED_RENEWABLE_SHARE",
    ),
    "S12": (
        "DISTRICT_ENERGY_ADVICE",
        "DISTRICT_MUEHLDORF",
        "Landratsamt Mühldorf",
        "JULY_PRESS_RELEASE_AUGUST_2025_ADVISORY_DATES",
    ),
    "S13": (
        "LIMITED_SUBSTANTIVE_STUDY_TEASER",
        "Gesees|Hummeltal|Mistelbach",
        "November 2025",
        "PLANNING_START_AND_REPORT_LINKS_NO_CURRENT_COMPLETION_PROOF",
    ),
    "S14": (
        "TRANSPORT_INFORMATION_NO_OPERATOR_ASSIGNMENT",
        "REGIONAL_TRANSPORT",
        "keine Verantwortung",
        "TIMETABLE_LINKS_NOT_PROOF_CITY_OPERATES_OR_FINANCES_SERVICE",
    ),
    "S15": (
        "GENERAL_BUSINESS_GRANTS_NO_EXPLICIT_CLIMATE_LINK",
        "REGIONAL_BUSINESS_ADVICE",
        "Existenzgründer",
        "NO_QUALIFIED_CLIMATE_GRANT_OR_PAYMENT",
    ),
    "S16": (
        "LOCAL_VISUAL_STATUS_MATRIX",
        "Hof",
        "Sachstand Januar 2024",
        "COLOUR_STATUS_HISTORICAL_NOT_CURRENT",
    ),
    "S17": (
        "PLANNED_ENERGY_PROJECTS_AND_FORECAST",
        "Brunn|Laaber",
        "34 GWh jährlich erwartet",
        "EXPECTED_OUTPUT_NOT_MEASURED; SOLAR_AND_WIND_DISTINCT",
    ),
    "S18": (
        "LOCAL_SPATIAL_PLANNING_INSTRUMENT",
        "Waldsassen",
        "Fassung vom 06.11.2023",
        "CRITERIA_VERSION_NOT_INSTALLED_PV",
    ),
    "S19": (
        "REPORTED_STRATEGY_UPDATE",
        "Amberg",
        "2012 entwickelt und 2017 fortgeschrieben",
        "SINGLE_SOURCE_REPORTED_HISTORY_NOT_TWO_ARCHIVED_VERSIONS",
    ),
    "S20": (
        "LOCAL_STUDY_WITH_EXPLICIT_EVIDENCE_LIMIT",
        "Fensterbach",
        "darauffolgende Umsetzung möglicher Maßnahmen",
        "OCTOBER_2025_FUTURE_IMPLEMENTATION",
    ),
    "S21": (
        "INTERMUNICIPAL_POLITICAL_OPPOSITION",
        "Aichen|Ziemetshausen",
        "37 Windkraftanlagen",
        "OPPOSED_PROPOSAL_NOT_MUNICIPAL_IMPLEMENTATION",
    ),
    "S22": (
        "LOCAL_PV_SITING_CRITERIA",
        "Affing",
        "Sitzung vom 05.08.2025",
        "ADOPTED_SITING_RULE_NOT_PV_INSTALLATION",
    ),
    "S23": (
        "REPORTED_LED_IMPLEMENTATION",
        "Memmingen",
        "realisiert die Stadt Memmingen Sanierungen",
        "IMPLEMENTATION_REPORTED_NO_COMPLETION_DATE_OR_MEASURED_EFFECT",
    ),
    "S24": (
        "LAND_ALLOCATION_FORM_NO_CLIMATE_GRANT_AWARD",
        "UNKNOWN",
        "Antragsbogen",
        "FORM_NOT_ACTUAL_AWARD_OR_PAYMENT",
    ),
    "S25": (
        "GREEN_MAINTENANCE_VACANCY_NO_VERIFIED_CLIMATE_MEASURE",
        "UNKNOWN",
        "Grünpflege",
        "MAINTENANCE_JOB_NOT_CLIMATE_OUTCOME",
    ),
    "S26": (
        "LOCAL_STUDY_FUNDING_AND_COMPLETION",
        "Eltmann",
        "Wärmeplanung erfolgreich abgeschlossen",
        "STUDY_COMPLETE_NOT_HEAT_NETWORK_COMMISSIONING",
    ),
    "S27": (
        "NAMED_PROGRAMME_DISCOVERY_TEASER",
        "Würzburg",
        "Grüne Inseln inmitten der Stadt",
        "PROGRAMME_VISION_NO_CURRENT_RULES_OR_OUTCOMES",
    ),
    "S28": (
        "HERITAGE_PROGRAMME_WITH_GREENING_DESEALING",
        "Marktbreit",
        "Begrünung und Entsiegelung",
        "2015_RULES_CAP_NOT_PAID_SUBSIDY_OR_CURRENT_ELIGIBILITY",
    ),
}


def run(db, selection, views, output):
    out = Path(output)
    if out.exists():
        raise ValueError("Never overwrite a completed original audit")
    out.mkdir(parents=True)
    frozen = json.loads(Path(selection).read_text())
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    records = {
        json.loads(x[0])["name"]: json.loads(x[0])
        for x in conn.execute("SELECT record FROM municipalities")
    }
    rows = []
    for s in frozen["selection"]:
        relevance, scope, cue, time_status = REVIEWS[s["id"]]
        d = json.loads((Path(views) / (s["id"] + ".json")).read_text())
        original = " ".join(" ".join(p["text"].split()) for p in d["pages"])
        if cue not in original:
            raise ValueError((s["id"], "Original cue missing", cue))
        parsed = " ".join(
            " ".join(x[0].split())
            for x in conn.execute(
                "SELECT text FROM units WHERE sha256=? ORDER BY ordinal", (s["sha256"],)
            )
        )
        scope_ags = [records[n]["ags"] for n in scope.split("|") if n in records]
        rows.append(
            {
                "id": s["id"],
                "source_sha256": s["sha256"],
                "frame_ags": s["ags"],
                "original_url": s["url"],
                "region": s["region"],
                "stratum": s["stratum"],
                "document_format": d["format"],
                "publisher_authority": "SELF_IDENTIFIED_DISTRICT_ON_MUNICIPAL_HOST"
                if s["id"] == "S12"
                else "REGISTRY_CANDIDATE_MUNICIPAL_OR_VG_HOST_WITH_ORIGINAL_SELF_IDENTIFICATION",
                "independent_domain_verification": False,
                "policy_relevance": relevance,
                "claim_scope_description": scope,
                "explicit_scope_ags": scope_ags,
                "scope_not_inferred_from_frame": True,
                "document_classification": relevance,
                "temporal_qualification": time_status,
                "current_validity_independently_verified": False,
                "original_cue": cue,
                "original_cue_verified": True,
                "parsed_cue_recovered": cue in parsed,
                "visual_status_preserved_in_text": False if s["id"] == "S16" else None,
                "review_status": "PROVISIONAL_ORIGINAL_FIRST_AGENT_REVIEW_NOT_HUMAN_GOLD",
            }
        )
    conn.close()
    (out / "judgments.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    )
    report = {
        "selection_sha256": hashlib.sha256(Path(selection).read_bytes()).hexdigest(),
        "sample": len(rows),
        "regions": dict(Counter(r["region"] for r in rows)),
        "strata": dict(Counter(r["stratum"] for r in rows)),
        "classification_counts": dict(Counter(r["policy_relevance"] for r in rows)),
        "original_cues_verified": sum(r["original_cue_verified"] for r in rows),
        "parsed_cues_recovered": sum(r["parsed_cue_recovered"] for r in rows),
        "visual_relation_loss_cases": ["S16"],
        "frame_scope_mismatch": ["S04"],
        "external_or_indirect_cases": ["S09", "S12", "S14"],
        "unknown_or_nonqualifying_policy_candidates": ["S05", "S10", "S15", "S24", "S25"],
        "independent_human_judgments": 0,
        "population_precision_or_parser_recall": None,
        "interpretation": "Enriched quota sample; source classification and conditional cue diagnostics only. No population climate-policy coverage or current-policy truth.",
        "review_order_limit": "Originals inspected before relation methods. Cue checks are post-hoc diagnostics, not blind parsing recall.",
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    base = list(
        csv.DictReader(
            Path("research/bavaria/continuation/release/census/municipality_census.csv").open()
        )
    )
    for r in base:
        r["new_sample_originals_reviewed"] = sum(x["frame_ags"] == r["ags"] for x in rows)
        r["new_sample_explicit_scope_originals"] = sum(
            r["ags"] in x["explicit_scope_ags"] for x in rows
        )
        r["independent_human_source_qualification"] = "PENDING"
    with (out / "municipality_quality_overlay.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(base[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(base)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run(
        "generated/bavaria/acquisition.sqlite",
        "generated/bavaria/completion/selection-v2.json",
        "generated/bavaria/completion/original_views",
        "generated/bavaria/completion/quality01",
    )
