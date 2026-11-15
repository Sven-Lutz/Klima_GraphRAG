"""Real-data retrieval inspection; candidate matches are never relevance Gold."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from crawler.core.retrieval import RetrievalMode
from crawler.web.bavaria import create_bavaria_service
from crawler.web.local_index import LocalIndex
from crawler.web.service import QueryOptions

CASES = [
    ("Wie ist die Hitzevorsorge in München?", "09162000"),
    ("Welche Quellen nennen Hitzeschutz in München?", "09162000"),
    ("Gibt es einen Hitzeaktionsplan in München?", "09162000"),
    ("Welche Klimaanpassung dokumentiert Augsburg?", "09761000"),
    ("Welche Quellen beschreiben Hochwasserschutz in Passau?", "09262000"),
    ("Welche Solarenergieprojekte nennt Regensburg?", "09362000"),
    ("Welche Wärmeplanung beschreibt Rosenheim?", "09163000"),
    ("Welche Energieprojekte nennt Landshut?", "09261000"),
    ("Welche Maßnahmen für Radverkehr nennt Ingolstadt?", "09161000"),
    ("Welche Energieeinsparmaßnahmen nennt Kempten (Allgäu)?", "09763000"),
    ("Welche Hitzeanpassung nennt Würzburg?", "09663000"),
    ("Welche Klimaschutzmaßnahmen nennt Schweinfurt?", "09662000"),
    ("Welche Verkehrswendeprojekte nennt Aschaffenburg?", "09661000"),
    ("Welche kommunalen Klimaziele nennt Coburg?", "09463000"),
    ("Welche erneuerbaren Energien nennt Bayreuth?", "09462000"),
    ("Welche Wärmeversorgung beschreibt Ansbach?", "09561000"),
    ("Welche Klimaanpassung dokumentiert Erlangen?", "09562000"),
    ("Welche Maßnahmen zu Solarenergie nennt Nürnberg?", "09564000"),
    ("Wer fördert die kommunale Wärmeplanung in Nennslingen?", "09577151"),
    ("Welche Maßnahmen zur LED-Straßenbeleuchtung nennt Hof?", "09464000"),
    ("Wie hoch ist der Stromverbrauch kommunaler Liegenschaften in Amberg 2022?", "09361000"),
    ("Welche Wärmeplanung dokumentiert Unterhaching?", "09184148"),
    ("Welche Energieversorgung beschreibt Stammbach?", "09475175"),
    ("Welche Klimaschutzmaßnahmen nennt Bamberg?", "09461000"),
    ("Welche Mobilitätsmaßnahmen nennt Kaufbeuren?", "09762000"),
    ("Welche Wärmeplanung beschreibt Strahlungen?", "09673171"),
    ("Welche Quellen in Bayern nennen einen Hitzeaktionsplan?", None),
    ("Welche Quellen in Bayern nennen Entsiegelung?", None),
]


def run(manifest, index, output):
    output.mkdir(parents=True, exist_ok=False)
    s = create_bavaria_service(manifest)
    s.workspace.discovery_index = LocalIndex(s.workspace, index)
    runs = []
    for question, ags in CASES:
        assert ags is None or ags in s.workspace.municipalities, ags
        for method in (RetrievalMode.LEXICAL, RetrievalMode.LEXICAL_EXPANDED, RetrievalMode.GRAPH):
            started = time.perf_counter()
            r = s.execute(QueryOptions(question, method, 8, 8, 24000, False, (ags,) if ags else ()))
            elapsed = time.perf_counter() - started
            for e in r["evidence"]:
                if ags and method is not RetrievalMode.GRAPH:
                    assert ags in e["document"]["municipality_ids"], "Wrong municipality evidence"
            canonical = r["retrieval_result"]
            run = dict(
                question=question,
                ags=ags,
                snapshot=canonical["database_snapshot_id"],
                method=method.value,
                latency_ms=round(elapsed * 1000, 3),
                diagnostics=canonical["diagnostics"],
                finding="CANDIDATE_PASSAGES_REQUIRE_CONTENT_INSPECTION"
                if r["evidence"]
                else "NO_EVIDENCE_FOUND_IN_THIS_SCOPE_AND_METHOD",
                evidence_count=len(r["evidence"]),
                evidence=[
                    dict(
                        source_id=e["source_id"],
                        document=e["document"],
                        text=e["evidence_text"],
                        traces=e["retrieval_traces"],
                    )
                    for e in r["evidence"]
                ],
            )

            def signature(value):
                return hashlib.sha256(
                    json.dumps(
                        {
                            "items": value["items"],
                            "mode": value["mode"],
                            "scope": value["search_scope"],
                            "snapshot": value["database_snapshot_id"],
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()

            repeated = s.execute(
                QueryOptions(question, method, 8, 8, 24000, False, (ags,) if ags else ())
            )["retrieval_result"]
            assert signature(canonical) == signature(repeated), "Non-reproducible ranked evidence"
            run["replay_identical_ranked_evidence"] = True
            run["ranked_evidence_sha256"] = signature(canonical)
            runs.append(run)
        latest = runs[-2]
        print(
            json.dumps(
                dict(
                    question=question,
                    expanded_hits=latest["evidence_count"],
                    first=latest["evidence"][0]["text"][:480] if latest["evidence"] else "NO_MATCH",
                ),
                ensure_ascii=False,
            ),
            flush=True,
        )
    report = dict(
        schema="research-quality-audit-v1",
        coverage=s.coverage,
        questions=len(CASES),
        runs=runs,
        human_gold=False,
        ranking_quality_metric=None,
        wrong_municipality_check="PASS",
    )
    (output / "RETRIEVAL_AUDIT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    s.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.manifest, a.index, a.output)
