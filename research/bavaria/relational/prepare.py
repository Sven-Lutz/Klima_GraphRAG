"""Freeze source-first relational cases; no downloads or inference."""

import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup

RESERVE = 15 * 1024**3


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def ident(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def read_case(conn, prefix, excerpt=None):
    rows = conn.execute("SELECT * FROM units WHERE unit_id LIKE ?", (prefix + "%",)).fetchall()
    if len(rows) != 1:
        raise ValueError("Unit prefix not unique: " + prefix)
    row = dict(rows[0])
    source = conn.execute("SELECT * FROM documents WHERE sha256=?", (row["sha256"],)).fetchone()
    associations = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM associations WHERE sha256=? ORDER BY url,ags", (row["sha256"],)
        )
    ]
    path = Path(source["raw_path"])
    body = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    if sha(body) != row["sha256"]:
        raise ValueError("Original hash mismatch")
    quote = excerpt or row["text"]
    start = row["text"].find(quote)
    if start < 0:
        raise ValueError("Quote missing from stored evidence")
    if body.startswith(b"%PDF"):
        proc = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
            input=body,
            capture_output=True,
            timeout=30,
            check=True,
        )
        pages = proc.stdout.decode().split("\f")
        visible = norm(pages[int(row["page_ref"]) - 1])
        locator_type = "physical_pdf_page_normalized_text"
    else:
        soup = BeautifulSoup(body, "lxml")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        visible = norm(soup.get_text(" ", strip=True))
        locator_type = "original_html_visible_text_normalized"
    needle = norm(quote)
    occurrences = [m.start() for m in re.finditer(re.escape(needle), visible)]
    if not occurrences:
        raise ValueError("Quote not found in original rendering: " + prefix)
    return {
        "case_id": prefix,
        "text": quote,
        "evidence_text": row["text"],
        "source_sha256": row["sha256"],
        "unit_id": row["unit_id"],
        "evidence_id": row["unit_id"],
        "source_type": "segment",
        "native_source_type": "pdf" if body.startswith(b"%PDF") else "html",
        "source_id": row["unit_id"],
        "document_id": "source-version:" + row["sha256"],
        "source_url": associations[0]["url"],
        "raw_path": str(path.resolve()),
        "source_frame_ags": sorted({a["ags"] for a in associations}),
        "heading": row["heading"],
        "page_ref": row["page_ref"],
        "quote": quote,
        "start_char": start,
        "end_char": start + len(quote),
        "original_locator": {
            "type": locator_type,
            "page_ref": row["page_ref"],
            "normalization": "whitespace_only_v1",
            "quote_occurrences": occurrences,
            "original_visible_text_sha256": sha(visible.encode()),
            "original_sha256_verified": True,
        },
        "reference_basis": "PROVISIONAL_AGENT_ORIGINAL_REVIEW",
    }


