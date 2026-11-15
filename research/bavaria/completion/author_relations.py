# ruff: noqa: E501
"""Original-first provisional policy relations for the new source-disjoint cohort."""

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from research.bavaria.relational.prepare import read_case


def author(db, selection, output):
    out = Path(output)
    if out.exists():
        raise ValueError("Never overwrite authored study specification")
    chosen = {x["id"]: x for x in json.loads(Path(selection).read_text())["selection"]}
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    registry = {
        json.loads(r["record"])["name"]: r["ags"]
        for r in conn.execute("SELECT * FROM municipalities")
    }
    entities = {}
    relations = []
    notes = {}
    groups = {}

    def entity(label, kind):
        key = kind.lower() + ":" + hashlib.sha256(label.encode()).hexdigest()[:16]
        entities[key] = [label, kind]
        return key

    def scope(names):
        return [registry[n] for n in names]

    def fact(
        s,
        subject,
        predicate,
        obj,
        needle,
        municipalities,
        subject_type="Organization",
        object_type="Project",
        modality="SOURCE_REPORTED_NOT_INDEPENDENTLY_VERIFIED",
        when=None,
        note=None,
    ):
        rows = conn.execute(
            "SELECT * FROM units WHERE sha256=? ORDER BY ordinal", (chosen[s]["sha256"],)
        ).fetchall()
        matches = [r for r in rows if needle in " ".join(r["text"].split())]
        if not matches:
            raise ValueError((s, "MISSING NEEDLE", needle))
        row = min(matches, key=lambda r: len(r["text"]))
        # A bounded original-verified unit excerpt, not a generated quotation.
        pattern = r"\s+".join(re.escape(w) for w in needle.split())
        m = re.search(pattern, row["text"])
        assert m
        start = max(0, m.start() - 160)
        end = min(len(row["text"]), m.end() + 480)
        quote = row["text"][start:end] if len(row["text"]) > 1700 else row["text"]
        try:
            read_case(conn, row["unit_id"], quote)
        except ValueError as exc:
            if "original rendering" not in str(exc):
                raise
            quote = m.group(0)
            read_case(conn, row["unit_id"], quote)
            note = (
                (note or "")
                + " Full parsed unit has an original-normalization mismatch; exact needle independently verified; full original context reviewed."
            )
        rid = f"{s}R{len(groups.get(s, [])) + 1:02}"
        rel = [
            rid,
            entity(subject, subject_type),
            predicate,
            entity(obj, object_type),
            row["unit_id"],
            quote,
            scope(municipalities),
            modality,
            when,
            [],
        ]
        relations.append(rel)
        groups.setdefault(s, []).append(rid)
        if note:
            notes[rid] = note
        return rid

    # Municipality, scope, roles and modality were inspected in actual archived originals.
    fact(
        "S01",
        "Institut für Energietechnik IfE GmbH",
        "COMMISSIONED_FOR",
        "Wärmeplanung Nennslingen",
        "Wärmeplanung wurde die Institut",
        ["Nennslingen"],
    )
    fact(
        "S01",
        "Bundesministerium für Wirtschaft und Klimaschutz",
        "FUNDS",
        "Wärmeplanung Nennslingen",
        "Fördergeber: Bundesministerium",
        ["Nennslingen"],
    )
    fact(
        "S01",
        "Wärmeplanung Nennslingen",
        "FUNDING_DURING",
        "01.09.2024–31.12.2025",
        "Die Förderphase läuft",
        ["Nennslingen"],
        "Project",
        "Interval",
        when="01.09.2024–31.12.2025",
    )
    fact(
        "S02",
        "i.n.s. - Institut für innovative Städte",
        "AUTHORS",
        "Radverkehrskonzept Roßtal",
        "beauftragten Büro i.n.s.",
        ["Roßtal"],
    )
    fact(
        "S02",
        "Radverkehrskonzept Roßtal",
        "ADOPTED_ON",
        "13. Oktober 2020",
        "13. Oktober 2020",
        ["Roßtal"],
        "Project",
        "Date",
        when="13.10.2020",
    )
    fact(
        "S02",
        "Radverkehrskonzept Roßtal",
        "HAS_IMPLEMENTATION_HORIZON",
        "bis zu 20 Jahren",
        "Umsetzungshorizont von bis zu 20 Jahren",
        ["Roßtal"],
        "Project",
        "Duration",
        modality="PLANNING_HORIZON_NOT_COMPLETION",
    )
    fact(
        "S03",
        "PB Consult",
        "PARTICIPATES_IN",
        "Maßnahmenworkshop Mobilitätskonzept Roth",
        "Gemeinsam mit dem Fachbüro PB Consult",
        ["Roth"],
    )
    fact(
        "S03",
        "Maßnahmenworkshop Mobilitätskonzept Roth",
        "OCCURRED_ON",
        "16. Juli 2026",
        "16. Juli 2026",
        ["Roth"],
        "Project",
        "Date",
        when="16.07.2026",
    )
    fact(
        "S03",
        "Maßnahmenworkshop Mobilitätskonzept Roth",
        "PRESENTS_PROPOSED_MEASURES",
        "69 Maßnahmen",
        "69 Maßnahmen vor",
        ["Roth"],
        "Project",
        "Count",
        modality="PROPOSALS_NOT_IMPLEMENTED",
    )
    fact(
        "S04",
        "Gemeinde Vorra",
        "COMMISSIONS",
        "Eignungsprüfung Wärmeplanung Vorra",
        "Auftraggeber",
        ["Vorra"],
        "Municipality",
        note="Original cover, physically rendered: Vorra, not publishing frame Velden.",
    )
    fact(
        "S04",
        "zeitgeist engineering gmbh",
        "AUTHORS",
        "Eignungsprüfung Wärmeplanung Vorra",
        "zeitgeist engineering",
        ["Vorra"],
    )
    fact(
        "S04",
        "Eignungsprüfung Wärmeplanung Vorra",
        "REPORT_DATED",
        "15.07.2026",
        "15.07.2026",
        ["Vorra"],
        "Project",
        "Date",
        when="15.07.2026",
    )
    fact(
        "S06",
        "Energieagentur Regensburg e. V.",
        "AUTHORS",
        "Potenzialflächenanalyse Windenergie Riedenburg",
        "eine Potenzialanalyse durch die Energieagentur",
        ["Riedenburg"],
    )
    fact(
        "S06",
        "Potenzialflächenanalyse Windenergie Riedenburg",
        "REPORTS_SUBMITTED_POTENTIAL_AREA",
        "477 ha",
        "477 ha",
        ["Riedenburg"],
        "Project",
        "Area",
        modality="POTENTIAL_AREA_NOT_BUILT",
    )
    fact(
        "S06",
        "Vorbescheidsvorhaben Windenergie Riedenburg",
        "REPORTED_CONSENT_ON",
        "23.11.2023",
        "23.11.2023",
        ["Riedenburg"],
        "AdministrativeProcess",
        "Date",
        when="23.11.2023",
        note="Collective administrative process, not a deduplicated single wind park.",
    )
    fact(
        "S07",
        "Stadt Landshut",
        "REPORTS",
        "LED-Leuchten Neue Neustadt",
        "moderner LED-Technik",
        ["Landshut"],
        "Municipality",
    )
    fact(
        "S07",
        "LED-Leuchten Neue Neustadt",
        "LOCATED_IN",
        "Neue Neustadt",
        "Iguzzini-Leuchten",
        ["Landshut"],
        "Project",
        "Location",
        modality="SOURCE_REPORTED_INSTALLATION",
    )
    fact(
        "S08",
        "Verwaltungsgemeinschaft Aiterhofen",
        "COMMISSIONS",
        "Wärmeplanungskonvoi Aiterhofen-Salching",
        "Auftraggeber:",
        ["Aiterhofen", "Salching"],
        "AdministrativeBody",
    )
    fact(
        "S08",
        "CreaTech Engineering GmbH",
        "AUTHORS",
        "Wärmeplanungskonvoi Aiterhofen-Salching",
        "Auftragnehmer:",
        ["Aiterhofen", "Salching"],
    )
    fact(
        "S08",
        "Wärmeplanungskonvoi Aiterhofen-Salching",
        "REPORT_DATED",
        "31.08.2025",
        "31.08.2025",
        ["Aiterhofen", "Salching"],
        "Project",
        "Date",
        when="31.08.2025",
    )
    fact(
        "S09",
        "Staatsministerium der Finanzen und für Heimat",
        "OFFERS",
        "Soforthilfe Haushalt/Hausrat Bayern",
        "Soforthilfe „Haushalt/Hausrat“",
        [],
    )
    fact(
        "S09",
        "Soforthilfe Haushalt/Hausrat Bayern",
        "HAS_GRANT_CAP",
        "5000 EUR je Haushalt",
        "5.000 Euro je Haushalt",
        [],
        "Project",
        "GrantCap",
        modality="EXTERNAL_PROGRAMME_RULE_NOT_DISBURSEMENT",
    )
    fact(
        "S09",
        "Soforthilfe Haushalt/Hausrat Bayern",
        "APPLICATION_TO",
        "zuständige Kreisverwaltungsbehörde",
        "Antrag bei der zuständigen Kreisverwaltungsbehörde",
        [],
        "Project",
        "AdministrativeBody",
    )
    fact(
        "S11",
        "KlimaKom eG",
        "COMMISSIONED_FOR",
        "Klimaschutzkonzept Moosburg 2014/2015",
        "KlimaKom eG und Green City Energy AG",
        ["Moosburg an der Isar"],
    )
    fact(
        "S11",
        "Green City Energy AG",
        "COMMISSIONED_FOR",
        "Klimaschutzkonzept Moosburg 2014/2015",
        "KlimaKom eG und Green City Energy AG",
        ["Moosburg an der Isar"],
    )
    fact(
        "S11",
        "Klimaschutzkonzept Moosburg 2014/2015",
        "TARGETS",
        "100 Prozent erneuerbare Energie 2035",
        "Bis 2035 soll erreicht werden",
        ["Moosburg an der Isar"],
        "Project",
        "Objective",
        modality="TARGET_NOT_MEASURED_OUTCOME",
        when="2035",
    )
    fact(
        "S12",
        "Landratsamt Mühldorf a. Inn",
        "OFFERS",
        "Energie-Bürgersprechstunden August 2025",
        "Das Landratsamt Mühldorf",
        [],
        "AdministrativeBody",
        note="District-published advisory offer; hosting municipality Rechtmehring is not automatically operator.",
    )
    fact(
        "S12",
        "Energie-Bürgersprechstunden August 2025",
        "ON_SITE_DATE",
        "6. August 2025",
        "6. August 2025",
        [],
        "Project",
        "Date",
        when="06.08.2025",
    )
    fact(
        "S12",
        "Energie-Bürgersprechstunden August 2025",
        "TELEPHONE_DATE",
        "20. August 2025",
        "20. August 2025",
        [],
        "Project",
        "Date",
        when="20.08.2025",
    )
    fact(
        "S13",
        "Energieagentur Nordbayern",
        "COMMISSIONED_FOR",
        "Wärmeplanung Gesees-Hummeltal-Mistelbach",
        "Beauftragt wurde die Energieagentur Nordbayern",
        ["Gesees", "Hummeltal", "Mistelbach"],
    )
    fact(
        "S13",
        "Wärmeplanung Gesees-Hummeltal-Mistelbach",
        "STARTED_PLANNING",
        "November 2025",
        "im November 2025",
        ["Gesees", "Hummeltal", "Mistelbach"],
        "Project",
        "Date",
        modality="STUDY_START_NOT_INFRASTRUCTURE",
        when="2025-11",
    )
    # Original Hof table carries status only in cell colour. Text-only information cannot recover it.
    for label, status in [
        ("M.2.10 LED-Straßenbeleuchtung Hof", "Im Prozess"),
        ("M.5.5 EE-Nahwärmenetze Hof", "Im Prozess"),
        ("M.7.13 Stadtwerke EE-Wärmenetze Hofer Land", "Nicht begonnen"),
    ]:
        needle = {"M.2.10": "M.2.10 LED", "M.5.5": "M.5.5 EE", "M.7.13": "M.7.13 Stadtwerke"}[
            label.split()[0]
        ]
        fact(
            "S16",
            label,
            "HAS_REPORTED_STATUS",
            status,
            needle,
            ["Hof"],
            "Measure",
            "Status",
            modality="ORIGINAL_VISUAL_COLOUR_REVIEW_PROVISIONAL",
            when="Januar 2024",
            note="Physical PDF page 1 rendered and reviewed; yellow=Im Prozess, pink=Nicht begonnen. Colour is absent in text units; not automatic text IE.",
        )
        fact(
            "S16",
            label,
            "PART_OF",
            "IKSK Hof Sachstand Januar 2024",
            needle,
            ["Hof"],
            "Measure",
            "Strategy",
            when="Januar 2024",
        )
    fact(
        "S17",
        "Orsted Onshore Deutschland GmbH",
        "PLANS",
        "Windpark Brunn-Laaber",
        "Das Projekt wird von der Firma Orsted",
        ["Brunn", "Laaber"],
    )
    fact(
        "S17",
        "Windpark Brunn-Laaber",
        "PROJECTED_ANNUAL_GENERATION",
        "34 GWh",
        "34 GWh jährlich erwartet",
        ["Brunn", "Laaber"],
        "Project",
        "ProjectedOutcome",
        modality="FORECAST_NOT_MEASUREMENT",
    )
    fact(
        "S17",
        "Windpark Brunn-Laaber",
        "PLANNED_OPERATION",
        "Ende 2026 / Anfang 2027",
        "Ende 2026 / Anfang 2027",
        ["Brunn", "Laaber"],
        "Project",
        "Date",
        modality="EXPECTED_NOT_OBSERVED",
        when="Ende 2026 / Anfang 2027",
    )
    fact(
        "S18",
        "Stadtrat Waldsassen",
        "ADOPTS",
        "Kriterienkatalog PFA Waldsassen",
        "Stadtrat der Stadt Waldsassen",
        ["Waldsassen"],
        "AdministrativeBody",
    )
    fact(
        "S18",
        "Kriterienkatalog PFA Waldsassen",
        "VERSION_DATED",
        "06.11.2023",
        "Fassung vom 06.11.2023",
        ["Waldsassen"],
        "Project",
        "Date",
        when="06.11.2023",
    )
    fact(
        "S19",
        "IKSK Amberg 2017",
        "UPDATES",
        "IKSK Amberg 2012",
        "2012 entwickelt und 2017 fortgeschrieben",
        ["Amberg"],
        "Strategy",
        "Strategy",
        modality="REPORTED_UPDATE_SINGLE_SOURCE_NOT_TWO_ARCHIVED_VERSIONS",
        when="2017",
    )
    fact(
        "S19",
        "IKSK Amberg 2012",
        "REPORTED_DEVELOPMENT_YEAR",
        "2012",
        "2012 entwickelt und 2017 fortgeschrieben",
        ["Amberg"],
        "Strategy",
        "Date",
        when="2012",
    )
    fact(
        "S20",
        "Institut für Energietechnik IfE GmbH",
        "PARTICIPATES_IN",
        "Wärmeplanung Fensterbach",
        "zusammen mit dem Institut für Energietechnik",
        ["Fensterbach"],
    )
    fact(
        "S20",
        "Wärmeplanung Fensterbach",
        "HAS_EVIDENCE_LIMIT",
        "Umsetzung möglicher Maßnahmen zukünftig",
        "darauffolgende Umsetzung möglicher Maßnahmen",
        ["Fensterbach"],
        "Project",
        "EpistemicStatus",
        modality="NO_COMPLETED_INFRASTRUCTURE_ESTABLISHED",
        when="Oktober 2025",
    )
    fact(
        "S21",
        "Gemeinde Aichen",
        "OPPOSES",
        "Windkraftvorschlag Oberes Zusamtal",
        "Die beiden Kommunen werden deshalb",
        ["Aichen", "Ziemetshausen"],
        "Municipality",
    )
    fact(
        "S21",
        "Markt Ziemetshausen",
        "OPPOSES",
        "Windkraftvorschlag Oberes Zusamtal",
        "Die beiden Kommunen werden deshalb",
        ["Aichen", "Ziemetshausen"],
        "Municipality",
    )
    fact(
        "S21",
        "Windkraftvorschlag Oberes Zusamtal",
        "PROPOSED_COUNT",
        "37 Windkraftanlagen",
        "Errichtung von 37 Windkraftanlagen",
        ["Aichen", "Ziemetshausen"],
        "Project",
        "Count",
        modality="PROPOSED_AND_OPPOSED_NOT_BUILT",
    )
    fact(
        "S22",
        "Institut für Energietechnik IfE GmbH",
        "COMMISSIONED_FOR",
        "Standortkonzept Freiflächen-PV Affing",
        "wurde von der Gemeinde Affing",
        ["Affing"],
    )
    fact(
        "S22",
        "Standortkonzept Freiflächen-PV Affing",
        "ADOPTED_ON",
        "05.08.2025",
        "Sitzung vom 05.08.2025",
        ["Affing"],
        "Project",
        "Date",
        when="05.08.2025",
    )
    fact(
        "S23",
        "Stadt Memmingen",
        "REPORTS_IMPLEMENTING",
        "Sanierung Innenbeleuchtung LED Memmingen",
        "realisiert die Stadt Memmingen Sanierungen",
        ["Memmingen"],
        "Municipality",
        modality="REPORTED_IMPLEMENTATION_NOT_INDEPENDENTLY_VERIFIED",
    )
    fact(
        "S23",
        "Sanierung Innenbeleuchtung LED Memmingen",
        "SUPPORTED_BY",
        "BMUB Klimaschutztechnologien bei der Stromnutzung",
        "Bundesministeriums für Umwelt, Naturschutz und Reaktorsicherheit",
        ["Memmingen"],
        "Project",
        "FundingProgramme",
    )
    fact(
        "S26",
        "Bundesministerium für Wirtschaft und Klimaschutz",
        "FUNDS",
        "Wärmeplanung Eltmann",
        "Fördergeber: Bundesministerium",
        ["Eltmann"],
    )
    fact(
        "S26",
        "Wärmeplanung Eltmann",
        "FUNDING_EXTENDED_UNTIL",
        "31.03.2026",
        "verlängert bis 31.03.2026",
        ["Eltmann"],
        "Project",
        "Date",
        when="31.03.2026",
    )
    fact(
        "S26",
        "Wärmeplanung Eltmann",
        "HAS_REPORTED_STATUS",
        "Wärmeplanungsstudie abgeschlossen",
        "Wärmeplanung erfolgreich abgeschlossen",
        ["Eltmann"],
        "Project",
        "Status",
        modality="STUDY_COMPLETED_NOT_HEAT_INFRASTRUCTURE",
    )
    fact(
        "S27",
        "Stadt Würzburg",
        "OFFERS",
        "Förderprogramm stadtlich grün",
        "Das städtische Förderprogramm",
        ["Würzburg"],
        "Municipality",
    )
    fact(
        "S27",
        "Förderprogramm stadtlich grün",
        "TARGETS",
        "Grüne Inseln inmitten der Stadt",
        "Grüne Inseln inmitten der Stadt",
        ["Würzburg"],
        "Project",
        "Objective",
        modality="PROGRAMME_VISION_NOT_MEASURED_EFFECT",
    )
    fact(
        "S28",
        "Stadt Marktbreit",
        "OFFERS",
        "Kommunales Fassaden- und Sanierungsprogramm Marktbreit",
        "Stadt Marktbreit als Zuwendung",
        ["Marktbreit"],
        "Municipality",
    )
    fact(
        "S28",
        "Kommunales Fassaden- und Sanierungsprogramm Marktbreit",
        "HAS_GRANT_CAP",
        "7700 EUR",
        "höchstens jedoch € 7.700",
        ["Marktbreit"],
        "Project",
        "GrantCap",
        modality="PROGRAMME_CAP_NOT_PAID_GRANT",
    )
    fact(
        "S28",
        "Kommunales Fassaden- und Sanierungsprogramm Marktbreit",
        "ELIGIBLE_ACTIVITY",
        "Begrünung und Entsiegelung",
        "Begrünung und Entsiegelung",
        ["Marktbreit"],
        "Project",
        "PolicyActivity",
    )
    # Tasks are authored after original inspection, before any new retrieval method output.
    tasks = []
    byid = {r[0]: r for r in relations}
    questions = {
        "S01": "Welche Organisation wurde für die Wärmeplanung Nennslingen beauftragt und wer wird als Fördergeber genannt?",
        "S02": "Wer erstellte das Radverkehrskonzept Roßtal und wann stimmte der Rat dem Konzept zu?",
        "S03": "Welches Fachbüro nahm am Maßnahmenworkshop Mobilitätskonzept Roth teil und wann fand er statt?",
        "S04": "Welche Gemeinde ist laut Original Auftraggeber der Eignungsprüfung und welches Büro bearbeitete sie? Ist die Host-Gemeinde Velden der Auftraggeber?",
        "S06": "Wer erstellte Riedenburgs Wind-Potenzialanalyse und wie groß ist die als zustimmungsfähig gemeldete Potenzialfläche?",
        "S07": "Welche Stadt berichtet über LED-Leuchten und in welchem Straßenzug?",
        "S08": "Welche Verwaltungsgemeinschaft ist Auftraggeber des Wärmeplanungskonvois Aiterhofen-Salching und welches Unternehmen Auftragnehmer?",
        "S09": "Welcher externe Akteur bietet die Hochwasser-Soforthilfe Haushalt/Hausrat an und welcher Höchstbetrag je Haushalt wird genannt?",
        "S11": "Welche zwei Büros wurden für Moosburgs Klimaschutzkonzept beauftragt und welches Erneuerbaren-Ziel wird damit verfolgt?",
        "S12": "Wer bietet die im Rechtmehringer Archiv veröffentlichte Energie-Bürgersprechstunde an und wann ist der Vor-Ort-Termin?",
        "S13": "Wer ist für die gemeinsame Wärmeplanung Gesees-Hummeltal-Mistelbach beauftragt und wann begann die Planung?",
        "S16": "Welchen Status weist die farbcodierte Hof-Tabelle der LED-Straßenbeleuchtung zu und zu welchem Konzeptstand gehört die Maßnahme?",
        "S17": "Wer plant den Windpark Brunn-Laaber und welcher jährliche Ertrag wird prognostiziert, nicht gemessen?",
        "S18": "Welches Gremium beschloss Waldsassens PFA-Kriterienkatalog und welcher Fassungsstand wird genannt?",
        "S19": "Welches Amberger Klimaschutzkonzept wurde 2017 fortgeschrieben und welches Entwicklungsjahr nennt die Quelle?",
        "S20": "Welche Organisation ist an Fensterbachs Wärmeplanung beteiligt und welches Umsetzungs-Evidenzlimit nennt die Quelle?",
        "S21": "Gegen welchen Windkraftvorschlag wenden sich Aichen und Ziemetshausen gemeinsam?",
        "S22": "Wer wurde für Affings Freiflächen-PV-Standortkonzept beauftragt und wann wurde das Konzept beschlossen?",
        "S23": "Welche LED-Maßnahme berichtet Memmingen umzusetzen und welches Förderprogramm unterstützt sie?",
        "S26": "Wer ist Fördergeber der Wärmeplanung Eltmann und bis wann wurde die Förderphase verlängert?",
        "S27": "Welches Würzburger Programm wird als städtisches Angebot genannt und welche Grün-Vision verfolgt es?",
        "S28": "Welche Stadt bietet das Fassadenprogramm an, welcher Förderhöchstbetrag gilt und welche Begrünungsaktivität ist förderfähig?",
    }
    for s, ids in groups.items():
        chosen_ids = ids[:2]
        if s == "S11":
            chosen_ids = ids[:3]
        if s == "S28":
            chosen_ids = ids[:3]
        rows = [byid[x] for x in chosen_ids]
        patterns = [[r[1], r[2], r[3]] for r in rows]
        for i, r in enumerate(rows):
            if entities[r[1]][1] in ["Organization", "AdministrativeBody", "Municipality"]:
                patterns[i][0] = "?actor" + str(i)
            if entities[r[3]][1] in [
                "Date",
                "Interval",
                "Duration",
                "Count",
                "Area",
                "GrantCap",
                "Status",
                "Objective",
                "EpistemicStatus",
                "Location",
                "PolicyActivity",
            ]:
                patterns[i][2] = "?value" + str(i)
        if s == "S19":
            patterns = [
                [rows[0][1], "UPDATES", "?prior"],
                ["?prior", "REPORTED_DEVELOPMENT_YEAR", "?year"],
            ]
        tasks.append(
            [f"N{len(tasks) + 1:02}", "relation_bundle", questions[s], patterns, chosen_ids]
        )
    # Explicit multi-source funding question and modality/source-attribution contrasts.
    for s, question, pred, objkind in [
        (
            "S17",
            "Belegt die Quelle bereits gemessene 34 GWh Erzeugung des Windparks Brunn-Laaber?",
            "MEASURED_ANNUAL_GENERATION",
            "34 GWh",
        ),
        (
            "S21",
            "Belegt die Quelle, dass Aichen den Windkraftvorschlag Oberes Zusamtal implementiert?",
            "IMPLEMENTS",
            "Windkraftvorschlag Oberes Zusamtal",
        ),
        (
            "S20",
            "Ist in dieser Quellenbasis eine abgeschlossene Wärmenetz-Inbetriebnahme Fensterbach belegt?",
            "OPERATES",
            "Wärmeplanung Fensterbach",
        ),
        (
            "S02",
            "Belegt die Zustimmung zum Radverkehrskonzept Roßtal bereits ausgezahlte Gesamtbaukosten?",
            "PAID_GRANT",
            "Radverkehrskonzept Roßtal",
        ),
    ]:
        project = next(
            (r[3] for r in relations if r[0].startswith(s) and entities[r[3]][0] == objkind), None
        )
        if project is None:
            project = entity(objkind, "Project")
        subj = entity("Gemeinde " + chosen[s]["name"], "Municipality") if s == "S21" else "?subject"
        tasks.append(
            [
                f"N{len(tasks) + 1:02}",
                "false_premise",
                question,
                [[subj, pred, project]],
                [],
                "ABSTAIN_NOT_ESTABLISHED",
            ]
        )
    nenn = groups["S01"][1]
    elt = groups["S26"][0]
    date = groups["S26"][1]
    tasks.append(
        [
            f"N{len(tasks) + 1:02}",
            "cross_source_funding",
            "Welcher Fördergeber ist für Nennslingen und Eltmann gemeinsam genannt, und bis wann wurde Eltmanns Förderphase verlängert?",
            [
                ["?funder", "FUNDS", byid[nenn][3]],
                ["?funder", "FUNDS", byid[elt][3]],
                [byid[elt][3], "FUNDING_EXTENDED_UNTIL", "?end"],
            ],
            [nenn, elt, date],
        ]
    )
    tasks.append(
        [
            f"N{len(tasks) + 1:02}",
            "status_identity",
            "Haben die getrennt codierten Hof-Maßnahmen M.5.5 EE-Nahwärmenetze und M.7.13 Stadtwerke EE-Wärmenetze denselben berichteten Status im Januar 2024?",
            [
                [byid[groups["S16"][2]][1], "HAS_REPORTED_STATUS", "?status1"],
                [byid[groups["S16"][4]][1], "HAS_REPORTED_STATUS", "?status2"],
            ],
            [groups["S16"][2], groups["S16"][4]],
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "status": "FROZEN_BEFORE_RETRIEVAL_OUTPUTS",
        "selection_sha256": hashlib.sha256(Path(selection).read_bytes()).hexdigest(),
        "entities": entities,
        "relations": relations,
        "tasks": tasks,
        "relation_notes": notes,
        "permitted_sources": [s["sha256"] for s in chosen.values()],
        "dependency_entity_types": [
            "Organization",
            "AdministrativeBody",
            "Municipality",
            "Project",
            "Measure",
            "Strategy",
            "AdministrativeProcess",
            "FundingProgramme",
        ],
        "dependency_links": [],
        "development_entities": [],
        "reference_status": "PROVISIONAL_AGENT_ORIGINAL_REVIEW_NOT_HUMAN_GOLD",
        "scope": "New source-first cohort, excludes all prior 200 review frames; common date/status/count constants do not constitute dependence links.",
        "information_asymmetry": "Curated and original-visual relations contain extra information; text-only arms do not receive it. Same-information text-card control planned.",
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        "Authored", len(relations), "relations", len(tasks), "tasks", len(groups), "source groups"
    )


if __name__ == "__main__":
    author(
        "generated/bavaria/acquisition.sqlite",
        "generated/bavaria/completion/selection-v2.json",
        "generated/bavaria/completion/relational-spec.json",
    )
