"""Full real-data local acceptance. All automated reviews forced human=false, never gold."""

import argparse
import json
import resource
import time
from pathlib import Path

from fastapi.testclient import TestClient

from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service
from crawler.web.local_index import LocalIndex
from crawler.web.validation import DIMENSIONS, ValidationWorkspace, attach_validation

QUESTIONS = [
    "Wer fördert die kommunale Wärmeplanung in Nennslingen?",
    "Welche Maßnahmen zur LED-Straßenbeleuchtung werden in Hof dokumentiert?",
    "Welche Rolle hat Ørsted beim geplanten Windpark Brunn-Laaber?",
    "Wie beschreibt Amberg den Stromverbrauch kommunaler Liegenschaften 2022?",
    "Welche Klimaschutzziele nennt München?",
    "Welche Maßnahmen zur Klimaanpassung nennt Augsburg?",
    "Welche Quellen nennen Solarenergie in Regensburg?",
    "Wie wird Hochwasserschutz in Passau behandelt?",
    "Welche Wärmeplanung beschreibt Rosenheim?",
    "Welche Energieprojekte werden in Landshut erwähnt?",
    "Welche Maßnahmen für Radverkehr werden in Ingolstadt genannt?",
    "Welche Energieeinsparmaßnahmen beschreibt Kempten (Allgäu)?",
    "Welche Quellen beschreiben Hitzeanpassung in Würzburg?",
    "Welche Klimaschutzmaßnahmen nennt Schweinfurt?",
    "Welche Verkehrswendeprojekte beschreibt Aschaffenburg?",
    "Welche kommunalen Klimaziele nennt Coburg?",
    "Welche Quellen nennen erneuerbare Energien in Bayreuth?",
    "Welche Wärmeversorgung beschreibt Ansbach?",
    "Welche Klimaanpassung wird in Erlangen dokumentiert?",
    "Welche Maßnahmen zu Solarenergie nennt Nürnberg?",
]


def compose(manifest, index_path, reviews):
    service = create_bavaria_service(manifest)
    index = LocalIndex(service.workspace, index_path)
    service.workspace.discovery_index = index
    app = create_app(service)
    attach_validation(app, service, index, reviews, technical=True)
    return app


