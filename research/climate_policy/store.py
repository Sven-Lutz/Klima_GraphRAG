"""Typed source-bound claims and an explicit, immutable SQLite read snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from crawler.core.attribution import EvidenceText, attribute
from research.offline_evaluation.contracts import canonical, require

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "research/experiments/e2_v11_acquisition_20261024/export"
SCHEMA = "climate-policy-claims/1"
MUNICIPALITIES = {
    "09263000": "Straubing",
    "09163000": "Rosenheim",
    "09471123": "Buttenheim",
    "09780147": "Wildpoldsried",
}
NUMBER = r"\d+(?:\.\d{3})*(?:,\d+)?"
TOPICS = {
    "adaptation": r"begrün|entsiegel|klimabäum|anpass|hitz|hochwasser",
    "energy": r"energie|strom|windkraft|photovolta|\bpv\b|weißgerät|kwh|co2",
    "heat": r"wärme|heizung|nahwärme|biomasse",
    "mobility": r"fahrrad|radabstell|stellplätz|ladepunkt|ladesäul|schnelllade|öpnv|mvv|stadtbus",
    "governance": r"klimaschutz|klimakonzept|förder|zuschuss|investition",
}
RULES_VERSION = "bounded-regex-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ident(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def number(text):
    return str(Decimal(text.replace(".", "").replace(",", ".")))


def tags(text):
    return [name for name, pattern in TOPICS.items() if re.search(pattern, text, re.I)]


def extract(unit):
    """Generic, deliberately narrow rules; every non-NULL value has exact evidence."""
    text = unit["text"]
    context = unit["heading_context"] or ""
    topics = tags(text + " " + context)
    if not topics:
        return []
    result = []
    scope = attribute(
        {"ags": unit["acquisition_ags"], "name": MUNICIPALITIES[unit["acquisition_ags"]]},
        [
            EvidenceText(
                text,
                "pdf_page" if unit["page_ref"] else "body",
                {"source_id": unit["raw_hashes"][0], "unit_id": unit["unit_id"]},
            )
        ],
        municipality_names=tuple(MUNICIPALITIES.values()),
        unit="claim",
    )

    def add(field, value, start, end, role="REPORTED_IN_SOURCE", uom=None, actor=None):
        quote = text[start:end]
        require(bool(quote) and text[start:end] == quote, "Invalid claim evidence")
        record = {
            "schema_version": SCHEMA,
            "unit_id": unit["unit_id"],
            "source_sha256": unit["raw_hashes"][0],
            "source_url": unit["source_url"],
            "source_frame_ags": unit["acquisition_ags"],
            "municipality_ags": None,
            "claim_scope_names": scope["territorial_scope"],
            "scope_status": scope["attribution_status"],
            "field": field,
            "value": value,
            "unit": uom,
            "role": role,
            "actor": actor,
            "topics": topics,
            "measure_label": None,
            "source_heading_context": context or None,
            "instrument": None,
            "sector": topics,
            "responsible_institution": None,
            "target": None,
            "quantity_kind": (
                "CAPACITY"
                if uom in ["kW", "kWp", "MW"]
                else "ENERGY"
                if uom == "kWh"
                else "EMISSION"
                if uom and "CO" in uom
                else None
            ),
            "reference_date": None,
            "validity_start": None,
            "validity_end": None,
            "reported_outcome": None,
            "projected_outcome": value if role == "PROJECTED" else None,
            "measured_outcome": value if role == "EXPLICIT_MEASUREMENT_REPORT" else None,
            "start_char": start,
            "end_char": end,
            "quote": quote,
            "page_ref": unit["page_ref"],
            "support_status": "RULE_EXTRACTED_NOT_INDEPENDENTLY_VALIDATED",
            ("uncertainty"): (
                "Historical source report; current real-world "
                "state and claim jurisdiction may be unknown"
            ),
            "rule_version": RULES_VERSION,
        }
        if len(scope["territorial_scope"]) == 1:
            record["municipality_ags"] = next(
                (a for a, n in MUNICIPALITIES.items() if n == scope["territorial_scope"][0]), None
            )
        record["claim_id"] = ident(record)
        result.append(record)

    # Monetary field assignment follows the nearest explicit preceding role cue.
    for m in re.finditer(rf"(?P<n>{NUMBER})\s*(?:€|Euro\b)", text, re.I):
        before = text[max(0, m.start() - 180) : m.start()]
        cues = []
        for field, pattern in [
            ("PROJECT_COST", r"Investitionskosten|Gesamtinvestition|Gesamtkosten"),
            ("REPORTED_GRANT", r"Förderung|Zuschuss"),
            ("FUNDING_CAP", r"maximal|höchstens|max\."),
        ]:
            cues.extend((hit.end(), field) for hit in re.finditer(pattern, before, re.I))
        field = max(cues)[1] if cues else "MONEY_MENTION"
        if field == "MONEY_MENTION":
            continue
        # Evidence includes the exact number and enough role context, never inferred payment.
        add(
            field,
            number(m["n"]),
            max(0, m.start() - 100),
            min(len(text), m.end() + 60),
            "NORMATIVE_LIMIT" if field == "FUNDING_CAP" else "REPORTED_IN_SOURCE",
            "EUR_PER_M2" if re.match(r"\s*(?:pro|/)\s*m(?:2|²)", text[m.end() :], re.I) else "EUR",
        )
    for m in re.finditer(rf"(?P<n>{NUMBER})\s*%", text):
        local = text[max(0, m.start() - 100) : m.end() + 80]
        if re.search(r"Förder|Fördersatz", local, re.I):
            add(
                "FUNDING_RATE",
                number(m["n"]),
                max(0, m.start() - 100),
                min(len(text), m.end() + 80),
                "NORMATIVE_LIMIT"
                if re.search(r"Richtlinie|förderfähigen", text, re.I)
                else "REPORTED_IN_SOURCE",
                "percent",
            )
        elif re.search(r"einspar|reduzier|Eigenkapital", local, re.I):
            add(
                "PERCENTAGE",
                number(m["n"]),
                max(0, m.start() - 80),
                min(len(text), m.end() + 80),
                uom="percent",
            )
    for m in re.finditer(
        rf"(?P<n>{NUMBER})\s*(?P<u>kWh|kWp|MW|kW|Tonnen\s*CO\s*2|t\s*CO\s*2)", text, re.I
    ):
        projected = bool(
            re.search(
                r"künftig|erwart|prognos|rechnerisch|kann.*eingespart|können.*eingespart|sollen",
                text,
                re.I,
            )
        )
        measured = bool(re.search(r"gemessen|Messung|Zählerstand", text, re.I))
        field = "ENERGY_OR_EMISSION"
        add(
            field,
            number(m["n"]),
            max(0, m.start() - 100),
            min(len(text), m.end() + 100),
            "PROJECTED"
            if projected
            else "EXPLICIT_MEASUREMENT_REPORT"
            if measured
            else "REPORTED_IN_SOURCE",
            m["u"],
        )
    count_patterns = [
        ("CHARGING_COLUMNS", r"(?P<n>\d+)\s*x\s*Schnellladesäulen"),
        ("CHARGING_POINTS", r"(?P<n>\d+)\s+Ladepunkte"),
        ("APPLIANCE_COUNT", r"(?P<n>\d+)\s+Weißgeräte"),
        ("DWELLING_COUNT", r"(?P<n>\d+)\s+Wohneinheiten"),
        ("PROPERTY_COUNT", r"(?P<n>\d+)\s+(?:private\s+)?Anwesen"),
        (
            "BICYCLE_SPACES",
            (
                "(?P<n>\\d+)\\s+(?:frei zugängliche\\s+)?(?:absch"
                "ließbare\\s+)?(?:Doppelstockstellplätze|Stellp"
                "lätze|Reihenbügel)"
            ),
        ),
    ]
    for field, pattern in count_patterns:
        for m in re.finditer(pattern, text, re.I):
            add(field, m["n"], max(0, m.start() - 90), min(len(text), m.end() + 100), uom="count")
    for m in re.finditer(r"\b\d{2}\.\d{2}\.\d{4}\b", text):
        add(
            "DATE",
            m.group(),
            max(0, m.start() - 100),
            min(len(text), m.end() + 100),
            "ANNOUNCED_OR_HISTORICAL_DATE",
        )
    for field, pattern in [
        ("VALIDITY_START", r"tritt\s+am\s+(\d{2}\.\d{2}\.\d{4})\s+in Kraft"),
        ("VALIDITY_END", r"bis\s+zum\s+(\d{2}\.\d{2}\.\d{4})\s+befristet"),
    ]:
        for m in re.finditer(pattern, text, re.I):
            add(field, m.group(1), m.start(), m.end(), "SOURCE_VALIDITY")
            result[-1]["validity_start" if field == "VALIDITY_START" else "validity_end"] = m.group(
                1
            )
    for field, pattern in [
        ("ACTOR", r"(?:Betreiber(?: und Eigentümer)?\s*:?\s*)([^\n]{5,140})"),
        (
            "FUNDING_ACTOR",
            (
                "(?:Projektträger\\s+Jülich\\s*\\(PTJ\\)|Bundesmin"
                "isterium für Umwelt[^.;\\n]{0,100}|Zukunft\\s*["
                "–-]\\s*Umwelt\\s*[–-]\\s*Gesellschaft(?:\\s*\\(ZUG"
                "\\))?(?:\\s*gGmbH)?)"
            ),
        ),
    ]:
        for m in re.finditer(pattern, text, re.I):
            value = m.group(1).strip() if field == "ACTOR" else m.group()
            add(field, value, m.start(), m.end(), actor=value)
    # A whole bounded source passage can support qualitative claims without invented values.
    for status, pattern in [
        (
            "REPORTED_IMPLEMENTED",
            r"ausgetauscht werden|wurden[^.]{0,60}(?:errichtet|installiert|geschaffen|ersetzt)",
        ),
        ("ANNOUNCED", r"\b(?:soll|sollen|geplant|vorgesehen)\b"),
    ]:
        for m in re.finditer(pattern, text, re.I):
            add(
                "MEASURE_STATUS",
                status,
                max(0, m.start() - 80),
                min(len(text), m.end() + 100),
                "ANNOUNCEMENT" if status == "ANNOUNCED" else "REPORTED_IN_SOURCE",
            )
    if len(text) >= 30 and not re.search(
        r"De-Mail|Cookies|Datenschutzhinweis|Alle abwählen|Google Translate", text, re.I
    ):
        add(
            "SOURCE_STATEMENT",
            None,
            0,
            len(text),
            "ANNOUNCEMENT"
            if re.search(r"soll|geplant|vorgesehen|voraussichtlich", text, re.I)
            else "REPORTED_IN_SOURCE",
        )
    for claim in result:
        claim["claim_id"] = ident({k: v for k, v in claim.items() if k != "claim_id"})
    return result


def connect(db):
    path = Path(db).resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def fingerprint():
    return ident(
        {
            "corpus": sha(EXPORT / "corpus.jsonl"),
            "catalog": sha(EXPORT / "catalog.jsonl"),
            "schema": SCHEMA,
            "extractor": sha(Path(__file__)),
        }
    )


def build(db):
    path = Path(db).resolve()
    signature = fingerprint()
    if path.exists():
        with connect(path) as conn:
            require(
                conn.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()[0]
                == signature,
                (
                    "Existing database does not match current sour"
                    "ce/code snapshot; choose a fresh --db"
                ),
            )
        return {"database": str(path), "cached": True, "fingerprint": signature}
    sources = [json.loads(x) for x in (EXPORT / "catalog.jsonl").read_bytes().splitlines()]
    units = [json.loads(x) for x in (EXPORT / "corpus.jsonl").read_bytes().splitlines()]
    require(len(sources) == 57 and len(units) == 894, "Unexpected frozen corpus")
    require(len({u["unit_id"] for u in units}) == 894, "Duplicate unit identity")
    source_index = {s["body_sha256"]: s for s in sources}
    for source in sources:
        require(
            sha(EXPORT / "raw" / source["body_sha256"]) == source["body_sha256"],
            "Raw source hash mismatch",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with sqlite3.connect(path) as conn:
        conn.executescript("""PRAGMA foreign_keys=ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE municipalities(ags TEXT PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE sources(sha256 TEXT PRIMARY KEY,url TEXT,publisher TEXT,source_frame_ags TEXT,
          acquired_at TEXT,document_date TEXT,cutoff_status TEXT,source_type TEXT);
        CREATE TABLE evidence(unit_id TEXT PRIMARY KEY,
          source_sha256 TEXT REFERENCES sources(sha256),
          text TEXT,heading TEXT,page_ref TEXT,source_frame_ags TEXT,locator_json TEXT);
        CREATE TABLE claims(claim_id TEXT PRIMARY KEY,unit_id TEXT REFERENCES evidence(unit_id),
          field TEXT,value TEXT,uom TEXT,role TEXT,municipality_ags TEXT,record_json TEXT);
        CREATE INDEX claims_field ON claims(field); CREATE INDEX claims_unit ON claims(unit_id);
        CREATE TABLE measures(measure_id TEXT PRIMARY KEY,
          unit_id TEXT REFERENCES evidence(unit_id),description TEXT,status TEXT);
        CREATE TABLE actors(actor_id TEXT PRIMARY KEY,name TEXT);
        CREATE TABLE claim_actors(claim_id TEXT REFERENCES claims(claim_id),
          actor_id TEXT REFERENCES actors(actor_id),
          relationship TEXT,PRIMARY KEY(claim_id,actor_id));
        """)
        conn.executemany("INSERT INTO municipalities VALUES (?,?)", MUNICIPALITIES.items())
        for s in sources:
            conn.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                (
                    s["body_sha256"],
                    s["url"],
                    s["publishing_institution"],
                    s["ags"],
                    s["acquisition_time"],
                    s["document_date"],
                    s["cutoff_eligibility"],
                    s["source_type"],
                ),
            )
        claims = []
        for u in units:
            source = source_index[u["raw_hashes"][0]]
            require(
                u["source_url"] == source["url"] and u["acquisition_ags"] == source["ags"],
                "Source identity mismatch",
            )
            conn.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
                (
                    u["unit_id"],
                    u["raw_hashes"][0],
                    u["text"],
                    u["heading_context"],
                    u["page_ref"],
                    u["acquisition_ags"],
                    json.dumps(u["origins"], ensure_ascii=False),
                ),
            )
            if re.search(
                r"ausgetauscht|errichtet|installiert|Förderung|eingespart|geplant|geschaffen",
                u["text"],
                re.I,
            ):
                conn.execute(
                    "INSERT INTO measures VALUES (?,?,?,?)",
                    (
                        ident(u["unit_id"]),
                        u["unit_id"],
                        u["text"],
                        "SOURCE_PASSAGE_MENTION_NOT_RESOLVED_PROJECT",
                    ),
                )
            for c in extract(u):
                require(
                    u["text"][c["start_char"] : c["end_char"]] == c["quote"], "Claim span mismatch"
                )
                conn.execute(
                    "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)",
                    (
                        c["claim_id"],
                        c["unit_id"],
                        c["field"],
                        c["value"],
                        c["unit"],
                        c["role"],
                        c["municipality_ags"],
                        json.dumps(c, ensure_ascii=False, sort_keys=True),
                    ),
                )
                if c["actor"]:
                    actor = ident(c["actor"])
                    conn.execute("INSERT OR IGNORE INTO actors VALUES (?,?)", (actor, c["actor"]))
                    conn.execute(
                        "INSERT INTO claim_actors VALUES (?,?,?)",
                        (c["claim_id"], actor, c["field"]),
                    )
                claims.append(c)
        conn.execute("INSERT INTO metadata VALUES (?,?)", ("fingerprint", signature))
        conn.execute("INSERT INTO metadata VALUES (?,?)", ("schema", SCHEMA))
        conn.execute(
            "INSERT INTO metadata VALUES (?,?)", ("corpus_sha256", sha(EXPORT / "corpus.jsonl"))
        )
    return {
        "database": str(path),
        "cached": False,
        "fingerprint": signature,
        "sources": 57,
        "units": 894,
        "claims": len(claims),
        "seconds": time.monotonic() - started,
        "claims_content_sha256": ident(sorted(claims, key=lambda c: c["claim_id"])),
    }
