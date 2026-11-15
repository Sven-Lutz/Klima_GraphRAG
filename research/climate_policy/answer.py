"""Deterministic evidence composer; no model truth claims or copied gold responses."""

import json
import re

from research.climate_policy import retrieval, store
from research.offline_evaluation.contracts import require

LABELS = {
    "FUNDING_CAP": "Förderobergrenze",
    "FUNDING_RATE": "Förderquote",
    "REPORTED_GRANT": "berichtete Förderung",
    "PROJECT_COST": "berichtete Projektkosten",
    "ENERGY_OR_EMISSION": "Energie-/Emissionsangabe",
    "CHARGING_COLUMNS": "Schnellladesäulen",
    "CHARGING_POINTS": "Ladepunkte",
    "APPLIANCE_COUNT": "Geräte",
    "PROPERTY_COUNT": "Anwesen",
    "DWELLING_COUNT": "Wohneinheiten",
    "BICYCLE_SPACES": "Stellplätze/Reihenbügel",
    "DATE": "Datumsangabe",
    "ACTOR": "benannter Akteur",
    "FUNDING_ACTOR": "benannter Förderakteur",
    "MEASURE_STATUS": "Maßnahmenstatus laut Quelle",
    "PERCENTAGE": "Prozentangabe",
    "VALIDITY_START": "Gültigkeitsbeginn",
    "VALIDITY_END": "Gültigkeitsende",
}


