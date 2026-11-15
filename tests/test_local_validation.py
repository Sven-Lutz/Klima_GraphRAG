"""Real SQLite/HTTP roundtrips over isolated source fixtures, never human gold."""

import copy

import pytest
from fastapi.testclient import TestClient
from test_pilot_release import technical_source

from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service
from crawler.web.local_index import LocalIndex
from crawler.web.validation import DIMENSIONS, attach_validation
from research.gold_studio.store import digest


def product(tmp_path, technical=True):
    manifest, _ = technical_source(tmp_path)
    service = create_bavaria_service(manifest, original_roots=[tmp_path])
    index = LocalIndex(service.workspace, tmp_path / "terms.sqlite")
    service.workspace.discovery_index = index
    app = create_app(service)
    attach_validation(app, service, index, tmp_path / "reviews", technical)
    return app, index


def login(client, code=None):
    assert (
        client.post("/api/validation/session", json={"code": code} if code else {}).status_code
        == 200
    )


def form(kind, decision="UNCERTAIN"):
    return dict(
        reviewer="AUTOMATED_TECHNICAL_FIXTURE",
        decision=decision,
        ratings={d: "NOT_JUDGED" for d in DIMENSIONS[kind]},
        human_confirmed=True,
        comment="Transport test; not a substantive scientific judgment.",
    )


def test_real_index_handoff_review_restart_export_import_and_split_safety(tmp_path):
    app, index = product(tmp_path)
    c = TestClient(app)
    assert c.get("/api/validation/catalog").status_code == 401
    login(c)
    result = c.post(
        "/api/query",
        json={
            "question": "Welche Planung einer Wärmeversorgung ist dokumentiert?",
            "mode": "lexical",
        },
    )
    assert result.status_code == 200 and result.json()["evidence"]
    sha = c.get("/api/validation/catalog").json()["items"][0]["sha256"]
    assert c.get("/api/validation/original/" + sha).headers["x-original-sha256"] == sha
    oid = c.post("/api/validation/handoff", json={"kind": "source", "key": sha}).json()["object_id"]
    bad = form("source")
    bad["evidence_quote"] = "NOT IN THIS SOURCE"
    assert c.post("/api/validation/review/" + oid, json=bad).status_code == 422
    assert c.post("/api/validation/review/" + oid, json=form("source")).status_code == 200
    obj = c.get("/api/validation/object/" + oid).json()
    assert obj["history"][0]["payload"]["human"] is False
    assert obj["history"][0]["payload"]["gold"] is False
    data = c.get("/api/validation/export").json()
    assert c.post("/api/validation/import", json=data).status_code == 200
    # Same data and metadata survive a newly composed service/store, no reindex.
    from crawler.web.validation import ValidationWorkspace

    reopened = ValidationWorkspace(
        app.state.research_service, index, tmp_path / "reviews", technical=True
    )
    assert reopened.export()["audit_head"] == app.state.validation.store.audit()
    fresh = ValidationWorkspace(
        app.state.research_service, index, tmp_path / "restored", technical=True
    )
    assert fresh.import_snapshot(data) == data["audit_head"]
    assert fresh.objects()[oid]["key"] == sha
    altered = copy.deepcopy(data)
    altered["split"] = "confirmation"
    with pytest.raises(ValueError, match="split"):
        fresh.import_snapshot(altered)
    altered = copy.deepcopy(data)
    altered["events"][0]["payload"]["key"] = "altered"
    with pytest.raises(ValueError, match="chain"):
        fresh.import_snapshot(altered)
    retrieval = c.post(
        "/api/validation/handoff",
        json={"kind": "retrieval", "result_id": result.json()["result_id"]},
    )
    assert retrieval.status_code == 200
    assert (
        c.post(
            "/api/validation/review/" + retrieval.json()["object_id"], json=form("retrieval")
        ).status_code
        == 200
    )


def test_blind_views_conflict_adjudication_and_import_cannot_promote_technical_reviews(tmp_path):
    app, index = product(tmp_path)
    a, b, owner = [TestClient(app) for _ in range(3)]
    login(a)
    login(b, app.state.validation.keys["B"])
    login(owner, app.state.validation.keys["C"])
    sha = a.get("/api/validation/catalog").json()["items"][0]["sha256"]
    oid = a.post("/api/validation/handoff", json={"kind": "source", "key": sha}).json()["object_id"]
    assert (
        a.post("/api/validation/review/" + oid, json=form("source", "CONFIRM")).status_code == 200
    )
    assert b.get("/api/validation/object/" + oid).json()["history"] == []
    assert b.post("/api/validation/review/" + oid, json=form("source", "REJECT")).status_code == 200
    assert len(a.get("/api/validation/object/" + oid).json()["history"]) == 1
    assert owner.get("/api/validation/queue?status=CONFLICT").json()["items"]
    f = form("source")
    f["adjudicate"] = True
    assert a.post("/api/validation/review/" + oid, json=f).status_code == 422
    assert owner.post("/api/validation/review/" + oid, json=f).status_code == 200
    assert owner.get("/api/validation/queue?status=ADJUDICATED").json()["items"]
    exported = owner.get("/api/validation/export").json()
    assert a.post("/api/validation/import", json=exported).status_code == 403
    # Recompute hashes to prove schema/split validation is separate from byte integrity.
    tampered = copy.deepcopy(exported)
    prev = "GENESIS"
    for event in tampered["events"]:
        if event["kind"] == "validation_review":
            event["payload"]["human"] = True
        event["previous"] = prev
        event["hash"] = digest(
            {k: event[k] for k in ("at", "role", "task", "stage", "kind", "previous", "payload")}
        )
        prev = event["hash"]
    tampered["audit_head"] = prev
    with pytest.raises(ValueError, match="technical/human"):
        app.state.validation.import_snapshot(tampered)


