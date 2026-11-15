"""Common evidence access, existing BM25, structured SQL and a bounded tool workflow."""

import json
import re
import types
from collections import Counter

from research.climate_policy.store import MUNICIPALITIES, connect, ident, tags
from research.experiments.e2_offline_20261024.kit.vendor import lexical
from research.offline_evaluation.contracts import require

# Reuse the unmodified frozen ranking implementation with a neutral municipality vocabulary.
RANK = types.FunctionType(
    lexical.rank.__code__, {**lexical.__dict__, "CITY_TERMS": {"FRAME": set()}}, "rank"
)
METHODS = ("bm25", "bm25_german", "sql", "tools")
FIELD_GROUPS = {
    "finance": ["FUNDING_CAP", "FUNDING_RATE", "REPORTED_GRANT", "PROJECT_COST", "FUNDING_ACTOR"],
    "effects": ["ENERGY_OR_EMISSION", "PERCENTAGE", "APPLIANCE_COUNT"],
    "actor": ["ACTOR", "FUNDING_ACTOR"],
    "capacity": [
        "CHARGING_COLUMNS",
        "CHARGING_POINTS",
        "BICYCLE_SPACES",
        "PROPERTY_COUNT",
        "DWELLING_COUNT",
    ],
    "dates": ["DATE", "VALIDITY_START", "VALIDITY_END"],
}


def intent(question):
    fields = []
    for name, pattern in [
        ("finance", r"förder|finanz|kosten|investition|zuschuss"),
        ("effects", r"wirk|einspar|co2|verbrauch|kwh|weißgerät"),
        ("actor", r"akteur|zuständig|betreiber|wer\b|träger"),
        ("capacity", r"wie viele|anzahl|stellpl|ladepunkt|säulen|anschluss|wohneinheit|anwesen"),
        ("dates", r"wann|datum|zeit|laufzeit|frist|gültig|2023|2026"),
    ]:
        if re.search(pattern, question, re.I):
            fields.extend(FIELD_GROUPS[name])
    return {
        "fields": list(dict.fromkeys(fields)),
        "topics": tags(question),
        "municipalities": [
            a for a, n in MUNICIPALITIES.items() if n.casefold() in question.casefold()
        ],
    }


def load_units(conn):
    rows = []
    for r in conn.execute(
        (
            "SELECT e.*,s.url FROM evidence e JOIN sources"
            " s ON s.sha256=e.source_sha256 ORDER BY unit_"
            "id"
        )
    ):
        rows.append(
            {
                "unit_id": r["unit_id"],
                "text": r["text"],
                "heading_context": r["heading"],
                "page_ref": r["page_ref"],
                "acquisition_ags": r["source_frame_ags"],
                "source_url": r["url"],
                "raw_hashes": [r["source_sha256"]],
            }
        )
    return rows


def lexical_rank(units, question, german=False):
    ranked, _ = RANK(units, question, "FRAME", "L3" if german else "L2")
    return [(unit["unit_id"], score) for score, unit in ranked]


def structured_rank(conn, units, question):
    plan = intent(question)
    wanted = plan["fields"]
    query_tokens = set(lexical.tokenise(question))
    if not wanted:
        wanted = ["SOURCE_STATEMENT"]
    placeholders = ",".join("?" for _ in wanted)
    records = conn.execute(
        f"SELECT record_json FROM claims WHERE field IN ({placeholders})", wanted
    ).fetchall()
    features = {u["unit_id"]: u for u in units}
    scores = Counter()
    for row in records:
        claim = json.loads(row[0])
        u = features.get(claim["unit_id"])
        if not u:
            continue
        if plan["municipalities"] and u["acquisition_ags"] not in plan["municipalities"]:
            continue
        if plan["topics"] and not set(plan["topics"]) & set(claim["topics"]):
            continue
        overlap = len(
            query_tokens & set(lexical.tokenise(u["text"] + " " + (u["heading_context"] or "")))
        )
        scores[u["unit_id"]] += 1 + overlap
    return sorted(scores.items(), key=lambda row: (-row[1], row[0]))


def retrieve(db, question, method="tools", k=5, budget_chars=6000, allowed_sources=None):
    require(
        method in METHODS and 1 <= k <= 10 and 200 <= budget_chars <= 16000,
        "Invalid method/context bounds",
    )
    with connect(db) as conn:
        units = load_units(conn)
        if allowed_sources is not None:
            units = [u for u in units if u["raw_hashes"][0] in allowed_sources]
        if method in ["bm25", "bm25_german"]:
            ranked = lexical_rank(units, question, method == "bm25_german")
            operations = ["lexical_rank"]
        elif method == "sql":
            ranked = structured_rank(conn, units, question)
            operations = ["typed_sql_query"]
        else:
            # Two bounded, deterministic tools; no hidden model/search loop.
            sql = structured_rank(conn, units, question)
            lex = lexical_rank(units, question, True)
            rrf = Counter()
            for ranking in [sql, lex]:
                for rank, (uid, _) in enumerate(ranking[:20], 1):
                    rrf[uid] += 1 / (60 + rank)
            ranked = sorted(rrf.items(), key=lambda row: (-row[1], row[0]))
            operations = ["typed_sql_query", "lexical_rank", "rrf"]
        by_id = {u["unit_id"]: u for u in units}
        selected = []
        used = 0
        for uid, score in ranked:
            u = by_id[uid]
            # Evidence is never silently truncated into a different locator.
            if used + len(u["text"]) > budget_chars:
                continue
            selected.append({**u, "score": score, "rank": len(selected) + 1})
            used += len(u["text"])
            if len(selected) >= k:
                break
    return {
        "items": selected,
        "operations": operations,
        "candidate_units": len(units),
        "context_chars": used,
        "question_intent": intent(question),
        "k": k,
        "budget_chars": budget_chars,
        "access_scope_sha256": ident(sorted(u["unit_id"] for u in units)),
    }
