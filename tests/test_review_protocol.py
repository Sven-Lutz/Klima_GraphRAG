"""Independent review lifecycle uses isolated synthetic users and source anchors."""

import json

import pytest
from fastapi.testclient import TestClient
from test_local_validation import product

from crawler.web.review_protocol import DECISIONS, KINDS, ReviewProtocol

SHA = "a" * 64


def resolver(source):
    if source["sha256"] != SHA or source.get("quote") != "Technical source passage":
        raise ValueError("Wrong synthetic source")
    return {**source, "unit_id": "technical-unit", "page_ref": "1", "text": source["quote"]}


def protocol(tmp_path, technical=True):
    p = ReviewProtocol(
        tmp_path / "protocol", "technical-snapshot", technical=technical, source_resolver=resolver
    )
    for name, role in [("alice", "reviewer"), ("bob", "reviewer"), ("carol", "adjudicator")]:
        p.create_account(
            "owner",
            name,
            "separate-secret-" + name,
            identity="technical individual " + name,
            role=role,
        )
    return p


def candidate(p, **kw):
    return p.register(
        "owner",
        kind="claim",
        key=kw.pop("key", "technical-claim"),
        payload={"label": "Technical proposed statement"},
        sources=[{"sha256": SHA, "quote": "Technical source passage"}],
        task_id="synthetic-task-1",
        **kw,
    )


def form(decision="SUPPORTED", **kw):
    return dict(
        decision=decision,
        comment="Synthetic transport test; no real human judgment",
        evidence_inspected=True,
        independence_confirmed=True,
        corrections={},
        **kw,
    )


def test_full_import_assignment_blind_review_conflict_adjudication_gold_and_restart(tmp_path):
    p = protocol(tmp_path)
    oid = candidate(p)
    assert p.assign("owner", oid, ["alice", "bob"], "carol") == "AWAITING_INDEPENDENT_REVIEWS"
    p.save("alice", oid, {"comment": "draft progress"}, draft=True)
    assert p.view("alice", oid)["history"][0]["payload"]["comment"] == "draft progress"
    p.save("alice", oid, form())
    assert p.view("alice", oid)["review_state"] == "SUBMITTED"
    assert p.view("bob", oid)["history"] == []
    assert p.view("bob", oid)["assignment"] == {"own_assignment": True, "blind": True}
    assert not any(e["kind"] == "protocol_review" for e in p.view("carol", oid)["history"])
    with pytest.raises(ValueError, match="Two independent"):
        p.adjudicate("carol", oid, form())
    p.save("bob", oid, form("WRONG_MUNICIPALITY"))
    assert p.state(oid) == "CONFLICT"
    assert p.queue("carol", status="CONFLICT")["total"] == 1
    with pytest.raises(PermissionError):
        p.adjudicate("alice", oid, form())
    assert p.export_gold("owner")["labels"] == []
    p.adjudicate("carol", oid, form("PARTIALLY_SUPPORTED"))
    assert p.state(oid) == "ADJUDICATED"
    exported = p.export_gold("owner")
    assert exported["gold_version"] == 2
    assert exported["labels"][0]["relevance"] is None
    assert exported["labels"][0]["decision"] == "PARTIALLY_SUPPORTED"
    assert not exported["human_gold"] and not exported["labels"][0]["human_gold"]
    assert exported["labels"][0]["label_origin"] == "synthetic_fixture"
    assert exported["labels"][0]["task_id"] == "synthetic-task-1"
    assert exported["labels"][0]["sources"][0]["unit_id"] == "technical-unit"
    assert p.agreement("owner")["overall"]["agreement"] == 0
    head = p.store.audit()
    reopened = ReviewProtocol(
        p.directory, "technical-snapshot", technical=True, source_resolver=resolver
    )
    assert reopened.store.audit() == head
    assert reopened.state(oid) == "ADJUDICATED"
    assert reopened.view("alice", oid)["history"] == p.view("alice", oid)["history"]
    assert reopened.audit_export("owner")["credentials_included"] is False