def run(manifest, index_path, output):
    output.mkdir(parents=True, exist_ok=False)
    timings = []
    questions = []
    objects = []
    reviews = output / "technical-reviews"
    for batch in range(2):
        app = compose(manifest, index_path, reviews)
        with TestClient(app) as c:
            assert c.post("/api/validation/session", json={}).status_code == 200
            for q in QUESTIONS[batch * 10 : (batch + 1) * 10]:
                start = time.monotonic()
                r = c.post(
                    "/api/query",
                    json={
                        "question": q,
                        "mode": "lexical",
                        "municipality_ids": ["09361000"] if "Amberg" in q else [],
                    },
                )
                elapsed = time.monotonic() - start
                assert r.status_code == 200, r.text
                result = r.json()
                timings.append(elapsed)
                questions.append(
                    dict(
                        question=q,
                        evidence_count=len(result["evidence"]),
                        seconds=elapsed,
                        scope=result["retrieval_result"].get("search_scope"),
                        valid_answer_generated=False,
                    )
                )
                if batch == 0 and len(objects) < 5:
                    handoff = c.post(
                        "/api/validation/handoff",
                        json={"kind": "retrieval", "result_id": result["result_id"]},
                    )
                    assert handoff.status_code == 200
                    objects.append((handoff.json()["object_id"], "retrieval"))
                assert c.get("/api/export/" + result["result_id"] + ".json").status_code == 200
            if batch == 0:
                # Deterministic coverage-oriented source test sample; no scientific sample claim.
                with app.state.validation.index.conn() as db:
                    sources = [
                        r[0]
                        for r in db.execute(
                            "SELECT sha256 FROM sources WHERE unit_count>0 ORDER BY sha256 LIMIT 10"
                        )
                    ]
                for sha in sources:
                    original = c.get("/api/validation/original/" + sha)
                    assert (
                        original.status_code == 200 and original.headers["x-original-sha256"] == sha
                    )
                    handoff = c.post("/api/validation/handoff", json={"kind": "source", "key": sha})
                    assert handoff.status_code == 200
                    objects.append((handoff.json()["object_id"], "source"))
                w = app.state.research_service.workspace
                for kind, keys in [
                    ("entity", sorted(w.entities)[:5]),
                    ("relation", [r["relation_id"] for r in w.relation_rows[:5]]),
                ]:
                    for key in keys:
                        r = c.post("/api/validation/handoff", json={"kind": kind, "key": key})
                        assert r.status_code == 200, r.text
                        objects.append((r.json()["object_id"], kind))
                for oid, kind in objects:
                    form = dict(
                        reviewer="AUTOMATED_TRANSPORT_ACCEPTANCE",
                        decision="UNCERTAIN",
                        ratings={d: "NOT_JUDGED" for d in DIMENSIONS[kind]},
                        comment="Automated persistence and rubric workflow only. "
                        "No substantive source, entity, relation or relevance judgment.",
                        human_confirmed=True,
                    )
                    saved = c.post("/api/validation/review/" + oid, json=form)
                    assert saved.status_code == 200, saved.text
                    reopened = c.get("/api/validation/object/" + oid).json()
                    assert reopened["history"][-1]["payload"]["human"] is False
                    assert reopened["history"][-1]["payload"]["gold"] is False
            else:
                for oid, kind in objects:
                    assert c.get("/api/validation/object/" + oid).json()["history"]
                coverage = c.get("/api/validation/coverage").json()
                assert len(coverage["municipalities"]) == 2056
                assert sum(x["searchable"] for x in coverage["municipalities"]) == 1733
                assert sum(x["human_source_reviews"] for x in coverage["municipalities"]) == 0
                (output / "COVERAGE.json").write_text(
                    json.dumps(coverage, ensure_ascii=False, indent=2)
                )
                owner = c.post(
                    "/api/validation/session", json={"code": app.state.validation.keys["C"]}
                )
                assert owner.status_code == 200
                exported = c.get("/api/validation/export").json()
                assert c.post("/api/validation/import", json=exported).status_code == 200
                (output / "TECHNICAL_REVIEW_SNAPSHOT.json").write_text(
                    json.dumps(exported, ensure_ascii=False, indent=2)
                )
                restored = ValidationWorkspace(
                    app.state.research_service,
                    app.state.validation.index,
                    output / "restored-technical",
                    technical=True,
                )
                assert restored.import_snapshot(exported) == exported["audit_head"]
                assert len(restored.objects()) == len(objects)
                assert c.get("/api/validation/original/nonexistent").status_code == 404
                assert (
                    c.post(
                        "/api/validation/handoff",
                        json={"kind": "retrieval", "result_id": "expired"},
                    ).status_code
                    == 422
                )
                graph = c.get("/api/graph").json()
                assert graph["entities"] == 124 and graph["relations"] == 108
    # Empty query and municipality gaps remain observable, not negative policy findings.
    empty = app.state.validation.index.discover("qxzunfindable9861", [], 8)
    assert empty["results"] == []
    report = dict(
        schema="klima-local-product-acceptance-v1",
        snapshot_id=app.state.research_service.database_snapshot_id,
        corpus=app.state.research_service.coverage,
        graph={"entities": 124, "relations": 108},
        index_bytes=index_path.stat().st_size,
        method="SQLite FTS5 BM25 over normalized roots; global IDF",
        queries=questions,
        source_reviews=10,
        entity_relation_reviews=10,
        retrieval_reviews=5,
        automated_review_human=False,
        gold=False,
        all_ratings="NOT_JUDGED",
        restart_persistence=True,
        export_import_fresh_and_idempotent=True,
        frozen_study_access=False,
        empty_and_missing_paths=True,
        maximum_query_seconds=max(timings),
        max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        rate_limits=(
            "unchanged; 10 queries per service lifecycle; "
            "two real service compositions for restart test"
        ),
    )
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "queries"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.manifest.resolve(), a.index.resolve(), a.output.resolve())
