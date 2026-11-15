"""Prepare source-checked provisional field slots before method comparisons."""

import argparse
import json
from pathlib import Path

from bs4 import BeautifulSoup

from research.climate_policy import store
from research.offline_evaluation.contracts import require


def prepare(out):
    require(not out.exists(), "Use absent benchmark directory")
    out.mkdir(parents=True)
    units = [json.loads(line) for line in (store.EXPORT / "corpus.jsonl").read_bytes().splitlines()]
    tasks = []
    checks = {}

    def reference(url_part, needle):
        hits = [u for u in units if url_part in u["source_url"] and needle in u["text"]]
        require(hits, "No authentic reference for " + needle)
        # Full literal duplicates only; no grouping generic equal numbers across assets.
        shortest = min(hits, key=lambda u: (len(u["text"]), u["unit_id"]))["text"]
        hits = [u for u in hits if u["text"] == shortest]
        # Freeze the smallest containing canonical statement; preserve exact-text source aliases.
        for u in hits:
            if u["unit_id"] in checks:
                continue
            raw = store.EXPORT / "raw" / u["raw_hashes"][0]
            require(store.sha(raw) == u["raw_hashes"][0], "Raw drift")
            if u["page_ref"]:
                from crawler.core.models import FetchResult
                from crawler.core.parsers.pdf_parser import parse_pdf

                parsed = parse_pdf(
                    FetchResult(u["source_url"], 200, "application/pdf", raw.read_bytes(), {}),
                    u["source_url"],
                )
                require(
                    any(u["text"] == s.text for s in parsed.segments), "PDF source replay mismatch"
                )
                origin = {"kind": "original_pdf_page", "page": u["page_ref"]}
            else:
                soup = BeautifulSoup(raw.read_bytes(), "lxml")
                norm = " ".join(u["text"].split())
                candidates = [
                    n
                    for n in soup.find_all(["p", "li", "div", "h1", "h2", "h3"])
                    if norm in " ".join(n.get_text(" ", strip=True).split())
                ]
                require(candidates, "No exact normalized original context")
                node = min(candidates, key=lambda n: len(n.get_text(" ", strip=True)))
                from research.experiments.m4a2_segmentation_admission.prepare_controls import (
                    node_path,
                )

                origin = {
                    "kind": "original_dom",
                    "path": node_path(node),
                    "matching_nodes": len(candidates),
                    ("selection"): (
                        "smallest containing original node, target is whole canonical passage"
                    ),
                }
            checks[u["unit_id"]] = {
                "source_sha256": u["raw_hashes"][0],
                "source_url": u["source_url"],
                "unit_id": u["unit_id"],
                "quote": u["text"],
                "start_char": 0,
                "end_char": len(u["text"]),
                "origin": origin,
                "review_kind": "AGENT_ORIGINAL_CHECK_PROVISIONAL_NOT_INDEPENDENT_HUMAN",
            }
        return [u["unit_id"] for u in hits]

    def slot(refs, field, value, uom, role):
        return {
            "reference_units": refs,
            "field": field,
            "value": value,
            "unit": uom,
            "role": role,
            "review_status": "PROVISIONAL_SOURCE_CHECKED_SLOT",
        }

    def add(
        identity,
        family,
        question,
        refs,
        slots,
        exposure="ADDITIONAL_TASK_DEVELOPMENT_EXPOSED",
        guard=None,
    ):
        tasks.append(
            {
                "id": identity,
                "family": family,
                "question": question,
                "known_reference_units": refs,
                "slots": slots,
                "exposure": exposure,
                "guard": guard,
                "independent_test": False,
            }
        )

    school = reference("fahrradabstellanlagen-an-straubings-schulen", "200.000 Euro")
    station = reference("fahrradabstellanlage-bahnhof", "410.000 Euro")
    a = [
        slot(school, "FUNDING_RATE", "85", "percent", "REPORTED_IN_SOURCE"),
        slot(school, "PROJECT_COST", "200000", "EUR", "REPORTED_IN_SOURCE"),
    ]
    b = [
        slot(station, "FUNDING_RATE", "85", "percent", "REPORTED_IN_SOURCE"),
        slot(station, "PROJECT_COST", "410000", "EUR", "REPORTED_IN_SOURCE"),
    ]
    add(
        "T01",
        "finance",
        (
            "Welche Förderung und Gesamtinvestition n"
            "ennt Straubing für Fahrradabstellanlagen"
            " an Schulen?"
        ),
        school,
        a,
    )
    add(
        "T02",
        "finance",
        (
            "Welche Förderquote und Investitionskoste"
            "n nennt Straubing für Fahrradabstellanla"
            "gen am Bahnhof?"
        ),
        station,
        b,
    )
    wind = reference("windkraft.html", "1,55 Mio. kWh")
    add(
        "T03",
        "quantity",
        "Welche jährliche Energieerzeugung pro Windkraftanlage nennt Wildpoldsried?",
        wind,
        [slot(wind, "ENERGY_OR_EMISSION", "1550000", "kWh", "REPORTED_IN_SOURCE")],
    )
    grant = reference("windkraft.html", "Investitionszuschuss vom Land Bayern")
    add(
        "T04",
        "finance",
        "Welchen Investitionszuschuss vom Land Bayern nennt Wildpoldsried für Windkraft?",
        grant,
        [slot(grant, "REPORTED_GRANT", "200000", "DM", "REPORTED_IN_SOURCE")],
    )
    led = reference("energie-einsparen.html", "75 %")
    add(
        "T05",
        "quantity",
        "Welche Reduzierung des Stromverbrauchs nennt Wildpoldsried beim Energieeinsparen?",
        led,
        [slot(led, "PERCENTAGE", "75", "percent", "REPORTED_IN_SOURCE")],
    )
    tree = reference("1000-klimabume", "1.000 neue Bäume")
    add(
        "T06",
        "adaptation",
        "Wie viele neue Klimabäume sollen in Straubing gepflanzt werden?",
        tree,
        [slot(tree, "TREE_TARGET_COUNT", "1000", "count", "ANNOUNCEMENT")],
    )
    add(
        "T07",
        "comparison",
        (
            "Vergleiche Förderquote und Gesamtinvesti"
            "tion der Fahrradabstellanlagen an Schule"
            "n und am Bahnhof Straubing."
        ),
        school + station,
        a + b,
    )
    windintent = reference("wind-photovoltaik", "Der Markt Buttenheim sieht sich")
    add(
        "T08",
        "status_actor",
        "Welche Absicht zur Windkraft und Photovoltaik beschreibt Markt Buttenheim?",
        windintent,
        [],
    )
    white = reference("energieeffizienz", "17.716,26")
    add(
        "T09",
        "forecast",
        (
            "Welche Investitionskosten, Förderung, Ge"
            "rätezahl und erwarteten Einsparungen nen"
            "nt Buttenheim für Weißgeräte?"
        ),
        white,
        [
            slot(white, "PROJECT_COST", "17716.26", "EUR", "REPORTED_IN_SOURCE"),
            slot(white, "REPORTED_GRANT", "7882.00", "EUR", "REPORTED_IN_SOURCE"),
            slot(white, "APPLIANCE_COUNT", "21", "count", "REPORTED_IN_SOURCE"),
            slot(white, "ENERGY_OR_EMISSION", "15745", "kWh", "PROJECTED"),
            slot(white, "ENERGY_OR_EMISSION", "6.3", "Tonnen CO 2", "PROJECTED"),
        ],
        exposure="PILOT_SOURCE_EXPOSED",
    )
    cap = reference("5617_foerderrichtlinie", "maximal 2.000,00")
    valid = reference("5617_foerderrichtlinie", "31.12.2026")
    add(
        "T10",
        "finance_validity",
        (
            "Welche Förderquote, Obergrenzen und Gült"
            "igkeit nennt die Straubinger Begrünungsr"
            "ichtlinie?"
        ),
        cap + valid,
        [
            slot(cap, "FUNDING_RATE", "50", "percent", "NORMATIVE_LIMIT"),
            slot(cap, "FUNDING_CAP", "30.00", "EUR_PER_M2", "NORMATIVE_LIMIT"),
            slot(cap, "FUNDING_CAP", "2000.00", "EUR", "NORMATIVE_LIMIT"),
            slot(valid, "VALIDITY_START", "01.10.2023", None, "SOURCE_VALIDITY"),
            slot(valid, "VALIDITY_END", "31.12.2026", None, "SOURCE_VALIDITY"),
        ],
        exposure="PILOT_SOURCE_EXPOSED",
    )
    baywa = reference("elektroladestationen-1", "BayWa Mobility Charging Innlände")
    add(
        "T11",
        "quantity",
        "Welche Schnellladesäulen und Ladepunkte nennt Rosenheim bei BayWa?",
        baywa,
        [
            slot(baywa, "CHARGING_COLUMNS", "8", "count", "REPORTED_IN_SOURCE"),
            slot(baywa, "CHARGING_POINTS", "16", "count", "REPORTED_IN_SOURCE"),
        ],
        exposure="PILOT_SOURCE_EXPOSED",
    )
    heat = reference("foerderprojekte.html", "Kommunale Wärmeplanung")
    add(
        "T12",
        "unknown_status",
        "Ist der Wärmeplan Wildpoldsried 2026 fertig?",
        heat,
        [],
        exposure="PILOT_SOURCE_EXPOSED",
        guard="current_status_warning",
    )
    add(
        "T13",
        "wrong_municipality",
        "Belegen die BayWa-Ladepunkte die Zuordnung zu Buttenheim?",
        baywa,
        [],
        exposure="PILOT_SOURCE_EXPOSED",
        guard="no_positive_typed_claim",
    )
    add(
        "T14",
        "measurement",
        "Ist die Energieeinsparung in Buttenheim tatsächlich gemessen?",
        white,
        [],
        exposure="PILOT_SOURCE_EXPOSED",
        guard="measurement_warning",
    )
    require(len(tasks) == 14, "Task count")
    payload = {
        "schema_version": "climate-policy-exploratory-tasks/1",
        "status": "FROZEN_BEFORE_METHOD_COMPARISONS",
        "tasks": tasks,
        "source_checks": checks,
        "review_status": "ALL_AGENT_PROVISIONAL_NOT_EXTERNAL_GOLD",
        ("label_scope"): (
            "Finite expected field slots and known an"
            "chors, not exhaustive qrels or semantic "
            "accuracy labels"
        ),
        "held_out_independent_cases": 0,
        ("dependent_case_rule"): (
            "Shared raw versions/exact-paragraph alte"
            "rnatives and same municipalities are not"
            " independent replicates"
        ),
        "methods": ["bm25", "bm25_german", "sql", "tools"],
        "common_access": "all 894 frozen units /57 raw versions; same canonical evidence IDs",
        "budget": {"k": 5, "context_characters": 6000, "paid_calls": 0},
        "hypotheses": {
            ("H1"): (
                "Compound normalization can change known-"
                "anchor capture compared with exact-token"
                " BM25."
            ),
            ("H2"): (
                "Typed SQL can help field queries but can"
                "not recover unsupported extraction/schem"
                "a gaps."
            ),
            "H3": "Bounded lexical+SQL tools may improve evidence bundles but incur query work.",
            ("H4"): (
                "Shared composer preserves provenance and"
                " caution but is not a model-quality comp"
                "arison."
            ),
        },
        "metrics": [
            "known_reference_bundle_capture@5 (not exhaustive Recall@5)",
            "conditional_expected_field_slot_coverage",
            "structural_citation_resolution",
            "predefined_guard_presence (software property, not semantic factual accuracy)",
            "stage time / storage / directly controlled paid API calls",
        ],
        "NOT_ALLOWED_METRICS": [
            "population precision",
            "global retrieval recall",
            "independent answer accuracy",
            "statistical superiority",
        ],
        "not_evaluated": {
            ("semantic"): (
                "No suitable installed encoder stack/text"
                "-model snapshot; only an image CLIP cach"
                "e discovered. No download/provider reque"
                "sted."
            ),
            ("graph"): (
                "No independent relation/path labels or v"
                "erified multi-hop task beyond same-store"
                " joins; no fair GraphRAG answer arm just"
                "ified in this block. Hypothesis remains "
                "open."
            ),
        },
        "code_sha256": {p.name: store.sha(p) for p in Path("research/climate_policy").glob("*.py")},
        "corpus_sha256": store.sha(store.EXPORT / "corpus.jsonl"),
        "catalog_sha256": store.sha(store.EXPORT / "catalog.jsonl"),
    }
    (out / "tasks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        "Frozen",
        len(tasks),
        "tasks,",
        sum(len(t["slots"]) for t in tasks),
        "provisional expected slots,",
        len(checks),
        "source-checked reference units",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    prepare(parser.parse_args().out)
