"""Original-first, explicitly provisional targeted field references; no model outputs."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup

# Labels were read from these actual original passages before IE method outputs.
# Field order: actor, role, status, reference_time, amount, unit.
FINANCE = [
    (
        "0b1ce2673a37c018496ae08a09669c758f865093274319e2a81f22b79b49b2bf",
        "Fördergeber und Förderung der Fahrradabstellanlage, nicht Gesamtkosten",
        [
            "Bundesministerium für Umwelt, Klimaschutz, Naturschutz und nukleare Sicherheit",
            "FUNDER",
            "REPORTED_FUNDING",
            None,
            "679016",
            "EUR",
        ],
        [
            "Bundesministerium für Umwelt, Klimaschutz, Naturschutz und nukleare Sicherheit",
            "Förderung erfolgt durch",
            "erhält",
            None,
            "679.016 Euro",
            "Euro",
        ],
    ),
    (
        "13dcd6a6b7fbc3ee84800546af42aab947188f9f9eedda2802844a88e7682662",
        "Kosten für die genannte Stadt nach Förderung und Planungsstand; kein gezahlter Zuschuss",
        ["Stadt Burghausen", "COST_BEARER", "PLANNED", "Juli 2024", "13600000", "EUR"],
        [
            "Stadt Burghausen",
            "Kosten für die Stadt Burghausen",
            "laut Planungsstand",
            "Juli 2024",
            "13,6 Millionen Euro",
            "Euro",
        ],
    ),
    (
        "03dd97371311890e1a1763a50dd2182a59d532dc8a8d37d4011f403204c279af",
        "Pauschaler kommunaler Zuschuss, nicht prozentuale Obergrenze oder Leistung",
        [None, None, "PROGRAMME_RULE", None, "100", "EUR"],
        [None, None, "Förderung beträgt pauschal", None, "100,00 Euro", "Euro"],
    ),
    (
        "05e40c11e0ed688293bad6dcc80efe6f10c3adf28ab3c8284f6faf5911fb77f7",
        "Förderbetrag der Beleuchtungsmaßnahme, nicht Gesamtkosten oder Einsparprognose",
        [None, None, "REPORTED_FUNDING", None, "6048", "EUR"],
        [None, None, "gefördert", None, "6.048 €", "€"],
    ),
    (
        "15cf94a5068cb7baa09291f8aba18cc481bfe262e6d8b995c08240295004483c",
        "Gesamtsumme und Ende der beschriebenen Auszahlungslaufzeit; keine aktuelle Zahlung bestätigen",  # noqa: E501 — frozen text/regex literal
        [None, None, "REPORTED_PAYMENT_SCHEDULE", "30. September 2036", "387750", "EUR"],
        [None, None, "Die Auszahlung beginnt", "30. September 2036", "387.750 Euro", "Euro"],
    ),
    (
        "2226fbb0a517c8b599241d0ab00a24a1fad9bb175b4bdcae4a93ad1a522b4f62",
        "Genannte Empfängergemeinde und Bundeszuschuss; nicht die hostende Gemeinde und nicht Gesamtkosten",  # noqa: E501 — frozen text/regex literal
        ["Gemeinde Schwarzenbach", "RECIPIENT", "REPORTED_FUNDING", None, "450000", "EUR"],
        [
            "Gemeinde Schwarzenbach",
            "bekommt die Gemeinde Schwarzenbach",
            "bekommt",
            None,
            "450.000 Euro",
            "Euro",
        ],
    ),
    (
        "04ee57938fdb3f50590da1a26ff464bed7c7f1e735468a3906de8765c235da6b",
        "Kosten laut Planung, nicht maximaler Förderbetrag; offener Bescheid ist keine Bewilligung",
        [None, None, "PLANNED", None, "89805", "EUR"],
        [None, None, "lt. Planung", None, "89.805 €", "€"],
    ),
    (
        "229a16ebd208df0aca974febf2afe402924141040a3b5f8e670eace049039623",
        "Fördergeber und Förderbetrag des Energienutzungsplans",
        [
            "Bayerische Staatsministerium für Wirtschaft, Landesentwicklung und Energie",
            "FUNDER",
            "REPORTED_FUNDING",
            None,
            "23600",
            "EUR",
        ],
        [
            "Bayerische Staatsministerium für Wirtschaft, Landesentwicklung und Energie",
            "gefördert durch",
            "wurde",
            None,
            "23.600 €",
            "€",
        ],
    ),
    (
        "0cfbbd5cb4792a31f4a412b6c4e0eb164364ad6d97fb4880a48aa7e6561700e1",
        "Berichtete Gesamtfördersumme der genehmigten Anträge, keine installierte Anlagenleistung",
        [None, None, "PROGRAMME_TAKEUP_REPORTED", None, "109495", "EUR"],
        [None, None, "genehmigten Anträgen", None, "109.495,00 €", "€"],
    ),
    (
        "2179bf411c011119112485b20eb1bb9fb9add05bc188e5f78c4a6828360aa18d",
        "Maximaler Zuschuss je Zisterne, nicht Zuschuss pro Kubikmeter",
        [None, None, "PROGRAMME_RULE", None, "500", "EUR"],
        [None, None, "nicht mehr als", None, "500,-- € pro Zisterne", "€"],
    ),
    (
        "053d29b28f71af417f2aa3ea375830001f4b766fc4f1b1a1db7436ad88e29776",
        "Förderhöchstbetrag je Einzelobjekt, nicht prozentualer Fördersatz",
        [None, None, "PROGRAMME_RULE", None, "30000", "EUR"],
        [None, None, "höchstens", None, "30.000,00 €", "€"],
    ),
    (
        "1799aa3ddca8eb5e18c14ce37e7f2760c7c4eb13b7f6911668db4f1a708a6545",
        "Förderbetrag pro Kind; kommunaler Wohnungsbau ist nicht automatisch Klimaschutz",
        [None, None, "PROGRAMME_RULE", None, "5000", "EUR_PER_CHILD"],
        [None, None, "pro Kind", None, "5.000 € pro Kind", "€ pro Kind"],
    ),
    (
        "0b2e22e35a1361dd6a0c92d0cbabe09a57ead5db4a11f29b8eba7cb711e68b83",
        "Empfängergemeinde, berichteter Einbau der Pumpen und Förderbetrag, keine Messwirkung",
        ["Gemeinde Lamerdingen", "RECIPIENT", "REPORTED_IMPLEMENTED", None, "1113.09", "EUR"],
        [
            "Gemeinde Lamerdingen",
            "erhält die Kommune",
            "neue Pumpen eingebaut",
            None,
            "1.113,09 Euro",
            "Euro",
        ],
    ),
    (
        "14ae4985251a08b2728da4ae1919cf03e6ab2093492e819f079d4a4e0c99c9f5",
        "Fördergeber und Förderbetrag der LED-Maßnahme, nicht Materialkosten",
        ["LEW", "FUNDER", "REPORTED_FUNDING", None, "742.06", "EUR"],
        ["LEW", "Die LEW fördert diese Maßnahme", "fördert diese Maßnahme", None, "742,06 €", "€"],
    ),
    (
        "0e0e8c49b76ec1ae9ec9a83a233012cd1e70fc33a7cefcd669dfbd832b380929",
        "Höchste steuerliche Vergünstigung; allgemeine Bundesregel ist keine kommunale Förderung",
        [None, None, "PROGRAMME_RULE", None, "40000", "EUR"],
        [None, None, "Steuerliche Vergünstigungen", None, "40.000,- €", "€"],
    ),
    (
        "382e867b300e47e7a89568cd487a14d1acf102d3d764670c09338babc3f936ce",
        "Förderhöchstgrenze Heizungstausch EFH und explizite Foliendatierung, keine kommunale Zahlung",  # noqa: E501 — frozen text/regex literal
        [None, None, "PROGRAMME_RULE", "18.03.2025", "30000", "EUR"],
        [None, None, "Förderhöchstgrenze", "18.03.2025", "30.000 €", "€"],
    ),
]
FIELDS = ("actor", "role", "status", "reference_time", "amount", "unit")
AMOUNT_KINDS = [
    "REPORTED_GRANT",
    "PLANNED_NET_COST",
    "FLAT_GRANT_RULE",
    "REPORTED_GRANT",
    "PROJECTED_PAYMENT_TOTAL",
    "REPORTED_GRANT",
    "PLANNED_COST",
    "REPORTED_GRANT",
    "REPORTED_PROGRAMME_TOTAL",
    "GRANT_CAP",
    "GRANT_CAP",
    "PER_CHILD_GRANT_RULE",
    "REPORTED_GRANT",
    "REPORTED_GRANT",
    "TAX_RELIEF_CAP",
    None,
]


def normalized(text):
    return " ".join(text.split())


def prepare(db, relation_cases, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a fresh frozen sample path")
    output.mkdir(parents=True)
    cases = []
    with sqlite3.connect(Path(db).resolve(strict=True).as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for case_index, (uid, focus, values, quotes) in enumerate(FINANCE):
            row = conn.execute(
                "SELECT u.*,a.ags,a.url,d.raw_path,m.record FROM units u "
                "JOIN associations a USING(sha256) JOIN documents d USING(sha256) "
                "JOIN municipalities m USING(ags) WHERE unit_id=?",
                (uid,),
            ).fetchone()
            if row is None:
                raise ValueError("Required unit missing: " + uid)
            raw_path = Path(row["raw_path"])
            body = (
                gzip.decompress(raw_path.read_bytes())
                if raw_path.suffix == ".gz"
                else raw_path.read_bytes()
            )
            if hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise ValueError("Source integrity violation")
            if row["page_ref"]:
                result = subprocess.run(
                    [
                        "pdftotext",
                        "-f",
                        row["page_ref"],
                        "-l",
                        row["page_ref"],
                        "-layout",
                        "-",
                        "-",
                    ],
                    input=body,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                original = result.stdout.decode()
            else:
                original = BeautifulSoup(body, "lxml").get_text(" ", strip=True)
            visible = normalized(original)
            for quote in quotes:
                if quote is not None and normalized(quote) not in visible:
                    raise ValueError("Original quote missing: " + repr(quote))
            record = json.loads(row["record"])
            cases.append(
                {
                    "case_id": "finance-" + uid[:12],
                    "text": row["text"],
                    "heading": row["heading"],
                    "focus": focus,
                    "source_sha256": row["sha256"],
                    "unit_id": uid,
                    "source_frame_ags": [row["ags"]],
                    "region": record["region"],
                    "source_url": row["url"],
                    "page_ref": row["page_ref"],
                    "raw_path": str(raw_path),
                    "provisional_expected": dict(zip(FIELDS, values)),
                    "expected_evidence": dict(zip(FIELDS, quotes)),
                    "reference_basis": "PROVISIONAL_AGENT_ORIGINAL_REVIEW",
                    "original_locator": {
                        "type": "PDF_physical_page" if row["page_ref"] else "HTML_visible_text",
                        "page": row["page_ref"],
                        "verified_quotes": True,
                    },
                }
            )
            cases[-1]["provisional_expected"]["amount_kind"] = AMOUNT_KINDS[case_index]
            cases[-1]["expected_evidence"]["amount_kind"] = (
                row["text"] if AMOUNT_KINDS[case_index] else None
            )
    for line in Path(relation_cases).read_text().splitlines():
        case = json.loads(line)
        # Explicit targeted extraction task, not a hidden selection among several actors.
        case["focus"] = {
            "e54bea7c839": "Fördergeber sowie veranschlagte Maßnahmenkosten, nicht Höhe der Zahlung",  # noqa: E501 — frozen text/regex literal
            "48e26f52fb7": "Projektträger, nicht Fördergeber oder fachlicher Partner",
            "8a24e28ddec": "Status der Wärmeplanungsstudie und ihr Durchführungszeitraum",
            "5431dbcd5f9": "Fördergeber und verlängertes Ende der Förderphase",
            "49022f44c3c": "Privater Kooperationspartner und Datum der Kooperationsvereinbarung",
            "c166e5acbcf": "Geplanter Netzanschlusspartner, keine Inbetriebnahme behaupten",
            "81bf418b69c": "Auftragnehmer der Beleuchtungsumstellung und Beginn des Auftrags",
            "eacea91e330": "Anbieter des Förderprogramms, Beginn und jährliches Programmbudget",
            "dff4b0cb567": "Status des Förderprogramms und genanntes Ende",
            "a3f0e9fe480": "Teilnehmende Gemeinde und Zeitraum der Netzwerkanerkennung",
            "73f727a221a": "Mitwirkendes Planungsbüro und abgeschlossener Studienzeitraum",
            "dc208ae3275": "Fördergeber und Förderquote, nicht Ersteller des Plans",
        }[case["case_id"]]
        amount_kind = {
            "e54bea7c839": "PLANNED_COST",
            "eacea91e330": "ANNUAL_PROGRAMME_BUDGET",
            "dc208ae3275": "FUNDING_RATE",
        }.get(case["case_id"])
        case["provisional_expected"]["amount_kind"] = amount_kind
        case["expected_evidence"]["amount_kind"] = case["text"] if amount_kind else None
        cases.append(case)
    # Exact-source and municipality connected groups; no question-row random split.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    for case in cases:
        source = "source:" + case["source_sha256"]
        for ags in case["source_frame_ags"]:
            a, b = find(source), find("municipality:" + ags)
            parent[a] = b
    for case in cases:
        members = sorted(x for x in parent if find(x) == find("source:" + case["source_sha256"]))
        case["component"] = hashlib.sha256(json.dumps(members).encode()).hexdigest()
        case["split"] = "development" if int(case["component"][:8], 16) % 4 == 0 else "evaluation"
        case["exposure"] = "SOURCE_EXPOSED_AGENT_REFERENCE_NOT_INDEPENDENT_HUMAN_GOLD"
    payload = "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in cases)
    (output / "cases.jsonl").write_text(payload)
    freeze = {
        "schema": "targeted-field-ie-v1",
        "cases": len(cases),
        "cases_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "base_database_sha256": hashlib.sha256(Path(db).read_bytes()).hexdigest(),
        "fields": [*FIELDS, "amount_kind"],
        "sampling": "16 financial/source-scope contrasts plus12 relational/temporal cases",
        "reference": "PROVISIONAL; same-agent original review; descriptive conditional evaluation only",  # noqa: E501 — frozen text/regex literal
        "unknown_project_dependencies": "No claim of comprehensive project-family independence",
    }
    (output / "FREEZE.json").write_text(json.dumps(freeze, indent=2))
    return freeze