# These cases and field judgments were defined from archived originals before model outputs.
IE_SPECS = [
    (
        "e54bea7c839",
        None,
        {
            "actor": "Bundesministerium für Wirtschaft und Klimaschutz",
            "role": "FUNDER",
            "status": "REPORTED_FUNDING",
            "reference_time": None,
            "amount": "85500",
            "unit": "EUR",
        },
        {
            "actor": "Bundesministerium für Wirtschaft und Klimaschutz",
            "role": "gefördert",
            "status": "wird durch",
            "amount": "85.500, - €",
            "unit": "€",
        },
        "85500 EUR is the estimated measure budget, not a grant amount or payment.",
    ),
    (
        "48e26f52fb7",
        None,
        {
            "actor": "Zukunft – Umwelt – Gesellschaft gGmbH",
            "role": "PROJECT_CARRIER",
            "status": "REPORTED_PARTICIPATION",
            "reference_time": None,
            "amount": None,
            "unit": None,
        },
        {
            "actor": "Zukunft – Umwelt – Gesellschaft gGmbH",
            "role": "als Projektträger",
            "status": "beteiligt",
        },
        "Extract ZUG, not the different ministry named as funder.",
    ),
    (
        "8a24e28ddec",
        None,
        {
            "actor": None,
            "role": None,
            "status": "REPORTED_COMPLETED_STUDY",
            "reference_time": "01.05.2024 – 30.09.2025",
            "amount": None,
            "unit": None,
        },
        {"status": "wurde im Bewilligungszeitraum", "reference_time": "01.05.2024 – 30.09.2025"},
        (
            "Vorhaben is a heat-planning study in the original heading; no "
            "installed heat network is proved."
        ),
    ),
    (
        "5431dbcd5f9",
        (
            "Titel und Förderkennzeichen: KSI: Kommunale Wärmeplanung für den "
            "Markt Neuhaus a. d. Pegnitz (67K29135) Laufzeit: Die Förderphase "
            "läuft von 01.11.2024 bis 31.10.2025 (Verlängerung bis 31.12.2026). "
            "Fördergeber: Bundesministerium für Wirtschaft und Klimaschutz (BMWK) "
            "Projektträgerschaft: Zukunft – Umwelt – Gesellschaft (ZUG) gGmbH"
        ),
        {
            "actor": "Bundesministerium für Wirtschaft und Klimaschutz",
            "role": "FUNDER",
            "status": "EXTENDED_FUNDING_PERIOD",
            "reference_time": "31.12.2026",
            "amount": None,
            "unit": None,
        },
        {
            "actor": "Bundesministerium für Wirtschaft und Klimaschutz",
            "role": "Fördergeber",
            "status": "Verlängerung bis 31.12.2026",
            "reference_time": "31.12.2026",
        },
        "Funding-period extension is not evidence of infrastructure completion.",
    ),
    (
        "49022f44c3c",
        (
            "Die Stadt Arnstein und der Markt Werneck haben am 11.02.2025 eine "
            "Kooperationsvereinbarung zur Entwicklung von Windenergieanlagen auf "
            "ihren jeweiligen Stadt bzw. Gemeindegebieten mit der Wust – Wind & "
            "Sonne GmbH & Co. KG geschlossen."
        ),
        {
            "actor": "Wust – Wind & Sonne GmbH & Co. KG",
            "role": "COOPERATION_PARTNER",
            "status": "AGREEMENT_REPORTED",
            "reference_time": "11.02.2025",
            "amount": None,
            "unit": None,
        },
        {
            "actor": "Wust – Wind & Sonne GmbH & Co. KG",
            "role": "Kooperationsvereinbarung",
            "status": "geschlossen",
            "reference_time": "11.02.2025",
        },
        "Scope explicitly includes Arnstein and Werneck, despite Arnstein publication host.",
    ),
    (
        "c166e5acbcf",
        None,
        {
            "actor": "ÜZ Mainfranken",
            "role": "PLANNED_GRID_CONNECTION_PARTNER",
            "status": "PLANNED",
            "reference_time": None,
            "amount": None,
            "unit": None,
        },
        {"actor": "ÜZ Mainfranken", "role": "örtlicher Netzbetreiber", "status": "wird"},
        "Prospective wording; no claim that wind turbines are in operation.",
    ),
    (
        "81bf418b69c",
        None,
        {
            "actor": "ÜZ Mainfranken",
            "role": "CONTRACTOR",
            "status": "CONTRACT_AWARDED",
            "reference_time": "3. Quartal 2022",
            "amount": None,
            "unit": None,
        },
        {
            "actor": "ÜZ Mainfranken",
            "role": "Auftrag zur Lieferung und Montage",
            "status": "vergeben",
            "reference_time": "3. Quartal 2022",
        },
        (
            "Contract award/source-reported period, not independent observation of "
            "completed installation."
        ),
    ),
    (
        "eacea91e330",
        None,
        {
            "actor": "Gemeinde Bergrheinfeld",
            "role": "PROGRAMME_PROVIDER",
            "status": "PROGRAMME_ANNOUNCED",
            "reference_time": "01.07.2021",
            "amount": "10000",
            "unit": "EUR_PER_YEAR",
        },
        {
            "actor": "Gemeinde Bergrheinfeld",
            "role": "kommunales Förderprogramm",
            "status": "legt",
            "reference_time": "01.07.2021",
            "amount": "10.000,00 € pro Haushaltsjahr",
            "unit": "€ pro Haushaltsjahr",
        },
        "Annual programme budget is not funds paid to projects.",
    ),
    (
        "dff4b0cb567",
        None,
        {
            "actor": None,
            "role": None,
            "status": "EXPIRED",
            "reference_time": "31.12.2023",
            "amount": None,
            "unit": None,
        },
        {"status": "abgelaufen", "reference_time": "31.12.2023"},
        (
            "Same-source context identifies the PV/storage programme; current "
            "application claim must be rejected."
        ),
    ),
    (
        "a3f0e9fe480",
        None,
        {
            "actor": "Gemeinde Kolitzheim",
            "role": "NETWORK_PARTICIPANT",
            "status": "REPORTED_PARTICIPATION",
            "reference_time": "2023 bis 2026",
            "amount": None,
            "unit": None,
        },
        {
            "actor": "Gemeinde Kolitzheim",
            "role": "aktive Kommune im Klimaschutz-Netzwerk",
            "status": "anerkannt",
            "reference_time": "2023 bis 2026",
        },
        ("Network recognition, not evidence that every network recommendation was implemented."),
    ),
    (
        "73f727a221a",
        None,
        {
            "actor": "Büro Wipflerplan",
            "role": "STUDY_COLLABORATOR",
            "status": "REPORTED_COMPLETED_STUDY",
            "reference_time": "2021 bis 2022",
            "amount": None,
            "unit": None,
        },
        {
            "actor": "Büro Wipflerplan",
            "role": "in Zusammenarbeit",
            "status": "durchgeführt",
            "reference_time": "2021 bis 2022",
        },
        "Study completion does not prove implementation of its recommendations.",
    ),
    (
        "dc208ae3275",
        None,
        {
            "actor": "Bayerische Staatsministerium für Wirtschaft, Energie und Technologie",
            "role": "FUNDER",
            "status": "REPORTED_FUNDING",
            "reference_time": None,
            "amount": "70",
            "unit": "percent",
        },
        {
            "actor": "Bayerische Staatsministerium für Wirtschaft, Energie und Technologie",
            "role": "gefördert",
            "status": "wurde",
            "amount": "70 %",
            "unit": "%",
        },
        (
            "Source inflection retained literally for actor; normalization must "
            "not silently rename the ministry."
        ),
    ),
]