def test_individual_accounts_credentials_and_assignment_boundaries(tmp_path):
    p = protocol(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        p.create_account(
            "owner", "alias", "different-secret", identity="technical individual alice"
        )
    with pytest.raises(PermissionError):
        p.create_account("alice", "intruder", "different-secret", identity="intruder")
    with pytest.raises(PermissionError):
        p.login("alice", "wrong password")
    token = p.login("alice", "separate-secret-alice")
    assert p.session(token)["username"] == "alice"
    p.logout(token)
    with pytest.raises(PermissionError):
        p.session(token)
    oid = candidate(p)
    with pytest.raises(ValueError):
        p.assign("owner", oid, ["alice", "alice"], "carol")
    with pytest.raises(PermissionError):
        p.view("alice", oid)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    with pytest.raises(PermissionError):
        p.export_gold("alice")
    with pytest.raises(ValueError):
        p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    with pytest.raises(ValueError, match="immutable"):
        p.save("alice", oid, form("UNSUPPORTED"))


def test_production_requires_humans_and_independence_and_preserves_holdout(tmp_path):
    p = protocol(tmp_path, technical=False)
    oid = candidate(p, split="independent_holdout")
    p.assign("owner", oid, ["alice", "bob"], "carol")
    with pytest.raises(ValueError, match="personal"):
        p.save("alice", oid, form())
    p.save("alice", oid, form(human_confirmed=True))
    p.save("bob", oid, form(human_confirmed=True))
    assert p.state(oid) == "CONSENSUS"
    assert p.export_gold("owner", purpose="development")["labels"] == []
    gold = p.export_gold("owner")
    assert gold["human_gold"] and len(gold["labels"]) == 1
    oid2 = candidate(p, key="nonindependent")
    p.assign("owner", oid2, ["alice", "bob"], "carol")
    f = form(human_confirmed=True)
    f["independence_confirmed"] = False
    p.save("alice", oid2, f)
    p.save("bob", oid2, form(human_confirmed=True))
    assert p.state(oid2) == "INDEPENDENCE_NOT_ESTABLISHED"
    assert len(p.export_gold("owner")["labels"]) == 1


def test_all_object_kinds_anchors_versions_priorities_and_deferred_labels(tmp_path):
    p = protocol(tmp_path)
    assert len(KINDS) == 10 and len(DECISIONS) == 10
    for kind in KINDS:
        oid = p.register(
            "owner",
            kind=kind,
            key="case-" + kind,
            payload={"label": kind},
            sources=[{"sha256": SHA, "quote": "Technical source passage"}],
            signals={"uncertainty": 1, "information_gain": 0.5},
        )
        assert p.view("owner", oid)["kind"] == kind
    assert p.queue("owner")["total"] == 10
    assert p.queue("owner", kind="temporal")["total"] == 1
    with pytest.raises(ValueError, match="verif|Wrong|anchor"):
        p.register("owner", kind="claim", key="bad", payload={}, sources=[{"sha256": SHA}])
    with pytest.raises(ValueError, match="immutable"):
        p.register(
            "owner",
            kind="claim",
            key="case-claim",
            payload={"label": "changed"},
            sources=[{"sha256": SHA, "quote": "Technical source passage"}],
        )
    oid = candidate(p, key="deferred", version=2)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    for actor in ["alice", "bob"]:
        p.save(actor, oid, form("INSUFFICIENT_EVIDENCE"))
    gold = p.export_gold("owner")
    assert gold["labels"][0]["relevance"] is None
    assert gold["unresolved_relevance"] == 0
    assert gold["non_relevance_objects"] == 1


def login(c, username, password):
    response = c.post(
        "/api/review-protocol/session", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text


def test_http_accounts_source_bound_import_and_cross_user_restrictions(tmp_path):
    app, index = product(tmp_path)
    p = app.state.review_protocol
    password = (
        (p.directory / "owner-credentials.txt").read_text().split("password: ")[1].splitlines()[0]
    )
    owner, alice, bob = TestClient(app), TestClient(app), TestClient(app)
    assert owner.get("/api/review-protocol/queue").status_code == 401
    login(owner, "owner", password)
    for name, role in [("alice", "reviewer"), ("bob", "reviewer"), ("carol", "adjudicator")]:
        assert (
            owner.post(
                "/api/review-protocol/accounts",
                json={
                    "username": name,
                    "password": "separate-secret-" + name,
                    "identity": "technical " + name,
                    "role": role,
                },
            ).status_code
            == 200
        )
    with index.conn() as c:
        sha = c.execute("SELECT sha256 FROM sources LIMIT 1").fetchone()[0]
    request = dict(
        kind="claim",
        key="HTTP technical",
        payload={"label": "Technical only"},
        sources=[{"sha256": sha, "quote": "TECHNICAL Planung Waerme 2026"}],
    )
    created = owner.post("/api/review-protocol/import", json={"objects": [request]})
    assert created.status_code == 200, created.text
    oid = created.json()["object_ids"][0]
    assert (
        owner.post(
            "/api/review-protocol/assignment",
            json={"object_id": oid, "reviewers": ["alice", "bob"], "adjudicator": "carol"},
        ).status_code
        == 200
    )
    login(alice, "alice", "separate-secret-alice")
    login(bob, "bob", "separate-secret-bob")
    assert alice.get("/api/review-protocol/original/" + sha).status_code == 200
    assert (
        alice.post(
            "/api/review-protocol/review", json={"object_id": oid, "form": form()}
        ).status_code
        == 200
    )
    own = bob.get("/api/review-protocol/object/" + oid).json()
    assert own["history"] == []
    assert alice.get("/api/review-protocol/accounts").status_code == 403
    assert alice.post("/api/review-protocol/gold-export", json={}).status_code == 403
    assert (
        bob.post("/api/review-protocol/review", json={"object_id": oid, "form": form()}).status_code
        == 200
    )
    gold = owner.post("/api/review-protocol/gold-export", json={}).json()
    assert gold["technical"] and not gold["human_gold"] and gold["label_count"] == 1
    assert "password_hash" not in json.dumps(owner.get("/api/review-protocol/audit").json())


def test_failed_import_is_atomic_snapshot_binding_and_locked_review_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    p = protocol(tmp_path)
    valid = dict(
        kind="claim",
        key="atomic",
        payload={"label": "Atomic source-bound import"},
        sources=[{"sha256": SHA, "quote": "Technical source passage"}],
    )
    bad = {**valid, "key": "bad", "sources": [{"sha256": "b" * 64, "quote": "wrong source"}]}
    head = p.store.audit()
    with pytest.raises(ValueError):
        p.import_candidates("owner", [valid, bad])
    assert p.store.audit() == head and not p.objects()
    oid = p.import_candidates("owner", [valid])[0]
    assert p.import_candidates("owner", [valid]) == [oid]
    p.assign("owner", oid, ["alice", "bob"], "carol")

    def submit():
        try:
            p.save("alice", oid, form())
            return "saved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(outcomes) == ["rejected", "saved"]
    assert len(p._events("protocol_review", oid)) == 1
    with pytest.raises(ValueError, match="binding"):
        ReviewProtocol(p.directory, "different-snapshot", technical=True, source_resolver=resolver)


def test_new_material_supersedes_gold_and_credentials_revocation(tmp_path):
    p = protocol(tmp_path)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    for person in ["alice", "bob"]:
        p.save(person, oid, form())
    assert p.export_gold("owner")["label_count"] == 1
    new = candidate(p, version=2)
    assert new != oid and p.export_gold("owner")["label_count"] == 0
    assert len(p.view("alice", oid)["history"]) == 1
    token = p.login("alice", "separate-secret-alice")
    p.update_account("owner", "alice", active=False)
    with pytest.raises(PermissionError):
        p.session(token)
    p.update_account("owner", "alice", active=True, password="new-separate-secret")
    with pytest.raises(PermissionError):
        p.login("alice", "separate-secret-alice")
    assert p.session(p.login("alice", "new-separate-secret"))["username"] == "alice"


def test_http_handoff_is_actual_result_source_anchored_and_never_accepts_invented_claim(tmp_path):
    app, _ = product(tmp_path)
    p = app.state.review_protocol
    password = (
        (p.directory / "owner-credentials.txt").read_text().split("password: ")[1].splitlines()[0]
    )
    owner = TestClient(app)
    login(owner, "owner", password)
    result = owner.post(
        "/api/query",
        json={
            "question": "Welche Planung einer Wärmeversorgung ist dokumentiert?",
            "mode": "lexical",
        },
    ).json()
    assert result["evidence"]
    handoff = owner.post(
        "/api/review-protocol/handoff",
        json={"kind": "answer_evidence", "result_id": result["result_id"]},
    )
    assert handoff.status_code == 200, handoff.text
    candidate = p.objects()[handoff.json()["object_id"]]
    assert candidate["sources"][0]["unit_id"] == "fixture-unit"
    assert candidate["sources"][0]["quote"] == "TECHNICAL Planung Waerme 2026"
    assert not handoff.json()["human_gold"]
    assert (
        owner.post(
            "/api/review-protocol/handoff",
            json={"kind": "claim", "result_id": result["result_id"], "proposed_text": "invented"},
        ).status_code
        == 422
    )
    assert (
        owner.post(
            "/api/review-protocol/handoff",
            json={"kind": "source_conflict", "result_id": result["result_id"]},
        ).status_code
        == 422
    )
    assert (
        owner.post(
            "/api/review-protocol/handoff", json={"kind": "claim", "result_id": "invented-id"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("include_context", [True, False])
def test_claim_handoff_preserves_required_graph_context_separately(tmp_path, include_context):
    import sqlite3
    from dataclasses import replace

    from test_pilot_release import technical_source

    from crawler.core.answer_generation import AnswerResult, AnswerStatus
    from crawler.core.citations import build_citation_registry
    from crawler.core.retrieval import GraphAssertion
    from crawler.web.app import create_app
    from crawler.web.bavaria import create_bavaria_service
    from crawler.web.local_index import LocalIndex
    from crawler.web.validation import attach_validation
    from research.bavaria.workspace.core import make_manifest

    manifest, _ = technical_source(tmp_path)
    with sqlite3.connect(tmp_path / "input.sqlite") as connection:
        sha = connection.execute("SELECT sha256 FROM documents").fetchone()[0]
        connection.execute(
            "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
            ("context-unit", sha, 2, "Kein Gold.", None, None, "html"),
        )
    manifest = tmp_path / "context-workspace.json"
    make_manifest(tmp_path / "input.sqlite", None, manifest)
    service = create_bavaria_service(manifest, original_roots=[tmp_path])
    index = LocalIndex(service.workspace, tmp_path / "terms.sqlite")
    service.workspace.discovery_index = index
    app = create_app(service)
    attach_validation(app, service, index, tmp_path / "reviews", True)
    protocol = app.state.review_protocol
    password = (
        (protocol.directory / "owner-credentials.txt")
        .read_text()
        .split("password: ")[1]
        .splitlines()[0]
    )
    client = TestClient(app)
    login(client, "owner", password)
    result = client.post("/api/query", json=dict(question="Planung Waerme", mode="lexical")).json()
    retrieved = service.store.get(result["result_id"]).canonical
    first = next(item for item in retrieved.items if item.source_id == "fixture-unit")
    relation = GraphAssertion(
        "synthetic-relation",
        "synthetic-artifact",
        "synthetic-run",
        "s",
        "Synthetic subject",
        "CONTEXT_REQUIRING",
        "o",
        "Synthetic object",
        first.evidence_text,
        required_source_ids=("fixture-unit", "context-unit"),
    )
    first = replace(first, assertions=(relation,))
    context = replace(
        first,
        evidence_id="synthetic-context-evidence",
        source_id="context-unit",
        segment_id="context-unit",
        evidence_text="Kein Gold.",
        assertions=(),
    )
    retrieved = replace(retrieved, items=(first, context) if include_context else (first,))
    answer = AnswerResult(
        AnswerStatus.ANSWERED,
        "Synthetic statement [C1].",
        build_citation_registry((first,)).citations,
        retrieved,
        None,
        None,
        None,
        "synthetic-run",
        retrieved.database_snapshot_id,
        None,
    )
    result_id = service.store.put(answer)
    response = client.post(
        "/api/review-protocol/handoff", json=dict(kind="claim", result_id=result_id)
    )
    if not include_context:
        assert response.status_code == 422
        return
    assert response.status_code == 200, response.text
    obj = protocol.objects()[response.json()["object_id"]]
    assert {s["unit_id"] for s in obj["sources"]} == {"fixture-unit", "context-unit"}
    assert obj["payload"]["cited_source_ids"] == ["fixture-unit"]
    assert obj["payload"]["context_source_ids"] == ["context-unit"]


def test_actual_review_export_re_evaluation_keeps_truth_and_relevance_distinct(tmp_path):
    from research.product_evaluation.metrics import evaluate_case
    from research.product_evaluation.review_bridge import reference_from_review_export

    p = protocol(tmp_path)
    claim = candidate(p)
    mapping = p.register(
        "owner",
        kind="answer_evidence",
        key="technical-mapping",
        payload={"question": "Technical synthetic relevance question"},
        sources=[{"sha256": SHA, "quote": "Technical source passage"}],
        task_id="synthetic-task-1",
    )
    for oid in [claim, mapping]:
        p.assign("owner", oid, ["alice", "bob"], "carol")
        p.save("alice", oid, form())
        p.save("bob", oid, form("WRONG_MUNICIPALITY"))
        p.adjudicate("carol", oid, form("PARTIALLY_SUPPORTED"))
    exported = p.export_gold("owner")
    questions = [
        {
            "question_id": "synthetic-task-1",
            "snapshot_id": "technical-snapshot",
            "split": "human_candidates",
        }
    ]
    with pytest.raises(ValueError, match="Technical"):
        reference_from_review_export(exported, questions)
    refs = reference_from_review_export(exported, questions, allow_technical=True)
    assert refs["synthetic-task-1"]["review_provenance"] == [
        next(r["final_event_hash"] for r in exported["labels"] if r["kind"] == "answer_evidence")
    ]
    metrics = evaluate_case(
        {"ranked_ids": ["technical-unit"], "claims": []}, refs["synthetic-task-1"], k=1
    )
    assert metrics["hit_at_k"]["value"] == 1
    assert metrics["hit_at_k"]["label_origin"] == "synthetic"
    assert metrics["recall_at_k"]["value"] is None
    assert metrics["faithfulness"]["value"] is None


def test_consolidation_notifies_feedback_only_after_independent_final_decision(tmp_path):
    p = protocol(tmp_path)
    calls = []
    p.on_finalized = lambda oid: calls.append(oid)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    assert calls == []
    p.save("bob", oid, form())
    assert calls == [oid]


def test_feedback_failure_does_not_erase_locked_human_review(tmp_path):
    p = protocol(tmp_path)

    def unavailable(oid):
        raise OSError("Synthetic derived-store outage")

    p.on_finalized = unavailable
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    result = p.save("bob", oid, form())
    assert result["review_feedback"] == "DEFERRED"
    assert p.state(oid) == "CONSENSUS"
    assert len(p._events("protocol_feedback_failed", oid)) == 1


def test_feedback_overlay_is_separate_private_and_material_version_bound(tmp_path):
    import hashlib

    from crawler.web.review_feedback import ReviewFeedback

    p = protocol(tmp_path)
    feedback = ReviewFeedback(p)
    answer = "Technical proposed statement [C1]."
    body = {
        "claim_id": "synthetic-claim",
        "proposed_text": answer,
        "question": "Synthetic question",
        "output_sha256": hashlib.sha256(answer.encode()).hexdigest(),
    }
    source = [{"sha256": SHA, "quote": "Technical source passage"}]
    oid = p.register("owner", kind="claim", key="synthetic-answer", payload=body, sources=source)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    p.save("bob", oid, form())
    result = {
        "snapshot": "technical-snapshot",
        "retrieval_result": {"query": "Synthetic question"},
        "answer_result": {"answer_text": answer},
        "answer_assessment": {
            "claims": [
                {
                    "id": "synthetic-claim",
                    "text": "Technical proposed statement",
                    "source_ids": ["technical-unit"],
                }
            ]
        },
    }
    public = feedback.overlay(result)
    private = feedback.overlay(result, show_decisions=True)
    assert public["answer_assessment"]["claims"][0]["review"]["decision"] is None
    assert private["answer_assessment"]["claims"][0]["review"]["decision"] == "SUPPORTED"
    assert private["answer_assessment"]["claims"][0]["review"]["human_gold"] is False
    assert "review" not in result["answer_assessment"]["claims"][0]
    assert private["answer_result"] == result["answer_result"]
    changed_context = json.loads(json.dumps(result))
    changed_context["retrieval_result"]["items"] = [
        dict(
            source_id="technical-unit",
            assertions=[dict(required_source_ids=["technical-unit", "new-context"])],
        )
    ]
    assert "review" not in feedback.overlay(changed_context)["answer_assessment"]["claims"][0]
    p.register(
        "owner", kind="claim", key="synthetic-answer", payload=body, sources=source, version=2
    )
    assert feedback.status()["state"] == "STALE_MATERIAL_REVIEW_REQUIRED"
    assert "review" not in feedback.overlay(result)["answer_assessment"]["claims"][0]


def test_automatic_feedback_never_releases_independent_holdout(tmp_path):
    from crawler.web.review_feedback import ReviewFeedback

    p = protocol(tmp_path)
    feedback = ReviewFeedback(p)
    oid = candidate(p, split="independent_holdout")
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    r = p.save("bob", oid, form())
    assert r["review_feedback"] == "HELD_FOR_INDEPENDENT_EVALUATION"
    assert p._events("protocol_gold_version") == []
    assert not (feedback.directory / "latest.json").exists()


def test_claim_truth_export_is_not_a_passage_relevance_label(tmp_path):
    p = protocol(tmp_path)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    p.save("bob", oid, form())
    label = p.export_gold("owner")["labels"][0]
    assert label["kind"] == "claim" and label["decision"] == "SUPPORTED"
    assert label["relevance"] is None


def test_unavailable_original_can_be_deferred_without_false_inspection_confirmation(tmp_path):
    p = protocol(tmp_path, technical=False)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    deferred = {
        **form("INSUFFICIENT_EVIDENCE", human_confirmed=True),
        "comment": "Original unavailable; only metadata and supplied extract could be inspected.",
        "evidence_inspected": False,
    }
    assert p.save("alice", oid, deferred)["state"] == "SUBMITTED"
    with pytest.raises(ValueError, match="inspection"):
        p.save("bob", oid, {**form(human_confirmed=True), "evidence_inspected": False})
    p.save("bob", oid, deferred)
    label = p.export_gold("owner")["labels"][0]
    assert label["relevance"] is None
    assert p._latest("protocol_review", oid)["alice"]["payload"]["evidence_inspected"] is False


def test_claim_metric_disagreement_requires_adjudication(tmp_path):
    p = protocol(tmp_path)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    for actor, supported in [("alice", True), ("bob", False)]:
        p.save(actor, oid, {**form("SUPPORTED"), "metric_judgments": {"supported": supported}})
    assert p.state(oid) == "CONFLICT"
    p.adjudicate(
        "carol",
        oid,
        dict(
            decision="SUPPORTED",
            comment="Synthetic adjudication",
            evidence_inspected=True,
            metric_judgments={"supported": True, "citation_correct": False},
        ),
    )
    gold = p.export_gold("owner")
    assert gold["labels"][0]["metric_judgments"]["supported"] is True
    assert gold["labels"][0]["metric_judgments"]["citation_correct"] is False
    assert gold["labels"][0]["metric_judgments"]["temporal_valid"] is None


@pytest.mark.parametrize(
    "damage", ["missing_pointer", "corrupt_pointer", "missing_gold", "missing_observations"]
)
def test_feedback_retry_rebuilds_missing_derived_pointer_without_rewriting_reviews(
    tmp_path, damage
):
    from crawler.web.review_feedback import ReviewFeedback

    p = protocol(tmp_path)
    feedback = ReviewFeedback(p)
    oid = candidate(p)
    p.assign("owner", oid, ["alice", "bob"], "carol")
    p.save("alice", oid, form())
    p.save("bob", oid, form())
    locked = [e["hash"] for e in p._events("protocol_review", oid)]
    before = feedback.status()
    # Isolated synthetic derived-file damage; no original or real review data touched.
    pointer = feedback.directory / "latest.json"
    if damage == "missing_pointer":
        pointer.unlink()
    elif damage == "corrupt_pointer":
        pointer.write_text("{unfinished")
    elif damage == "missing_gold":
        (feedback.directory / "gold" / (before["gold_sha256"] + ".json")).unlink()
    else:
        (feedback.observations.directory / "reports" / (before["gold_sha256"] + ".json")).unlink()
    feedback.sync(oid)
    after = feedback.status()
    assert after["state"] == "READY"
    assert after["gold_version"] > before["gold_version"]
    assert locked == [e["hash"] for e in p._events("protocol_review", oid)]
