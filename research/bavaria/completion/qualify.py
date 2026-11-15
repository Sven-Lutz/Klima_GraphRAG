# ruff: noqa: E501
"""Freeze stronger-model qualification references from newly verified originals."""

import hashlib
import json
from pathlib import Path

FIELDS = ["actor", "role", "status", "reference_time", "amount", "unit", "amount_kind"]
# Decisions are original-first provisional development references, never human gold.
CASES = [
    (
        "S02",
        "Radverkehrskonzept: beauftragtes Büro, Rollenbezug und Datum der Zustimmung; keine Umsetzungskosten oder Auszahlung.",
        [
            "i.n.s. - Institut für innovative Städte",
            "COMMISSIONED_CONSULTANT",
            "ADOPTED",
            "13. Oktober 2020",
            None,
            None,
            None,
        ],
        "Radverkehrskonzept gibt Handlungsempfehlungen",
        1900,
    ),
    (
        "S06",
        "Potenzialanalyse Riedenburg: erstellende Organisation und Status der geplanten Windenergieanlagen; Datum des gemeindlichen Einvernehmens, keine Auszahlung.",
        [
            "Energieagentur Regensburg e. V.",
            "STUDY_AUTHOR",
            "PLANNED",
            "23.11.2023",
            None,
            None,
            None,
        ],
        "Die Stadt Riedenburg gehört",
        2300,
    ),
    (
        "S09",
        "Soforthilfe Haushalt/Hausrat: genannter externer Förderakteur, Förderhöchstbetrag und Regelstatus; tatsächlicher Zahlungszeitpunkt UNKNOWN.",
        [
            "Staatsministeriums der Finanzen und für Heimat",
            "FUNDER",
            "PROGRAMME_RULE",
            None,
            "5000",
            "EUR",
            "GRANT_CAP",
        ],
        "Soforthilfen für Bürger",
        1700,
    ),
    (
        "S13",
        "Kommunale Wärmeplanung der drei genannten Gemeinden: beauftragte Organisation, Beginn und Planungsstatus; keine installierte Infrastruktur.",
        [
            "Energieagentur Nordbayern",
            "COMMISSIONED_CONSULTANT",
            "PLANNED",
            "November 2025",
            None,
            None,
            None,
        ],
        "Zwischenergebnis Kommunale Wärmeplanung Hummeltal",
        900,
    ),
    (
        "S17",
        "Windpark Brunn-Laaber: Planungsfirma, Rollenbezug, Status und prognostizierter jährlicher Ertrag; Geldbetrag und Referenzzeit einer Messung UNKNOWN.",
        ["Orsted Onshore Deutschland GmbH", "PROJECT_PLANNER", "PLANNED", None, None, None, None],
        "Windpark Brunn-Laaber Im nördlichen",
        1400,
    ),
    (
        "S24",
        "Welche bereits bewilligte kommunale Klimaschutzförderung belegt dieser Bauplatz-Antragsbogen? Unbelegte Klimaschutz-Felder ausdrücklich UNKNOWN lassen.",
        [None, None, None, None, None, None, None],
        None,
        1600,
    ),
    (
        "S28",
        "Kommunales Programm mit Begrünung/Entsiegelung: Stadt als Förderakteur, Förderhöchstbetrag in Euro, Programmregel; Datum einer Auszahlung UNKNOWN.",
        ["Stadt Marktbreit", "FUNDER", "PROGRAMME_RULE", None, "7700", "EUR", "GRANT_CAP"],
        "§5 Förderung",
        2100,
    ),
    (
        "S05",
        "Welche konkrete kommunale Klimaschutzmaßnahme samt Akteur, Status, Projektzeit und Förderbetrag belegt der Auszug? Ohne Nachweis alle Felder UNKNOWN.",
        [None, None, None, None, None, None, None],
        None,
        1400,
    ),
]


def prepare(views, selection, db, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Qualification freeze already exists")
    output.mkdir(parents=True)
    rows = []
    for identifier, focus, values, start, length in CASES:
        d = json.loads((Path(views) / (identifier + ".json")).read_text())
        s = d["source"]
        full = " ".join(" ".join(p["text"].split()) for p in d["pages"])
        offset = full.index(start) if start else 0
        text = full[offset : offset + length]
        # Ensure all raw field evidence exists in exactly the frozen original excerpt.
        evidence = {f: None for f in FIELDS}
        for field, value in zip(FIELDS, values):
            if value is None:
                continue
            token = {
                "amount": "5.000" if identifier == "S09" else "7.700",
                "unit": "Euro" if identifier == "S09" else "€",
            }.get(field, value)
            if field in ("role", "status", "amount_kind"):
                token = text
            if token not in text:
                raise ValueError((identifier, field, token))
            evidence[field] = token
        rows.append(
            {
                "case_id": identifier,
                "component": s["sha256"],
                "source_sha256": s["sha256"],
                "source_frame_ags": [s["ags"]],
                "source_url": s["url"],
                "raw_path": s["raw_path"],
                "region": s["region"],
                "heading": s["name"],
                "page_ref": None,
                "unit_id": "original-excerpt:" + s["sha256"] + ":" + str(offset),
                "text": text,
                "focus": focus,
                "provisional_expected": dict(zip(FIELDS, values)),
                "expected_evidence": evidence,
                "original_locator": {
                    "normalized_offset": offset,
                    "length": len(text),
                    "original_sha256": s["sha256"],
                },
                "split": "evaluation",
                "reference_basis": "PROVISIONAL_AGENT_ORIGINAL_REVIEW",
                "exposure": "QUALIFICATION_ONLY_NO_CONFIRMATORY_GOLD",
            }
        )
    content = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)
    (output / "cases.jsonl").write_text(content)
    (output / "FREEZE.json").write_text(
        json.dumps(
            {
                "cases": len(rows),
                "cases_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "base_database_sha256": hashlib.sha256(Path(db).read_bytes()).hexdigest(),
                "selection_sha256": hashlib.sha256(Path(selection).read_bytes()).hexdigest(),
                "purpose": "Qualification only, category vocabulary from reference schema disclosed; no confirmatory accuracy claim.",
                "gate_before_model_output": {
                    "schema_valid_fraction_min": 1,
                    "strict_field_f1_min": 0.50,
                    "correct_unknown_fraction_min": 0.80,
                    "critical_actor_role_status_errors_max": 0,
                    "failure_action": "No mass inference; retain deterministic default.",
                },
                "model": "qwen3:1.7b",
                "max_calls": 8,
                "context": 2048,
                "output": 384,
                "seed": 42,
                "threads": 1,
                "independent_human_gold": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print("Frozen qualification cases", len(rows))


if __name__ == "__main__":
    prepare(
        "generated/bavaria/completion/original_views",
        "generated/bavaria/completion/selection-v2.json",
        "generated/bavaria/acquisition.sqlite",
        "generated/bavaria/completion/model-qualification",
    )
