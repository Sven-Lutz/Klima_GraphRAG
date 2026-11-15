# ruff: noqa: E501
"""No-inference synthesis of frozen results and explicit provisional claim reviews."""

import csv
import json
from decimal import Decimal
from pathlib import Path

from research.bavaria.continuation.ie_compare import summary
from research.bavaria.relational.build import Components

from .metrics import paired_bootstrap


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def analyze(root):
    root = Path(root)
    out = root / "analysis01"
    if out.exists():
        raise ValueError("Use fresh analysis directory")
    out.mkdir()
    tasks = [
        json.loads(line) for line in (root / "relations02/tasks.jsonl").read_text().splitlines()
    ]
    relations = [
        json.loads(line) for line in (root / "relations02/relations.jsonl").read_text().splitlines()
    ]
    entities = json.loads((root / "relational-spec-extended-v2.json").read_text())["entities"]
    byid = {r["relation_id"]: r for r in relations}
    # Audit fixes event-node component omission for descriptive sensitivity only.
    # No frozen split or unexposed confirmation task is relabelled or promoted.
    components = Components()
    link_types = {
        "Organization",
        "AdministrativeBody",
        "Municipality",
        "Project",
        "Measure",
        "Strategy",
        "AdministrativeProcess",
        "FundingProgramme",
        "ReportedOutcome",
        "Indicator",
        "PolicyDocument",
        "Actor",
    }
    for r in relations:
        values = [
            "source:" + r["source_sha256"],
            *["municipality:" + a for a in r["claim_scope_ags"]],
            *["municipality:" + a for a in r["source_frame_ags"]],
        ]
        values += [v for v in [r["subject_id"], r["object_id"]] if entities[v][1] in link_types]
        components.union(*values)
    task_clusters = {}
    audit = []
    for t in tasks:
        roots = {
            components.find(term)
            for p in t["patterns"]
            for term in [p[0], p[2]]
            if not term.startswith("?") and term in components.parent
        }
        if not roots and t["task_id"] == "N23":
            roots = {components.find("source:" + byid["S17R02"]["source_sha256"])}
        task_clusters[t["task_id"]] = sorted(roots)
        if t["split"] == "quarantine_cross_component":
            audit.append(
                {
                    "task_id": t["task_id"],
                    "frozen_split": t["split"],
                    "descriptive_components": sorted(roots),
                    "reason": "Unique observation/indicator nodes were omitted from original dependency type list; all six temporal tasks bind the Amberg source component. Frozen status untouched; development-exposed only.",
                }
            )
    save(
        out / "component_audit.json",
        {
            "audit": audit,
            "changes_to_frozen_files": False,
            "new_cohort_components": len({c for cs in task_clusters.values() for c in cs}),
            "old_200_confirmation_quarantine": "UNCHANGED",
        },
    )
    raw = [
        json.loads(line) for line in (root / "retrieval03/results.jsonl").read_text().splitlines()
    ]
    taskmap = {t["task_id"]: t for t in tasks}
    scored = []
    for r in raw:
        t = taskmap[r["task_id"]]
        visual = any("VISUAL" in byid[x]["modality"] for x in t["required_relations"])
        scored.append(
            {
                **r,
                "component_ids": task_clusters[t["task_id"]],
                "requires_visual_relation_information": visual,
                "known_bundle_with_required_representation": r["complete_known_bundle"]
                and (
                    not visual
                    or r["context_representation"] == "text_plus_provisional_relation_cards"
                ),
            }
        )
    (out / "retrieval_diagnostics.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in scored)
    )
    methods = sorted({r["method"] for r in scored})
    tables = []
    for method in methods:
        for subset in [
            "frozen_evaluation_positive",
            "all_positive_development",
            "temporal_supplement",
        ]:
            rows = [
                r
                for r in scored
                if r["method"] == method
                and not r["negative_task"]
                and (
                    subset == "all_positive_development"
                    or subset == "frozen_evaluation_positive"
                    and r["split"] == "evaluation_provisional"
                    or subset == "temporal_supplement"
                    and r["task_id"] in ["X05", "X06", "X07", "X08", "X09"]
                )
            ]
            tables.append(
                {
                    "method": method,
                    "subset": subset,
                    "tasks": len(rows),
                    "source_components": len({c for r in rows for c in r["component_ids"]}),
                    "known_bundles": sum(r["complete_known_bundle"] for r in rows),
                    "representation_feasible_known_bundles": sum(
                        r["known_bundle_with_required_representation"] for r in rows
                    ),
                    "known_evidence_occurrences": sum(r["known_required_evidence"] for r in rows),
                    "captured_occurrences": sum(r["captured_required_evidence"] for r in rows),
                }
            )
    with (out / "retrieval_summary.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(tables[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(tables)
    positive = [r for r in scored if not r["negative_task"]]
    bootstrap = {
        b + " minus " + a: paired_bootstrap(positive, a, b)
        for a, b in [
            ("bm25_context", "bm25_same_relation_information"),
            ("sql_relations", "graph_traversal"),
            ("semantic", "semantic_windows"),
        ]
    }
    save(
        out / "conditional_bootstrap.json",
        {
            "status": "POSTHOC_CONDITIONAL_PROVISIONAL_REFERENCE_SENSITIVITY_NOT_POPULATION_INFERENCE",
            "comparisons": bootstrap,
            "note": "Amberg temporal supplement is one dependency component, not five independent tests. Reference labels remain same-agent provisional; no p-values.",
        },
    )
    base = [
        json.loads(line) for line in (root / "stronger-ie01/results.jsonl").read_text().splitlines()
    ]
    ids = {"S09", "S17", "S24", "S05"}
    ab = json.loads((root / "unknown-ablation01/report.json").read_text())
    save(
        out / "unknown_encoding_comparison.json",
        {
            "status": "POSTHOC_FOUR_CASE_DIAGNOSTIC",
            "same_case_ids": sorted(ids),
            "json_null_model": summary(
                [r for r in base if r["method"] == "model" and r["case_id"] in ids]
            ),
            "explicit_UNKNOWN_model": ab["metrics"],
            "qualification": "NOT_PASSED_UNKNOWN_SPECIFICITY; not a confirmatory rescue or isolated grammar-only effect",
        },
    )
    claims = []

    def claim(case, provider, label, supported, citation, source_note, facet=True):
        claims.append(
            {
                "claim_id": case
                + ":"
                + provider
                + ":"
                + str(sum(x["case_id"] == case and x["provider"] == provider for x in claims) + 1),
                "case_id": case,
                "provider": provider,
                "claim": label,
                "supported_in_original": supported,
                "citation_attribution_and_format_correct": citation,
                "source_first_rationale": source_note,
                "requested_facet": facet,
                "reference_status": "PROVISIONAL_AGENT_ORIGINAL_REVIEW_NOT_HUMAN_GOLD",
            }
        )

    for provider in ["workspace_rules", "ollama"]:
        valid = provider == "workspace_rules"
        claim(
            "A01",
            provider,
            "VG Aiterhofen is commissioner",
            True,
            valid,
            "Salching report physical page2: Auftraggeber Verwaltungsgemeinschaft Aiterhofen.",
        )
        claim(
            "A01",
            provider,
            "CreaTech Engineering GmbH is contractor",
            True,
            valid,
            "Same original cover/metadata page2: Auftragnehmer CreaTech.",
        )
        if valid:
            claim(
                "A01",
                provider,
                "Report dated31.08.2025",
                True,
                True,
                "Original physicalpage2 date; extra supported facet.",
                False,
            )
        claim(
            "A02",
            provider,
            "KlimaKom eG commissioned",
            True,
            valid,
            "Moosburg original names both firms, April2014.",
        )
        claim(
            "A02",
            provider,
            "Green City Energy AG commissioned",
            True,
            valid,
            "Same original; historical role only.",
        )
        claim(
            "A02",
            provider,
            "100percent renewable-energy target2035",
            True,
            valid,
            "Target, not achieved share. Model uses invalid B1/B2 labels despite correct content.",
        )
    claim(
        "A04",
        "workspace_rules",
        "Funding extended to31.03.2026",
        True,
        True,
        "Eltmann original explicit extension.",
    )
    claim(
        "A04",
        "workspace_rules",
        "Planning study completed",
        True,
        True,
        "Eltmann reports study complete, not heat infrastructure.",
        False,
    )
    claim(
        "A04",
        "ollama",
        "Stadt Eltmann is funder",
        False,
        False,
        "Original names BMWK; planner omitted FUNDS facet and model guessed city.",
    )
    claim(
        "A04",
        "ollama",
        "Funding extended to31.03.2026",
        True,
        True,
        "Correct date in original and selectedC1; overall answer failed canonical grounding.",
    )
    (out / "answer_claim_reviews.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in claims)
    )
    qa = {
        "status": "PROVISIONAL_SOURCE_FIRST_CLAIM_REVIEW",
        "source_answerable_cases": 4,
        "unsupported_current_or_payment_cases": 2,
        "common_context_characters": 6000,
        "common_output_tokens": 512,
        "identical_selected_evidence_verified": True,
        "model_actual_calls": 3,
        "model_canonical_accepted_answerable": 0,
        "model_semantically_complete_raw_candidates": 2,
        "rules_initial_complete_answerable": 2,
        "rules_after_funding_facet_regression_complete_answerable": 3,
        "false_abstention_case": "A03 original table answerable but context preflight excluded complete proof for both providers.",
        "preflight_correct_abstentions": ["A05", "A06"],
        "abstention_not_attributable_to_model": True,
        "claims_by_provider": {
            p: {
                "assertive_claims": sum(r["provider"] == p for r in claims),
                "supported": sum(r["provider"] == p and r["supported_in_original"] for r in claims),
                "unsupported": sum(
                    r["provider"] == p and not r["supported_in_original"] for r in claims
                ),
            }
            for p in ["workspace_rules", "ollama"]
        },
        "generator_qualification": "FAILED_NO_DEMO_PROMOTION",
        "independent_human_labels": 0,
    }
    save(out / "answer_quality.json", qa)
    temporal = []
    refs = json.loads((root / "quantity-ie01/FREEZE.json").read_text())
    for year, values in refs["references"].items():
        temporal.append(
            {
                "reference_year": int(year),
                "municipal_electricity_GWh": float(values[0]),
                "municipal_heat_GWh": float(values[1]),
                "territorial_grid_electricity_GWh": float(values[2]),
                "territorial_energy_GHG_t": float(values[3]),
                "status": "AS_PUBLISHED_PROVISIONAL_INVENTORY_NOT_CAUSAL_EFFECT",
            }
        )
    with (out / "temporal_inventory.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(temporal[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(temporal)
    save(
        out / "temporal_interpretation.json",
        {
            "municipal_heat_2021_2022_GWh_difference": str(Decimal("13.2") - Decimal("15.3")),
            "percentage_change": float((Decimal("13.2") / Decimal("15.3") - 1) * 100),
            "territorial_GHG_2021_2022_t_difference": 427000 - 455000,
            "causal_attribution": None,
            "limits": [
                "Reported BISKO estimates, updated factors and statistical transport inputs; not independent physical measurements.",
                "Municipal property portfolio is not whole territory; scope and stock may change.",
                "2020 electricity257GWh vs retrospective rounded260GWh is a variation requiring precision/method adjudication, not automatic contradiction.",
                "No current2026 policy performance or cross-municipality ranking.",
            ],
        },
    )
    save(
        out / "REPORT.json",
        {
            "cohort_originals": 35,
            "relations": 108,
            "tasks": 38,
            "true_dependency_components": len({c for cs in task_clusters.values() for c in cs}),
            "representation_effect": "SQL and graph same curated facts and logical patterns; all38 identical relation sets.",
            "information_effect": "BM25 original text versus same query plus provisional relation cards; extra annotation/visual information disclosed.",
            "formal_query_limit": "SQL/graph consume oracle logical patterns; no end-to-end NL planning superiority follows from their gap over text.",
            "source_repair": "One exact original contact fragment restored after parser omission; shared by all extended arms.",
            "independent_human_gold": False,
            "historical_confirmation_200": "UNTOUCHED_QUARANTINED",
            "confidence_claim": "No general architecture winner.",
        },
    )
    print("Analysis complete; no model or network calls")


if __name__ == "__main__":
    analyze("generated/bavaria/completion")