def analyze(db, question, method="tools", k=5, budget_chars=6000, allowed_sources=None):
    response = retrieval.retrieve(db, question, method, k, budget_chars, allowed_sources)
    canonical_units = {
        u["unit_id"]: u
        for u in (
            json.loads(line) for line in (store.EXPORT / "corpus.jsonl").read_bytes().splitlines()
        )
    }
    citations = []
    claims = []
    warnings = []
    plan = response["question_intent"]
    specific_topics = set(plan["topics"]) - {"governance"}
    named_focus = re.findall(r"\b(?:[A-ZÄÖÜ][a-zäöüß]+[A-Z][A-Za-zäöüß]*|[A-Z]{2,})\b", question)
    with store.connect(db) as conn:
        for item in response["items"]:
            source = conn.execute(
                "SELECT * FROM sources WHERE sha256=?", (item["raw_hashes"][0],)
            ).fetchone()
            require(
                store.sha(store.EXPORT / "raw" / source["sha256"]) == source["sha256"],
                "Original hash drift at query",
            )
            original = canonical_units[item["unit_id"]]
            require(
                item["text"] == original["text"]
                and item["raw_hashes"] == original["raw_hashes"]
                and item["source_url"] == original["source_url"],
                "Database evidence differs from canonical frozen source unit",
            )
            reference = "S" + str(len(citations) + 1)
            citations.append(
                {
                    "id": reference,
                    "unit_id": item["unit_id"],
                    "url": source["url"],
                    "source_sha256": source["sha256"],
                    "publisher": source["publisher"],
                    "source_frame_ags": source["source_frame_ags"],
                    "acquired_at": source["acquired_at"],
                    "document_date": source["document_date"],
                    "page_ref": item["page_ref"],
                    "heading_context": item["heading_context"],
                    "start_char": 0,
                    "end_char": len(item["text"]),
                    "quote": item["text"],
                    "cutoff_status": source["cutoff_status"],
                }
            )
            available = [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT record_json FROM claims WHERE unit_id=? ORDER BY claim_id",
                    (item["unit_id"],),
                )
            ]
            for claim in available:
                require(
                    claim["claim_id"]
                    == store.ident(
                        {key: value for key, value in claim.items() if key != "claim_id"}
                    ),
                    "Claim record hash mismatch",
                )
                if plan["fields"] and claim["field"] not in plan["fields"]:
                    continue
                if claim["field"] == "SOURCE_STATEMENT":
                    continue
                if specific_topics and not specific_topics & set(claim["topics"]):
                    continue
                if named_focus and not any(
                    term.casefold() in claim["quote"].casefold() for term in named_focus
                ):
                    continue
                if (
                    plan["municipalities"]
                    and claim["source_frame_ags"] not in plan["municipalities"]
                ):
                    continue
                require(
                    item["text"][claim["start_char"] : claim["end_char"]] == claim["quote"],
                    "Claim locator invalid",
                )
                claims.append({**claim, "citation_id": reference})
    # Same composition for all methods; representation/retrieval is the experiment factor.
    unique = {
        store.ident([c["unit_id"], c["field"], c["value"], c["start_char"], c["end_char"]]): c
        for c in claims
    }
    claims = list(unique.values())
    if re.search(r"vergleich", question, re.I) and {"NORMATIVE_LIMIT", "REPORTED_IN_SOURCE"} <= {
        c["role"] for c in claims
    }:
        warnings.append(
            (
                "Fördergrenzen und berichtete Projektförderung"
                " haben unterschiedliche Rollen; daraus folgt "
                "kein Wirksamkeitsvergleich."
            )
        )
    for c in claims:
        if c["role"] == "PROJECTED":
            warnings.append("Eine Wirkungsangabe ist eine Prognose, keine gemessene Einsparung.")
    if re.search(r"aktuell|heute|abgeschlossen|fertig|2026", question, re.I):
        warnings.append(
            (
                "Aktueller Realweltstatus ist mit den archivie"
                "rten Fassungen nicht bestätigt; eine angekünd"
                "igte Frist beweist keine Fertigstellung."
            )
        )
    if re.search(r"gemessen|messung|tatsächlich.*einspar", question, re.I) and not any(
        c["role"] == "EXPLICIT_MEASUREMENT_REPORT" for c in claims
    ):
        warnings.append(
            "Im ausgewählten Belegkontext ist keine ausdrücklich gemessene Wirkung nachgewiesen."
        )
    for cite in citations:
        other = [
            n
            for a, n in store.MUNICIPALITIES.items()
            if a not in plan["municipalities"]
            and re.search(r"\b" + re.escape(n) + r"\b", cite["quote"], re.I)
        ]
        if len(plan["municipalities"]) == 1 and other:
            warnings.append(
                "Beleg nennt auch "
                + ", ".join(other)
                + ". Diese Quelle bestätigt damit keine ausschließliche Zuordnung zum Frageort."
            )
    lines = []
    for c in claims:
        value = c["value"] or "UNKNOWN"
        uom = (" " + c["unit"]) if c["unit"] else ""
        role = {
            "PROJECTED": "Prognose",
            "NORMATIVE_LIMIT": "normative Grenze",
            "EXPLICIT_MEASUREMENT_REPORT": "als Messung berichtet",
            "REPORTED_IN_SOURCE": "in der Quelle berichtet",
            "ANNOUNCED_OR_HISTORICAL_DATE": "historisch/angekündigt",
            "SOURCE_VALIDITY": "Gültigkeit laut Quelle",
        }.get(c["role"], c["role"])
        city = store.MUNICIPALITIES[c["source_frame_ags"]]
        lines.append(
            f"{city} – {LABELS.get(c['field'], c['field'])}: "
            f"{value}{uom} ({role}) [{c['citation_id']}]."
        )
    if not claims:
        if citations:
            lines.append(
                (
                    "Passende archivierte Quellenpassagen wurden g"
                    "efunden; die Frage ist daraus nicht automatis"
                    "ch als vollständiger Sachverhalt bestätigt."
                )
            )
        else:
            lines.append(
                "Keine belastbare Antwort im zugänglichen archivierten Quellenrahmen gefunden."
            )
    lines.append(
        (
            "Quellenkommune und Aussagegeltung sind getren"
            "nt: nicht explizit belegte Zuständigkeiten, f"
            "ehlende Werte und heutige Zustände bleiben UN"
            "KNOWN."
        )
    )
    observations = []
    if not claims and citations:
        for cite in citations[:2]:
            observations.append(
                {
                    "citation_id": cite["id"],
                    "quote": cite["quote"][:500],
                    "support": "EXTRACTIVE_OBSERVATION_NOT_COMPLETE_ANSWER",
                }
            )
        lines.extend(
            "Quellenauszug [" + o["citation_id"] + "]: " + o["quote"] for o in observations
        )
    return {
        "extractive_observations": observations,
        "question": question,
        "method": method,
        "status": "PARTIAL_SOURCE_GROUNDED" if citations else "NO_SUPPORT_FOUND",
        "answer": "\n".join(lines),
        "claims": claims,
        "citations": citations,
        "warnings": list(dict.fromkeys(warnings)),
        "retrieval": {key: value for key, value in response.items() if key != "items"},
        "claim_jurisdiction": "UNKNOWN_UNLESS_EXPLICIT",
        "independent_validation": False,
        "api_calls": 0,
        "api_cost_eur": 0,
    }
