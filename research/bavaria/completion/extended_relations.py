# ruff: noqa: E501
"""Targeted cross-document and temporal extension, retaining all earlier freezes."""

import hashlib
import json
import re
from pathlib import Path

from research.bavaria.federation import source_connection
from research.bavaria.relational.prepare import read_case


def extend(output):
    out = Path(output)
    if out.exists():
        raise ValueError("Extension already frozen")
    root = Path("generated/bavaria/completion")
    spec = json.loads((root / "relational-spec.json").read_text())
    selected = json.loads((root / "selection-followup.json").read_text())
    db = "generated/bavaria/acquisition.sqlite"
    extra = str(root / "temporal/acquisition.sqlite")
    conn = source_connection(db, [extra])
    for ident, url_fragment in [("S33", "2010-2022"), ("S34", "2020_fuer"), ("S35", "2021_fuer")]:
        row = conn.execute(
            "SELECT a.*,d.raw_path,d.raw_bytes FROM associations a JOIN documents d USING(sha256) WHERE a.url LIKE ?",
            ("%" + url_fragment + "%",),
        ).fetchone()
        if not row:
            raise ValueError("Missing temporal original " + ident)
        selected["selection"].append(
            {
                **dict(row),
                "id": ident,
                "name": "Amberg",
                "region": "Oberpfalz",
                "population": None,
                "stratum": "TARGETED_TEMPORAL_ORIGINAL",
            }
        )
    chosen = {s["id"]: s for s in selected["selection"]}
    spec["extra_databases"] = [extra]
    spec["permitted_sources"] = [s["sha256"] for s in chosen.values()]
    spec["budgets"] = {"max_pattern_hops": 5}
    entities = spec["entities"]
    relations = spec["relations"]
    notes = spec["relation_notes"]
    tasks = spec["tasks"]

    def entity(label, kind):
        key = kind.lower() + ":" + hashlib.sha256(label.encode()).hexdigest()[:16]
        entities[key] = [label, kind]
        return key

    def fact(
        source,
        subject,
        pred,
        obj,
        needle,
        subject_type="Project",
        object_type="Quantity",
        modality="REPORTED_NOT_INDEPENDENTLY_VERIFIED",
        year=None,
    ):
        candidates = [
            r
            for r in conn.execute(
                "SELECT * FROM units WHERE sha256=? ORDER BY ordinal", (chosen[source]["sha256"],)
            )
            if needle in " ".join(r["text"].split())
        ]
        if not candidates:
            raise ValueError((source, needle))
        row = min(candidates, key=lambda x: len(x["text"]))
        m = re.search(r"\s+".join(re.escape(w) for w in needle.split()), row["text"])
        assert m
        quote = (
            row["text"][max(0, m.start() - 180) : min(len(row["text"]), m.end() + 450)]
            if len(row["text"]) > 1700
            else row["text"]
        )
        try:
            read_case(conn, row["unit_id"], quote)
        except ValueError:
            quote = m.group(0)
            read_case(conn, row["unit_id"], quote)
        number = sum(r[0].startswith(source + "X") for r in relations) + 1
        rid = source + "X" + str(number).zfill(2)
        # Scope comes from inspected report sections, not hosting AGS.
        municipalities = (
            []
            if source == "S30"
            else ["09361000"]
            if source in ["S32", "S33", "S34", "S35"]
            else [chosen[source]["ags"]]
        )
        relations.append(
            [
                rid,
                entity(subject, subject_type),
                pred,
                entity(obj, object_type),
                row["unit_id"],
                quote,
                municipalities,
                modality,
                year,
                [],
            ]
        )
        notes[rid] = (
            "Original-first provisional; report reference year is not publication/retrieval date. Scope and method must remain explicit."
        )
        return rid

    ros1 = fact(
        "S29",
        "Klimaschutzinformation Roßtal",
        "CREDITS",
        "Umweltreferent Roßtal",
        "Ein Projekt des Umweltreferenten",
        "PolicyDocument",
        "Actor",
    )
    ros2 = fact(
        "S29",
        "Klimaschutzinformation Roßtal",
        "CREDITS",
        "Agenda 21 Roßtal",
        "der Agenda 21 in Roßtal",
        "PolicyDocument",
        "Organization",
    )
    mos1 = fact(
        "S30",
        "Bader Energie GmbH",
        "NAMED_CONTACT_FOR",
        "Nahwärmeinformation auf Moosburger Website nicht städtisch",
        "Nahwärme - Fa. Bader Energie GmbH",
        "Organization",
        "PolicyDocument",
    )
    mos2 = fact(
        "S30",
        "Nahwärmeinformation auf Moosburger Website nicht städtisch",
        "HAS_EVIDENCE_LIMIT",
        "ausdrücklich nicht städtisch",
        "Versorgung Nahwärme (nicht städtisch!)",
        "PolicyDocument",
        "EpistemicStatus",
    )
    br1 = fact(
        "S31",
        "Institut für Energietechnik IfE GmbH",
        "PARTICIPATES_IN",
        "Wärmeplanung Brunn",
        "mit dem Institut für Energietechnik",
        "Organization",
        "Project",
    )
    br2 = fact(
        "S31",
        "Wärmeplanung Brunn",
        "STARTED_PLANNING",
        "März 2026",
        "im März 2026 begonnen",
        "Project",
        "Date",
        "STUDY_START_NOT_INFRASTRUCTURE",
        "2026-03",
    )
    fact(
        "S31",
        "Wärmeplanung Brunn",
        "EXPECTED_REPORT",
        "Herbst 2026",
        "soll im Herbst 2026 vorliegen",
        "Project",
        "Date",
        "EXPECTED_NOT_COMPLETED",
        "Herbst 2026",
    )
    listed = []
    for year in [2020, 2021, 2022]:
        listed.append(
            fact(
                "S32",
                "Berichteindex Amberg",
                "LISTS",
                "Klimaschutzbericht " + str(year),
                "Klimaschutzbericht " + str(year),
                "PolicyDocument",
                "PolicyDocument",
                year=str(year),
            )
        )
    # Normalized as-published indicator observations retain source version, scope and units.
    values = {
        ("S34", 2020): [
            ("Kommunaler Strombedarf", "4 GWh", "auf 4 GWh"),
            ("Kommunaler Wärmebedarf", "12.7 GWh", "rund 12,7 GWh"),
            ("Gesamtstädtischer Strombedarf", "257 GWh", "auf 257 GWh"),
            ("Territoriale energiebedingte THG", "418000 t THG", "418.000 t THG"),
        ],
        ("S35", 2021): [
            ("Kommunaler Strombedarf", "4 GWh", "auf 4.000.000 kWh"),
            ("Kommunaler Wärmebedarf", "15.3 GWh", "15.300.000 kWh"),
            ("Gesamtstädtischer Strombedarf", "266 GWh", "266.000.000 kWh"),
            ("Territoriale energiebedingte THG", "455000 t THG", "455.000 t THG"),
        ],
        ("S33", 2022): [
            ("Kommunaler Strombedarf", "4 GWh", "rund 4.000.000 kWh"),
            ("Kommunaler Wärmebedarf", "13.2 GWh", "13.200.000 kWh"),
            ("Gesamtstädtischer Strombedarf", "268 GWh", "268.000.000 kWh"),
            ("Territoriale energiebedingte THG", "427000 t THG", "427.000 t THG"),
        ],
    }
    observations = {}
    for (source, year), fields in values.items():
        for indicator, value, needle in fields:
            label = indicator + " Amberg " + str(year) + " berichtet " + source
            rid1 = fact(
                source,
                label,
                "HAS_REPORTED_VALUE",
                value,
                needle,
                "ReportedOutcome",
                "Quantity",
                "REPORTED_INVENTORY_ESTIMATE_NOT_INDEPENDENT_MEASUREMENT",
                str(year),
            )
            rid2 = fact(
                source,
                label,
                "INSTANCE_OF",
                indicator + " Amberg",
                needle,
                "ReportedOutcome",
                "Indicator",
                "NORMALIZED_SERIES_IDENTITY_PROVISIONAL",
                str(year),
            )
            rid3 = fact(
                source,
                label,
                "DATA_REFERENCE_YEAR",
                str(year),
                needle,
                "ReportedOutcome",
                "Date",
                "DATA_YEAR_NOT_PUBLICATION_DATE",
                str(year),
            )
            observations[indicator, year] = (label, [rid1, rid2, rid3])
    historical = fact(
        "S35",
        "Gesamtstädtischer Strombedarf Amberg 2020 berichtet S35",
        "HAS_REPORTED_VALUE",
        "rund 260 GWh",
        "rund 260.000.000 kWh",
        "ReportedOutcome",
        "Quantity",
        "ROUNDED_HISTORICAL_VALUE_POSSIBLE_REVISION_NOT_AUTOMATIC_CONTRADICTION",
        "2020",
    )
    records = {r[0]: r for r in relations}

    def task(family, question, patterns, required, negative=False):
        qid = "X" + str(sum(t[0].startswith("X") for t in tasks) + 1).zfill(2)
        tasks.append(
            [qid, family, question, patterns, required]
            + (["ABSTAIN_NOT_ESTABLISHED"] if negative else [])
        )

    def pattern(rid, subject=None, obj=None):
        r = records[rid]
        return [subject or r[1], r[2], obj or r[3]]

    def old_by_source(s):
        return [r[0] for r in relations if r[0].startswith(s + "R")]

    ros = old_by_source("S02")[0]
    task(
        "cross_document_roles",
        "Wer erstellte Roßtals Radverkehrskonzept und welche Akteure nennt die separate Klimaschutzinformation als Beteiligte?",
        [pattern(ros, subject="?consultant"), pattern(ros1, obj="?information_actor")],
        [ros, ros1, ros2],
    )
    mo = old_by_source("S11")
    task(
        "cross_document_scope",
        "Welche Büros und welches Ziel nennt Moosburgs Energiewendeseite, und welcher ausdrücklich nicht städtische Nahwärmekontakt steht auf der separaten Versorgungsseite?",
        [
            pattern(mo[0], subject="?consultant"),
            pattern(mo[2], obj="?target"),
            pattern(mos1, subject="?contact"),
        ],
        [*mo, mos1, mos2],
    )
    wind = old_by_source("S17")[0]
    task(
        "project_identity",
        "Wer plant den Windpark Brunn-Laaber und wer arbeitet an der getrennten Wärmeplanung Brunn mit, deren Beginn wann berichtet wird?",
        [
            pattern(wind, subject="?wind_actor"),
            pattern(br1, subject="?heat_actor"),
            pattern(br2, obj="?start"),
        ],
        [wind, br1, br2],
    )
    oldam = old_by_source("S19")[0]
    task(
        "cross_document_history",
        "Welche ältere Konzeptversion wurde in Amberg fortgeschrieben und welche Berichtsjahre nennt der separate Klimaschutzberichte-Index?",
        [pattern(oldam, obj="?prior"), pattern(listed[0], obj="?listed_report")],
        [oldam, *listed],
    )
    for indicator in [
        "Kommunaler Wärmebedarf",
        "Kommunaler Strombedarf",
        "Territoriale energiebedingte THG",
        "Gesamtstädtischer Strombedarf",
    ]:
        a, ar = observations[indicator, 2021]
        b, br = observations[indicator, 2022]
        task(
            "temporal_comparison",
            f"Welche Werte berichtet Amberg für {indicator} in den Berichten 2021 und 2022, bei gleicher benannter Reihe, und ist daraus allein ein kausaler Maßnahmeneffekt belegt?",
            [
                pattern(ar[1], obj="?same_indicator"),
                pattern(br[1], obj="?same_indicator"),
                pattern(ar[0], obj="?value2021"),
                pattern(br[0], obj="?value2022"),
            ],
            [ar[0], ar[1], br[0], br[1]],
        )
    first = observations["Gesamtstädtischer Strombedarf", 2020][1][0]
    task(
        "reported_value_variation",
        "Welche unterschiedlich gerundeten Werte für Ambergs Stromverbrauch 2020 nennen der Bericht 2020 und der Bericht 2021? Ist damit ein fachlicher Widerspruch bewiesen?",
        [pattern(first, obj="?published2020"), pattern(historical, obj="?published2021")],
        [first, historical],
    )
    municipal = observations["Kommunaler Wärmebedarf", 2022][1][1]
    city = observations["Territoriale energiebedingte THG", 2022][1][1]
    task(
        "incompatible_scope_negative",
        "Sind kommunaler Wärmebedarf und gesamtstädtische THG-Bilanz dieselbe Indikatorreihe, die ohne weitere Prüfung zusammengerechnet werden darf?",
        [pattern(municipal, obj="?same_indicator"), pattern(city, obj="?same_indicator")],
        [],
        True,
    )
    # Preserve original scope controls and predeclared original corpus; all new tasks exploratory.
    spec["extension_status"] = "FROZEN_BEFORE_EXTENDED_METHOD_OUTPUTS_AFTER_INITIAL_DEVELOPMENT"
    spec["extension_rationale"] = (
        "Four archived followups plus three named original reports; genuine cross-document roles, identity, time and scope. No broad crawl."
    )
    spec["reference_limits"] = (
        "All original-first agent references remain provisional. Published inventories are estimates; no independent human Gold, causal effects or automatically adjudicated contradictions."
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    (root / "selection-extended.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n"
    )
    conn.close()
    print(
        "Extended frozen relations", len(relations), "tasks", len(tasks), "originals", len(chosen)
    )


if __name__ == "__main__":
    extend("generated/bavaria/completion/relational-spec-extended.json")
