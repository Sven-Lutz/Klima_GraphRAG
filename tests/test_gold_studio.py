"""Synthetic protocol tests never produce human research labels."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from research.gold_studio.app import create_app
from research.gold_studio.prepare import prepare
from research.gold_studio.store import Store


def login(client, store, role):
    key = json.loads(store.meta("keys"))[role]
    assert client.post("/api/login", json={"role": role, "code": key}).status_code == 200
    assert (
        client.post(
            "/api/profile",
            json={
                "name": "TECH-" + role,
                "qualification": "synthetic software test",
                "human": True,
                "developer": True,
                "independent": False,
            },
        ).status_code
        == 200
    )


def payload(stage, task, authority="official"):
    p = {
        "original_inspected": True,
        "seconds": 1,
        "comment": "TECHNICAL_SIMULATION",
        "source_sha256": task["allowed_original_sha256"][0],
    }
    if stage == "source":
        p.update(
            authority=authority, scope="unclear", policy="unclear", answerability="UNANSWERABLE"
        )
    elif stage == "fields":
        p.update(temporal="UNKNOWN", judgment="not_proven")
    elif stage == "retrieval":
        p.update(relevance={x["id"]: "unclear" for x in task["pool"]}, complete="unclear")
    else:
        p.update(answers={})
    return p


def test_frozen_selection_and_method_blinding():
    from research.gold_studio.calibrate import run

    with pytest.raises(ValueError, match="local HTTP"):
        run("nonexistent-workspace", "https://example.org")
    b = prepare()
    assert len(b["selection"]["calibration"]) == 12
    assert b["selection"]["planned_old_split_counts"] == {"evaluation": 130, "development": 70}
    assert not any(
        t["cohort"] == "existing_200"
        for t in b["tasks"]
        if t["task_id"] in b["selection"]["calibration"]
    )
    for t in b["tasks"]:
        for c in t["pool"]:
            assert not set(c) & {"method", "rank", "score", "reference_status"}


def test_api_privacy_resume_and_no_simulated_gold(tmp_path):
    app = create_app(tmp_path, True)
    s = app.state.store
    with TestClient(app) as c:
        assert c.get("/api/state").status_code == 403
        login(c, s, "A")
        assert c.get("/api/state", headers={"X-Studio-Role": "B"}).status_code == 403
        old_source = next(t for t in s.bundle["tasks"] if t["cohort"] == "existing_200")[
            "allowed_original_sha256"
        ][0]
        assert c.get("/api/original/" + old_source + "/text").status_code == 403
        task = s.tasks()[0]
        tid = task["task_id"]
        assert c.get("/api/conflicts").status_code == 403
        assert c.get("/api/task/" + tid + "?stage=retrieval").status_code == 403
        p = payload("source", task)
        assert c.post("/api/judgment/" + tid + "/source", json={"form": p}).status_code == 200
        assert c.get("/api/task/" + tid).json()["draft"]["payload"] == p
        assert (
            c.post("/api/judgment/" + tid + "/source", json={"form": p, "lock": True}).status_code
            == 200
        )
        assert (
            c.post("/api/judgment/" + tid + "/source", json={"form": p, "lock": True}).status_code
            == 409
        )
        assert s.profile("A")["human"] is False
        login(c, s, "B")
        assert c.get("/api/task/" + tid).json()["draft"] is None
        login(c, s, "C")
        assert c.get("/api/conflicts").status_code == 409
        assert c.post("/api/freeze", json={}).status_code == 409
        assert (
            c.post(
                "/api/main", json={"rights_confirmed": True, "calibration_reviewed": True}
            ).status_code
            == 409
        )
        assert c.post("/api/login", json={"role": "A", "code": "wrong"}).status_code == 403
        assert (
            c.post("/api/login", json={}, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
    restored = Store(tmp_path / "annotations.sqlite", prepare(), True)
    assert restored.latest("A", tid, "source")["payload"] == p
    with sqlite3.connect(s.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM events")


def test_two_complete_reviews_custom_adjudication_and_immutable_export(tmp_path):
    from research.gold_studio.store import STAGES

    b = prepare()
    b["selection"]["calibration"] = b["selection"]["calibration"][:2]
    s = Store(tmp_path / "test.sqlite", b, True)
    for role in ["A", "B", "C"]:
        with s.conn() as c:
            s.event(
                c,
                role,
                "",
                "profile",
                "profile",
                {
                    "name": "SYNTHETIC_" + role,
                    "qualification": "technical fixture",
                    "human": False,
                    "independent": False,
                    "developer": True,
                },
            )
    for role in ["A", "B"]:
        for stage in STAGES:
            for i, t in enumerate(s.tasks()):
                p = payload(
                    stage,
                    t,
                    "institutional" if role == "B" and i == 0 and stage == "source" else "official",
                )
                if stage == "fields":
                    p["relations"] = [
                        {
                            "subject": "SYNTHETIC_A",
                            "predicate": "FUNDS",
                            "object": "SYNTHETIC_P",
                            "evidence_spans": [],
                        },
                        {
                            "subject": "SYNTHETIC_P",
                            "predicate": "PART_OF",
                            "object": "SYNTHETIC_S",
                            "evidence_spans": [],
                        },
                    ]
                s.save(role, t["task_id"], stage, p, True)
    conflict = s.conflicts()[0]
    original = conflict["a"]["hash"]
    custom = {**conflict["a"]["payload"], "scope": "external"}
    s.adjudicate(
        conflict["task_id"],
        conflict["stage"],
        {"choice": "custom", "custom_payload": custom, "rationale": "Technical test only"},
    )
    assert s.latest("A", conflict["task_id"], conflict["stage"])["hash"] == original
    export = s.export()
    ref = next(j for j in export["adjudication"] if j["task_id"] == conflict["task_id"])[
        "final_reference"
    ]
    assert ref["studio_stages"]["source"]["scope"] == "external"
    assert len(ref["relations"]) == 2
    assert not any(row["independent_human_attestation"] for row in export["forms"]["A"])
    assert all(j["final_reference"]["component_ids"] for j in export["adjudication"])
    assert s.audit() == export["report"]["audit_head"]


def test_existing_metric_adapter_accepts_relational_schema_and_prefixed_ids(tmp_path):
    """Synthetic declarations test the schema adapter, never actual Human Gold."""
    import hashlib

    from research.bavaria.completion.metrics import canonical_hash, evaluate

    packet = tmp_path / "fixture"
    packet.mkdir()
    (packet / "originals").mkdir()
    body = b"<html><p>Exactly one synthetic fixture claim.</p></html>"
    sha = hashlib.sha256(body).hexdigest()
    original = "originals/" + sha + ".html"
    (packet / original).write_bytes(body)
    questions = [{"task_id": "completion:FIXTURE", "allowed_original_sha256": [sha]}]
    (packet / "questions.json").write_text(json.dumps(questions))
    (packet / "source_manifest.json").write_text(json.dumps([{"sha256": sha, "file": original}]))
    (packet / "PACKET.json").write_text(
        json.dumps(
            {
                "files_sha256": {
                    name: hashlib.sha256((packet / name).read_bytes()).hexdigest()
                    for name in ["questions.json", "source_manifest.json"]
                }
            }
        )
    )
    records = {}
    for reviewer in ["A", "B"]:
        row = {
            "task_id": "completion:FIXTURE",
            "reviewer_id": "SYNTHETIC_" + reviewer,
            "reviewer_qualification": "TECHNICAL_FIXTURE_NOT_ACTUAL_HUMAN",
            "independent_human_attestation": True,
            "originals_inspected": [sha],
            "answerability": "FULL",
        }
        records[reviewer] = row
        (packet / reviewer).mkdir()
        (packet / reviewer / "annotations.jsonl").write_text(json.dumps(row) + "\n")
    span = {
        "source_sha256": sha,
        "physical_page": None,
        "verbatim_quote": "Exactly one synthetic fixture claim.",
        "required_bundle": "group",
    }
    adjudication = {
        "task_id": "completion:FIXTURE",
        "adjudicator_id": "SYNTHETIC_C",
        "reviewer_A_record_sha256": canonical_hash(records["A"]),
        "reviewer_B_record_sha256": canonical_hash(records["B"]),
        "dependency_clearance": True,
        "rationale": "technical fixture only",
        "final_reference": {
            "source_scope_review_complete": True,
            "evidence_spans": [span],
            "component_ids": ["fixture-source"],
            "task_class": "fixture",
        },
    }
    (packet / "adjudication.jsonl").write_text(json.dumps(adjudication) + "\n")
    db = tmp_path / "fixture.sqlite"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE evidence(evidence_id TEXT,source_sha256 TEXT,text TEXT,page_ref TEXT)"
        )
        c.execute("INSERT INTO evidence VALUES(?,?,?,NULL)", ("unit", sha, span["verbatim_quote"]))
    runs = tmp_path / "runs.jsonl"
    runs.write_text(
        json.dumps({"task_id": "FIXTURE", "method": "fixture", "selected_evidence": ["unit"]})
        + "\n"
    )
    output = tmp_path / "metrics.json"
    evaluate(packet, db, runs, output)
    result = json.loads(output.read_text())
    assert result["rows"][0]["complete_known_bundle"] == 1
    assert "OWNER_SCIENTIFIC_RELEASE_PENDING" in result["status"]