def main(db, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh output directory")
    if (
        shutil.disk_usage(output.parent if output.parent.exists() else ".").free
        < RESERVE + 15 * 1024**2
    ):
        raise RuntimeError("Storage reserve plus output allowance not available")
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    records = []
    for prefix, excerpt, expected, anchors, note in IE_SPECS:
        record = read_case(conn, prefix, excerpt)
        record["provisional_expected"] = expected
        record["expected_evidence"] = anchors
        record["scientific_note"] = note
        record["expected_field_spans"] = {}
        for field, quote in anchors.items():
            start = record["text"].find(quote)
            if start < 0:
                raise ValueError("Expected field anchor not in IE input: " + field)
            record["expected_field_spans"][field] = [start, start + len(quote)]
        records.append(record)
    output.mkdir(parents=True)
    (output / "ie_cases.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    )
    (output / "IE_FREEZE.json").write_text(
        json.dumps(
            {
                "schema": "relational-ie-source-cases-v1",
                "status": "FROZEN_BEFORE_MODEL_OUTPUTS",
                "cases": len(records),
                "independent_human_gold": False,
                "case_sha256": sha((output / "ie_cases.jsonl").read_bytes()),
                "database": str(Path(db).resolve()),
                "database_sha256": sha(Path(db).read_bytes()),
                "preparation_code_sha256": sha(Path(__file__).read_bytes()),
                "reference_policy": (
                    "Unmodified provisional source-first labels; do not tune to model outputs."
                ),
            },
            indent=2,
        )
    )
    return records


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(len(main(args.db, args.output)))