def test_index_identity_scope_empty_results_and_production_requires_personal_confirmation(tmp_path):
    app, index = product(tmp_path, technical=False)
    c = TestClient(app)
    login(c)
    assert index.discover("unfindablexyz987", [], 8)["results"] == []
    assert index.discover("Planung", ["09162111"], 8)["results"]
    with pytest.raises(ValueError):
        index.discover("Wärme", ["00000000"], 8)
    wrong = copy.copy(index.workspace)
    wrong.snapshot_id = "different"
    with pytest.raises(ValueError, match="another snapshot"):
        LocalIndex(wrong, index.path)
    sha = c.get("/api/validation/catalog").json()["items"][0]["sha256"]
    oid = c.post("/api/validation/handoff", json={"kind": "source", "key": sha}).json()["object_id"]
    f = form("source")
    f["human_confirmed"] = False
    assert c.post("/api/validation/review/" + oid, json=f).status_code == 422
    assert c.get("/api/validation/original/not-a-source").status_code == 404
    assert (
        c.post(
            "/api/validation/handoff", json={"kind": "retrieval", "result_id": "expired"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/validation/session", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_ambiguous_municipalities_and_malformed_handoff_are_actionable(tmp_path):
    app, index = product(tmp_path)
    c = TestClient(app)
    login(c)
    registry = app.state.research_service.workspace.municipalities
    other = copy.deepcopy(registry["09162111"])
    other["ags"] = "09162112"
    registry["09162112"] = other
    r = c.post(
        "/api/query",
        json={"question": "Welche Planung nennt TECHNICAL Teststadt?", "mode": "lexical"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "ambiguous_municipality"
    assert c.post("/api/validation/handoff", json=[]).status_code == 422
    assert c.post("/api/validation/handoff", json={"kind": "source"}).status_code == 422
    assert c.get("/api/validation/catalog?ags=00000000").status_code == 422
    assert c.get("/api/validation/catalog?region=not-a-region").status_code == 422


def test_catalog_and_queue_filters_preserve_source_frame_and_missingness(tmp_path):
    app, index = product(tmp_path)
    c = TestClient(app)
    login(c)
    catalog = c.get("/api/validation/catalog?ags=09162111&kind=html").json()
    assert catalog["total"] == 1
    sha = catalog["items"][0]["sha256"]
    oid = c.post("/api/validation/handoff", json={"kind": "source", "key": sha}).json()["object_id"]
    assert (
        c.get("/api/validation/queue?ags=09162111&document_type=html").json()["items"][0][
            "object_id"
        ]
        == oid
    )
    assert c.get("/api/validation/queue?document_type=pdf").json()["items"] == []
    invalid = form("source")
    invalid["ratings"]["publisher"] = "IRRELEVANT"
    assert c.post("/api/validation/review/" + oid, json=invalid).status_code == 422
    assert c.get("/api/validation/object/" + oid).json()["history"] == []


def test_original_integrity_failure_is_not_a_scientific_negative_label(tmp_path):
    app, index = product(tmp_path)
    c = TestClient(app)
    login(c)
    sha = c.get("/api/validation/catalog").json()["items"][0]["sha256"]
    with index.conn() as db:
        original = db.execute("SELECT raw_path FROM sources WHERE sha256=?", (sha,)).fetchone()[0]
    from pathlib import Path

    Path(original).write_bytes(b"changed original, not the archived SHA")
    assert c.get("/api/validation/original/" + sha).status_code == 404
    oid = c.post("/api/validation/handoff", json={"kind": "source", "key": sha}).json()["object_id"]
    obj = c.get("/api/validation/object/" + oid).json()
    assert obj["history"] == [] and obj["payload"]["scientific_validation"] == "NOT_JUDGED"


def test_malformed_import_is_rejected_without_mutating_history(tmp_path):
    app, _ = product(tmp_path)
    c = TestClient(app, raise_server_exceptions=False)
    login(c)
    snapshot = c.get("/api/validation/export").json()
    for malformed in ([], {**snapshot, "events": [None]}, {**snapshot, "events": [{}]}):
        assert c.post("/api/validation/import", json=malformed).status_code == 422
        assert c.get("/api/validation/export").json()["audit_head"] == snapshot["audit_head"]


def test_diagnostics_are_bound_exported_and_keep_municipal_scope(tmp_path):
    app, index = product(tmp_path)
    c = TestClient(app)
    result = c.post(
        "/api/query",
        json={
            "question": "Wie ist die Hitzevorsorge?",
            "mode": "lexical_expanded",
            "municipality_ids": ["09162111"],
        },
    ).json()
    diagnostic = result["retrieval_result"]["diagnostics"]
    assert (
        diagnostic["index_binding"]["snapshot"] == app.state.research_service.database_snapshot_id
    )
    assert diagnostic["effective_ags"] == ["09162111"]
    assert diagnostic["expansion"]["expanded_run"]["effective_ags"] == ["09162111"]
    assert "hitzeaktionsplan" in diagnostic["expansion"]["terms"]
    assert diagnostic["scope_indexed_units"] > 0
    assert diagnostic["candidates_after_municipality"] == 0
    assert result["evidence"] == []
    exported = c.get("/api/export/" + result["result_id"] + ".json").json()
    assert exported["diagnostics"] == diagnostic


def test_method_catalog_distinguishes_ie_scope_and_unavailable_dense(tmp_path):
    app, _ = product(tmp_path)
    catalog = app.state.research_service.status()["method_catalog"]
    methods = {m["id"]: m for m in catalog["methods"]}
    assert {k for k, m in methods.items() if m["available"]} == {
        "lexical",
        "lexical_expanded",
        "graph",
    }
    assert not methods["semantic"]["available"]
    assert catalog["graph_version"] == app.state.research_service.projection_id


def test_query_context_does_not_create_topical_hits_or_cross_compound_expansions(tmp_path):
    app, index = product(tmp_path)
    assert "energie" not in index.expand_query("Welche Solarenergieprojekte?")
    assert index.expand_query("Welche Energieprojekte?")
    # Rebuild a tiny technical fixture with the observed false-positive pattern.
    import json
    import sqlite3

    from research.bavaria.workspace.core import make_manifest

    folder = tmp_path / "generic-content"
    folder.mkdir()
    manifest, _ = technical_source(folder)
    ledger = json.loads(manifest.read_text())["artifacts"]["ledger"]["path"]
    with sqlite3.connect(ledger) as c:
        c.execute("UPDATE units SET text='Kommunale Verkehrsüberwachung nennt eine Sperrung.'")
    # The changed fixture is a new snapshot; never overwrite an immutable manifest.
    manifest = folder / "generic-workspace.json"
    make_manifest(ledger, None, manifest)
    service = create_bavaria_service(manifest, original_roots=[folder])
    index = LocalIndex(service.workspace, folder / "terms.sqlite")
    result = index.discover("Welche kommunalen Klimaziele nennt TECHNICAL Teststadt?", ["09162111"])
    assert result["results"] == []
    assert {"kommunal", "nennt"} <= set(result["diagnostics"]["ignored_context_terms"])


def test_pilot_feedback_is_persistent_isolated_and_never_gold(tmp_path):
    app, index = product(tmp_path)
    a, b = TestClient(app), TestClient(app)
    login(a)
    login(b, app.state.validation.keys["B"])
    payload = dict(
        message="Technical feedback test, no scientific judgment",
        category="ux",
        snapshot_id=app.state.research_service.database_snapshot_id,
        research_gold=False,
        context_included=False,
    )
    assert a.post("/api/feedback", json=payload).status_code == 200
    assert len(a.get("/api/feedback").json()["items"]) == 1
    assert a.get("/api/feedback").json()["items"][0]["payload"]["software"] == app.state.release_id
    assert b.get("/api/feedback").json()["items"] == []
    assert a.get("/api/validation/export").json()["events"] == []
    assert a.post("/api/feedback", json={**payload, "research_gold": True}).status_code == 422
    from crawler.web.validation import ValidationWorkspace

    reopened = ValidationWorkspace(app.state.research_service, index, tmp_path / "reviews", True)
    assert reopened.feedback_store.events()[0]["payload"]["human_gold"] is False


def test_navigation_filter_is_exact_and_not_a_topic_or_validity_judgment():
    from crawler.web.local_index import navigation_boilerplate

    assert navigation_boilerplate("Seite übersetzen mit Google Translate in: Englisch")
    assert navigation_boilerplate("Impressum")
    assert not navigation_boilerplate("Datenschutzmaßnahmen beim Hitzeaktionsplan")
    assert not navigation_boilerplate("Hitzeaktionsplan der Gemeinde Aschheim")
