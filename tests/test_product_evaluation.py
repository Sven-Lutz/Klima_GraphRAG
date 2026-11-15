"""Isolated synthetic product-evaluation fixtures; never productive human judgments."""

import json
import math
import sqlite3
from types import SimpleNamespace

import pytest

from research.product_evaluation.catalog import (
    _components,
    build_catalog,
    load_questions,
    public_manifest,
    validate_catalog,
)
from research.product_evaluation.metrics import evaluate_case, summarize_results
from research.product_evaluation.runner import dashboard_payload, execute, render_dashboard


@pytest.fixture
def catalog(tmp_path):
    ledger = tmp_path / "synthetic.sqlite"
    municipalities = {
        f"09{i:06}": {"name": f"SYNTHETIC Gemeinde {i}", "region": f"Region {i % 7}"}
        for i in range(1200)
    }
    with sqlite3.connect(ledger) as c:
        c.executescript(
            "CREATE TABLE metadata(key,value);"
            "INSERT INTO metadata VALUES('schema','bavaria-acquisition-v1');"
            "CREATE TABLE municipalities(ags,record);"
            "CREATE TABLE documents(sha256);"
            "CREATE TABLE associations(ags,sha256);"
            "CREATE TABLE units(unit_id,sha256,text,ordinal);"
        )
        for i, ags in enumerate(municipalities):
            sha = f"synthetic-source-{i}"
            c.execute(
                "INSERT INTO municipalities VALUES(?,?)", (ags, json.dumps(municipalities[ags]))
            )
            c.execute("INSERT INTO documents VALUES(?)", (sha,))
            c.execute("INSERT INTO associations VALUES(?,?)", (ags, sha))
            if i % 8:
                c.execute(
                    "INSERT INTO units VALUES(?,?,?,?)",
                    (f"unit-{i}", sha, "SYNTHETIC: Wärmeplanung wird dokumentiert. " * 5, 1),
                )
    workspace = SimpleNamespace(
        db=ledger,
        source_databases=[],
        municipalities=municipalities,
        snapshot_id="bavaria-workspace-sha256:synthetic-not-real",
    )
    directory = tmp_path / "catalog"
    build_catalog(workspace, directory)
    return directory


def test_large_catalog_strata_and_holdout_sealing(catalog):
    public = public_manifest(catalog)
    assert public["questions"] == 1200
    assert public["splits"]["holdout"]["count"] >= 100
    assert 200 <= public["splits"]["human_candidates"]["count"] <= 300
    assert len(public["regions"]) == 7
    assert public["human_gold"] is False
    assert "question" not in json.dumps(public) or "questions" in public
    with pytest.raises(PermissionError, match="release receipt"):
        load_questions(catalog, "holdout")
    assert not (catalog / "holdout-access.jsonl").exists()
    development = load_questions(catalog, "development")
    assert all(r["answerability"] == "NOT_JUDGED" for r in development)
    assert any(r["evidence_availability"] == "NO_TOPIC_SEED" for r in development)
    assert len({r["question_id"] for r in development}) == len(development)
    assert len({r["semantic_intent_id"] for r in development}) == len(development)


def test_component_split_leakage_and_tamper_are_rejected(catalog):
    groups, _ = _components(
        ["a", "b", "c"],
        [
            {"ags": "a", "sha256": "shared"},
            {"ags": "b", "sha256": "shared"},
        ],
    )
    assert any(set(g) == {"a", "b"} for g in groups)
    rows = load_questions(catalog, "development")[:2]
    rows[1]["source_ids"] = rows[0]["source_ids"]
    rows[1]["split"] = "regression"
    with pytest.raises(ValueError, match="leakage"):
        validate_catalog(rows)
    path = catalog / "development.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        load_questions(catalog, "development")


def test_holdout_release_is_explicit_and_audited_only_in_synthetic_fixture(catalog):
    receipt = dict(
        evaluation_id="SYNTHETIC",
        owner="SYNTHETIC_QA_OWNER",
        frozen_method_sha256="not-real",
        purpose="independent_evaluation",
    )
    rows = load_questions(catalog, "holdout", release_receipt=receipt)
    assert len(rows) >= 100
    event = json.loads((catalog / "holdout-access.jsonl").read_text())
    assert event["event"] == "HOLDOUT_EXPOSURE"
    assert event["receipt"] == receipt


def test_standard_retrieval_metrics_and_unknown_denominators():
    output = dict(ranked_ids=["b", "a"], claims=[], abstained=False)
    ref = dict(label_origin="synthetic", qrels={"a": 3, "b": 0, "c": 2}, complete_universe=True)
    m = evaluate_case(output, ref, k=2)
    assert m["precision_at_k"]["value"] == 0.5
    assert m["recall_at_k"]["value"] == 0.5
    assert m["mrr"]["value"] == 0.5
    assert m["hit_at_k"]["value"] == 1
    assert m["ndcg_at_k"]["value"] == pytest.approx((7 / math.log2(3)) / (7 + 3 / math.log2(3)))
    assert m["answer_support"]["value"] is None
    incomplete = evaluate_case(output, {**ref, "complete_universe": False})
    assert incomplete["recall_at_k"]["value"] is None
    unjudged = evaluate_case({**output, "ranked_ids": ["foreign"]}, ref)
    assert unjudged["precision_at_k"]["value"] is None
    empty = evaluate_case({**output, "ranked_ids": []}, ref, k=8)
    assert empty["precision_at_k"]["value"] == 0
    assert empty["precision_returned"]["value"] is None


def test_claim_evaluation_abstention_multihop_and_origin_separation():
    output = dict(
        ranked_ids=["a", "b"], claims=[{"id": "one"}, {"id": "two"}], abstained=False, latency_ms=5
    )
    reviews = [
        dict(
            supported=True,
            citation_correct=True,
            scope_correct=True,
            temporal_valid=None,
            hallucinated=False,
        ),
        dict(
            supported=False,
            citation_correct=False,
            scope_correct=False,
            temporal_valid=False,
            hallucinated=True,
        ),
    ]
    ref = dict(
        label_origin="synthetic",
        claim_reviews=reviews,
        answerable=True,
        required_evidence_groups=[["a", "alternative"], ["b"]],
    )
    m = evaluate_case(output, ref)
    assert m["faithfulness"]["value"] == 0.5
    assert m["hallucination_rate"]["value"] == 0.5
    assert m["temporal_validity"]["denominator"] == 1
    assert m["abstention_quality"]["value"] == 1
    assert m["multi_hop_success"]["value"] == 1
    human = evaluate_case(output, {**ref, "label_origin": "independent_human"})
    summary = summarize_results(
        [dict(method="method", metrics=m), dict(method="method", metrics=human)]
    )
    assert len([row for row in summary["metrics"] if row["metric"] == "faithfulness"]) == 2
    with pytest.raises(ValueError, match="Every output claim"):
        evaluate_case(output, {**ref, "claim_reviews": []})
    with pytest.raises(ValueError, match="origin"):
        evaluate_case(output, {**ref, "label_origin": "gold-because-agent-said-so"})


def test_runner_resume_failure_provenance_and_safe_dashboard(catalog, tmp_path):
    output = tmp_path / "evaluation"
    calls = []

    def predict(row):
        calls.append(row["question_id"])
        return dict(
            ranked_ids=[],
            claims=[],
            abstained=True,
            input_tokens=0,
            output_tokens=0,
            provider_cost=0,
            provider="none",
            model="none",
            prompt_version="none",
        )

    report = execute(catalog, output, predict, limit=3)
    assert report["succeeded"] == 3 and report["failed"] == 0
    assert len(calls) == 3
    assert report["independent_gold_available"] is False
    execute(catalog, output, predict, limit=3)
    assert len(calls) == 3
    with pytest.raises(ValueError, match="Resume"):
        execute(catalog, output, predict, limit=2)
    with pytest.raises(PermissionError):
        execute(catalog, tmp_path / "holdout-run", predict, split="holdout")
    assert dashboard_payload(catalog, [output])["runs"][0]["cases"] == 3
    unsafe = render_dashboard(dict(metrics=[dict(metric="<script>bad</script>")]))
    assert "<script>bad" not in unsafe and "&lt;script&gt;bad" in unsafe

    def failing(_):
        raise ValueError("do not expose credential details")

    report = execute(catalog, tmp_path / "failed", failing, limit=1)
    assert report["failed"] == 1
    raw = (tmp_path / "failed/results.jsonl").read_text()
    assert "credential" not in raw and '"status": "failed"' in raw


def test_review_gold_bridge_preserves_origins_boundaries_and_incomplete_recall(catalog):
    from research.gold_studio.store import digest
    from research.product_evaluation.review_bridge import reference_from_review_export

    question = load_questions(catalog, "regression")[0]
    payload = dict(
        schema="klima-independent-review-v1",
        technical=True,
        human_gold=False,
        snapshot_id=question["snapshot_id"],
        purpose="SYNTHETIC_QA",
        gold_version="synthetic-v1",
        labels=[
            dict(
                task_id=question["question_id"],
                split="regression",
                kind="answer_evidence",
                sources=[dict(sha256="synthetic-source", unit_id="synthetic-unit")],
                relevance=2,
                human_gold=False,
                label_origin="synthetic_fixture",
                review_hashes=["synthetic-reviewer-A", "synthetic-reviewer-B"],
                final_event_hash="synthetic-adjudication",
            )
        ],
    )
    payload["content_sha256"] = digest(payload)
    with pytest.raises(ValueError, match="Technical"):
        reference_from_review_export(payload, [question])
    refs = reference_from_review_export(payload, [question], allow_technical=True)
    ref = refs[question["question_id"]]
    assert ref["label_origin"] == "synthetic" and ref["complete_universe"] is False
    metrics = evaluate_case(dict(ranked_ids=["synthetic-unit"], claims=[]), ref, k=1)
    assert metrics["hit_at_k"]["value"] == 1
    assert metrics["recall_at_k"]["value"] is None
    assert metrics["hit_at_k"]["label_origin"] == "synthetic"
    payload["labels"][0]["split"] = "holdout"
    with pytest.raises(ValueError, match="checksum"):
        reference_from_review_export(payload, [question], allow_technical=True)
    payload["content_sha256"] = digest({k: v for k, v in payload.items() if k != "content_sha256"})
    with pytest.raises(ValueError, match="split"):
        reference_from_review_export(payload, [question], allow_technical=True)


def evaluation_app(catalog, tmp_path, *, predictor=None):
    import threading
    from collections import deque

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from crawler.web.evaluation_dashboard import attach_evaluation_dashboard
    from crawler.web.review_protocol import ReviewProtocol

    snapshot = public_manifest(catalog)["snapshot_id"]
    app = FastAPI()
    app.state.query_lock = threading.Lock()
    app.state.recent_queries = deque(maxlen=20)
    app.state.query_budget_per_minute = 20
    app.state.response_timeout = 2
    app.state.review_protocol = ReviewProtocol(tmp_path / "users", snapshot, technical=True)
    p = app.state.review_protocol
    p.create_account(
        "owner",
        "synthetic_admin",
        "test-secret-long-admin",
        identity="Synthetic Admin",
        role="admin",
    )
    p.create_account(
        "owner", "synthetic_reviewer", "test-secret-long-reviewer", identity="Synthetic Reviewer"
    )

    class Product:
        retriever = SimpleNamespace(supported_modes=["lexical", "lexical_expanded", "graph"])

        def ask(self, options, owner):
            assert owner == "technical_evaluation" and options.generate_answer
            if predictor:
                return predictor(options, owner)
            return dict(
                retrieval_result={"items": []},
                evidence=[],
                answer_result={
                    "status": "insufficient_evidence",
                    "generation": {
                        "provider": "local_extractive",
                        "model": "synthetic-fixture",
                        "prompt_version": "synthetic-fixture",
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                },
                answer_assessment={"claims": []},
            )

    app.state.research_product = Product()
    attach_evaluation_dashboard(app, SimpleNamespace(database_snapshot_id=snapshot), catalog)
    client = TestClient(app)
    client.cookies.set(
        "review_protocol_session", p.login("synthetic_admin", "test-secret-long-admin")
    )
    return app, client


def test_dashboard_browsing_accounts_shared_rate_lock_and_runtime(catalog, tmp_path):
    app, client = evaluation_app(catalog, tmp_path)
    assert client.get("/evaluation").status_code == 200
    assert client.get("/api/evaluation/catalog").json()["questions"] == 1200
    assert client.get("/api/evaluation/questions?split=holdout").status_code == 403
    cases = client.get("/api/evaluation/questions?split=regression&limit=2").json()["items"]
    assert len(cases) == 2
    payload = dict(split="regression", method="lexical", question_ids=[cases[0]["question_id"]])
    app.state.query_lock.acquire()
    try:
        assert client.post("/api/evaluation/run", json=payload).status_code == 429
    finally:
        app.state.query_lock.release()
    assert len(app.state.recent_queries) == 0
    response = client.post("/api/evaluation/run", json=payload)
    assert response.status_code == 200 and response.json()["summary"]["succeeded"] == 1
    assert len(app.state.recent_queries) == 1
    assert not app.state.query_lock.locked()
    reports = client.get("/api/evaluation/runs").json()["items"]
    assert len(reports) == 1 and reports[0]["summary"]["independent_gold_available"] is False
    assert client.get("/api/evaluation/export/" + reports[0]["run_id"]).status_code == 200
    assert (
        client.post(
            "/api/evaluation/run", json={**payload, "question_ids": ["unknown"]}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/evaluation/run", json={**payload, "split": "holdout"}).status_code == 403
    )
    packet = client.get("/api/evaluation/annotation-packet?limit=2").json()
    assert packet["human_gold"] is False and len(packet["objects"]) == 2
    assert packet["objects"][0]["kind"] == "answer_evidence"
    assert packet["objects"][0]["sources"][0]["unit_id"]
    client.cookies.clear()
    assert client.post("/api/evaluation/run", json=payload).status_code == 403
    token = app.state.review_protocol.login("synthetic_reviewer", "test-secret-long-reviewer")
    client.cookies.set("review_protocol_session", token)
    assert client.post("/api/evaluation/run", json=payload).status_code == 403


def test_evaluation_timeout_keeps_worker_lock_until_real_completion(catalog, tmp_path):
    import threading

    finish = threading.Event()
    returned = threading.Event()

    def slow(options, owner):
        try:
            finish.wait(1)
            return dict(evidence=[], retrieval_result={"items": []})
        finally:
            returned.set()

    app, client = evaluation_app(catalog, tmp_path, predictor=slow)
    app.state.response_timeout = 0.005
    qid = load_questions(catalog, "regression")[0]["question_id"]
    with client:
        try:
            r = client.post("/api/evaluation/run", json={"question_ids": [qid]})
            assert r.status_code == 202
            pending = r.json()
            assert pending["status"] == "EVALUATION_PENDING"
            assert client.get(pending["poll_url"]).status_code == 202
            client.cookies.clear()
            assert client.get(pending["poll_url"]).status_code == 403
            p = app.state.review_protocol
            p.create_account(
                "owner",
                "other_admin",
                "other-test-secret-long",
                identity="Synthetic Other",
                role="admin",
            )
            client.cookies.set(
                "review_protocol_session", p.login("other_admin", "other-test-secret-long")
            )
            assert client.get(pending["poll_url"]).status_code == 404
            client.cookies.set(
                "review_protocol_session", p.login("synthetic_admin", "test-secret-long-admin")
            )
            assert app.state.query_lock.locked()
        finally:
            finish.set()
        assert returned.wait(1)
        assert app.state.query_lock.acquire(timeout=2)
        app.state.query_lock.release()
        completed = client.get(pending["poll_url"])
        assert completed.status_code == 200
        assert completed.json()["run_id"] == pending["run_id"]
        assert completed.json()["summary"]["succeeded"] == 1
        assert client.get(pending["poll_url"]).json() == completed.json()
        assert len(app.state.recent_queries) == 1
    assert returned.wait(1)
    # Completion includes metadata writes after the product call returns.
    assert app.state.query_lock.acquire(timeout=2)
    app.state.query_lock.release()


def test_pending_evaluation_worker_failure_is_safe_and_releases_lock(
    catalog, tmp_path, monkeypatch
):
    import threading

    finish = threading.Event()

    def fail(*args, **kwargs):
        finish.wait(2)
        raise RuntimeError("synthetic internal path must not leak")

    monkeypatch.setattr("crawler.web.evaluation_dashboard.execute", fail)
    app, client = evaluation_app(catalog, tmp_path)
    app.state.response_timeout = 0.005
    qid = load_questions(catalog, "regression")[0]["question_id"]
    with client:
        try:
            response = client.post("/api/evaluation/run", json={"question_ids": [qid]})
            assert response.status_code == 202
        finally:
            finish.set()
        assert app.state.query_lock.acquire(timeout=2)
        app.state.query_lock.release()
        failed = client.get(response.json()["poll_url"])
        assert failed.status_code == 500
        assert failed.json()["error"]["code"] == "evaluation_failed"
        assert "internal path" not in failed.text
        assert len(app.state.recent_queries) == 1


def test_review_derived_evaluation_reports_do_not_unblind_other_accounts(catalog, tmp_path):
    app, client = evaluation_app(catalog, tmp_path)
    question = load_questions(catalog, "regression")[0]
    run_id = "synthetic-private-reference"
    execute(
        catalog,
        app.state.evaluation_run_directory / run_id,
        lambda q: dict(ranked_ids=[]),
        selected_question_ids=[question["question_id"]],
        reference_by_id={question["question_id"]: dict(label_origin="synthetic", qrels={})},
    )
    assert len(client.get("/api/evaluation/runs").json()["items"]) == 1
    assert client.get("/api/evaluation/export/" + run_id).status_code == 200
    reference_id = "a" * 64
    reference_path = catalog / "review-references" / (reference_id + ".json")
    reference_path.parent.mkdir(exist_ok=True)
    reference_path.write_text(
        json.dumps(
            dict(
                split="regression",
                references={question["question_id"]: dict(label_origin="synthetic", qrels={})},
            )
        )
    )
    p = app.state.review_protocol
    p.create_account(
        "owner",
        "synthetic_judge",
        "test-secret-long-judge",
        identity="Synthetic Judge",
        role="adjudicator",
    )
    for username, password in [
        (None, None),
        ("synthetic_reviewer", "test-secret-long-reviewer"),
        ("synthetic_judge", "test-secret-long-judge"),
    ]:
        client.cookies.clear()
        if username:
            client.cookies.set("review_protocol_session", p.login(username, password))
        assert client.get("/api/evaluation/runs").json()["items"] == []
        assert client.get("/api/evaluation/export/" + run_id).status_code == 403
        assert (
            client.post(
                "/api/evaluation/run",
                json=dict(question_ids=[question["question_id"]], reference_id=reference_id),
            ).status_code
            == 403
        )
    assert len(app.state.recent_queries) == 0


def test_consolidated_reviews_automatically_materialize_versioned_partial_references(
    catalog, tmp_path
):
    from crawler.web.review_feedback import ReviewFeedback
    from crawler.web.review_protocol import ReviewProtocol
    from research.product_evaluation.catalog import public_manifest

    snapshot = public_manifest(catalog)["snapshot_id"]
    p = ReviewProtocol(
        tmp_path / "reviews",
        snapshot,
        technical=True,
        source_resolver=lambda source: {
            **source,
            "unit_id": "synthetic-rated-unit",
            "evidence_id": "b" * 64,
            "text": source["quote"],
        },
    )
    for name, role in [("first", "reviewer"), ("second", "reviewer"), ("third", "adjudicator")]:
        p.create_account(
            "owner",
            name,
            "isolated-test-secret-" + name,
            identity="Synthetic test " + name,
            role=role,
        )
    feedback = ReviewFeedback(p, catalog)
    task = load_questions(catalog, "regression")[0]
    oid = p.register(
        "owner",
        kind="answer_evidence",
        key="synthetic-auto-relevance",
        payload={"label": "Synthetic relevance task"},
        sources=[{"sha256": "a" * 64, "quote": "Synthetic exact passage"}],
        task_id=task["question_id"],
        split="regression",
    )
    p.assign("owner", oid, ["first", "second"], "third")
    form = dict(
        decision="SUPPORTED",
        comment="Synthetic transport fixture only",
        corrections={},
        independence_confirmed=True,
        evidence_inspected=True,
    )
    p.save("first", oid, form)
    assert not (feedback.directory / "latest.json").exists()
    result = p.save("second", oid, form)
    assert result["review_feedback"] == "SYNCED"
    receipt = json.loads((feedback.directory / "latest.json").read_text())
    reference_id = receipt["references"]["regression"]["reference_id"]
    bundle = json.loads((catalog / "review-references" / (reference_id + ".json")).read_text())
    ref = bundle["references"][task["question_id"]]
    assert ref["qrels"] == {"synthetic-rated-unit": 2}
    assert ref["label_origin"] == "synthetic" and ref["complete_universe"] is False
    assert receipt["human_gold"] is False and receipt["holdout_excluded"] is True
    versions = len(p._events("protocol_gold_version"))
    feedback.sync(oid)
    assert len(p._events("protocol_gold_version")) == versions
    assert p.view("first", oid)["assignment"] == {"own_assignment": True, "blind": True}
    assert not (catalog / "holdout-access.jsonl").exists()


@pytest.mark.parametrize("split", ["regression", "human_candidates"])
def test_automatic_review_reference_is_used_by_real_evaluation_endpoint(catalog, tmp_path, split):
    from crawler.web.review_feedback import ReviewFeedback

    def prediction(options, owner):
        return {
            "retrieval_result": {"items": []},
            "evidence": [{"source_id": "synthetic-rated-unit"}],
            "answer_assessment": {"claims": []},
        }

    app, client = evaluation_app(catalog, tmp_path, predictor=prediction)
    p = app.state.review_protocol
    p.source_resolver = lambda source: {
        **source,
        "unit_id": "synthetic-rated-unit",
        "text": source["quote"],
    }
    p.create_account(
        "owner", "synthetic_second", "test-secret-long-second", identity="Synthetic Second"
    )
    p.create_account(
        "owner",
        "synthetic_third",
        "test-secret-long-third",
        identity="Synthetic Third",
        role="adjudicator",
    )
    app.state.review_feedback = ReviewFeedback(p, catalog)
    task = load_questions(catalog, split)[0]
    oid = p.register(
        "owner",
        kind="answer_evidence",
        key="synthetic-auto-endpoint",
        payload={"label": "Synthetic bound relevance"},
        sources=[{"sha256": "a" * 64, "quote": "Synthetic passage"}],
        task_id=task["question_id"],
        split="human_annotation_candidate" if split == "human_candidates" else split,
    )
    p.assign("owner", oid, ["synthetic_reviewer", "synthetic_second"], "synthetic_third")
    f = dict(
        decision="SUPPORTED",
        comment="Synthetic only",
        corrections={},
        independence_confirmed=True,
        evidence_inspected=True,
    )
    p.save("synthetic_reviewer", oid, f)
    p.save("synthetic_second", oid, f)
    state = client.get("/api/evaluation/review-feedback").json()
    assert state["state"] == "READY" and state["human_gold"] is False
    result = client.post(
        "/api/evaluation/run",
        json={"split": split, "method": "lexical", "question_ids": [task["question_id"]]},
    ).json()
    metric = next(m for m in result["summary"]["metrics"] if m["metric"] == "hit_at_k")
    assert metric["value"] == 1 and metric["label_origin"] == "synthetic"
    assert result["summary"]["independent_gold_available"] is False
    assert not (catalog / "holdout-access.jsonl").exists()


def test_known_generation_failure_is_not_reported_as_unclassified_success():
    from crawler.web.evaluation_dashboard import normalize_product_result

    result = normalize_product_result(
        {
            "answer_result": {
                "status": "generation_failed",
                "generation": {"provider": "ollama_grounded"},
            },
            "answer_assessment": {"claims": []},
        }
    )
    assert result["errors"] == ["answer_synthesis"]
    assert result["provider_cost"] == 0


def test_bound_claim_judgments_never_transfer_to_changed_answers():
    output = dict(
        ranked_ids=["u1"],
        claims=[dict(id="c1", text="Synthetic claim", source_ids=["u1"])],
        answer_text_sha256="a" * 64,
        query_sha256="b" * 64,
        snapshot_id="synthetic-snapshot",
    )
    review = dict(
        claim_id="c1",
        text="Synthetic claim",
        source_ids=["u1"],
        answer_text_sha256="a" * 64,
        query_sha256="b" * 64,
        snapshot_id="synthetic-snapshot",
        judgments=dict(supported=True, citation_correct=False, temporal_valid=None),
    )
    ref = dict(label_origin="synthetic", bound_claim_reviews=[review])
    metrics = evaluate_case(output, ref)
    assert metrics["answer_support"]["value"] == 1
    assert metrics["citation_correctness"]["value"] == 0
    assert metrics["temporal_validity"]["value"] is None
    for field in ["answer_text_sha256", "query_sha256", "snapshot_id"]:
        changed = evaluate_case({**output, field: "changed"}, ref)
        assert changed["answer_support"]["value"] is None
    for claim in [
        dict(id="c1", text="changed", source_ids=["u1"]),
        dict(id="c1", text="Synthetic claim", source_ids=["foreign"]),
    ]:
        assert evaluate_case({**output, "claims": [claim]}, ref)["answer_support"]["value"] is None
    partial = evaluate_case({**output, "claims": output["claims"] + [dict(id="c2")]}, ref)
    assert partial["answer_support"]["denominator"] == 1
    assert partial["answer_support"]["label_origin"] == "synthetic"


def test_claim_gold_bridge_binds_answer_and_keeps_qrels_empty(catalog):
    import hashlib

    from research.gold_studio.store import digest
    from research.product_evaluation.review_bridge import reference_from_review_export

    question = load_questions(catalog, "regression")[0]
    label = dict(
        task_id=question["question_id"],
        split="regression",
        kind="claim",
        object_id="synthetic-object",
        sources=[dict(sha256="synthetic-source", unit_id="u1")],
        human_gold=False,
        label_origin="synthetic_fixture",
        review_hashes=["A", "B"],
        final_event_hash="synthetic-final",
        original_inspection_confirmed=True,
        payload=dict(
            question=question["question"],
            claim_id="c1",
            output_sha256="a" * 64,
            proposed_text="Synthetic claim [C1].",
        ),
        metric_judgments=dict(supported=True, citation_correct=False),
    )
    export = dict(
        schema="klima-independent-review-v1",
        technical=True,
        human_gold=False,
        snapshot_id=question["snapshot_id"],
        gold_version=1,
        labels=[label],
    )

    def seal():
        export["content_sha256"] = digest(
            {k: v for k, v in export.items() if k != "content_sha256"}
        )

    seal()
    refs = reference_from_review_export(export, [question], allow_technical=True)
    ref = refs[question["question_id"]]
    assert "qrels" not in ref
    output = dict(
        ranked_ids=["u1"],
        claims=[dict(id="c1", text="Synthetic claim", source_ids=["u1"])],
        answer_text_sha256="a" * 64,
        snapshot_id=question["snapshot_id"],
        query_sha256=hashlib.sha256(question["question"].encode()).hexdigest(),
    )
    assert evaluate_case(output, ref)["answer_support"]["value"] == 1
    assert evaluate_case(output, ref)["precision_at_k"]["value"] is None
    label["sources"].append(dict(sha256="synthetic-context-source", unit_id="u2"))
    label["payload"].update(cited_source_ids=["u1"], context_source_ids=["u2"])
    seal()
    context_ref = reference_from_review_export(export, [question], allow_technical=True)[
        question["question_id"]
    ]
    bound = context_ref["bound_claim_reviews"][0]
    assert bound["source_ids"] == ["u1"] and bound["context_source_ids"] == ["u2"]
    assert "qrels" not in context_ref
    assert evaluate_case(output, context_ref)["answer_support"]["value"] is None
    context_output = {**output, "claims": [{**output["claims"][0], "context_source_ids": ["u2"]}]}
    assert evaluate_case(context_output, context_ref)["answer_support"]["value"] == 1
    label["payload"]["context_source_ids"] = ["foreign"]
    seal()
    with pytest.raises(ValueError, match="partition"):
        reference_from_review_export(export, [question], allow_technical=True)
    label["payload"]["context_source_ids"] = ["u2"]
    label["payload"]["question"] = "Different question"
    seal()
    with pytest.raises(ValueError, match="binding"):
        reference_from_review_export(export, [question], allow_technical=True)


def test_normalized_claim_context_comes_from_actual_cited_graph_assertions():
    from crawler.web.evaluation_dashboard import normalize_product_result

    claim = dict(id="synthetic", text="Synthetic claim", source_ids=["u1"])
    payload = dict(
        evidence=[],
        retrieval_result=dict(
            items=[
                dict(source_id="u1", assertions=[dict(required_source_ids=["u1", "u2"])]),
                dict(source_id="u2", assertions=[dict(required_source_ids=["unrelated"])]),
            ]
        ),
        answer_assessment=dict(claims=[claim]),
    )
    normalized = normalize_product_result(payload)
    assert normalized["claims"][0]["context_source_ids"] == ["u2"]
    assert "context_source_ids" not in claim


def test_bound_claim_review_conflicts_and_uninspected_metrics_fail_closed():
    from crawler.web.review_protocol import metric_judgments

    with pytest.raises(ValueError, match="inspection"):
        metric_judgments(dict(metric_judgments={"supported": True}))
    with pytest.raises(ValueError, match="booleans"):
        metric_judgments(dict(evidence_inspected=True, metric_judgments={"supported": 1}))


def test_review_observations_are_operator_private_even_from_independent_reviewers(
    catalog, tmp_path
):
    from crawler.web.review_feedback import ReviewFeedback

    app, client = evaluation_app(catalog, tmp_path)
    p = app.state.review_protocol
    app.state.review_feedback = ReviewFeedback(p)
    response = client.get("/api/evaluation/review-observations")
    assert response.status_code == 200 and response.json()["cases"] == 0
    p.create_account(
        "owner",
        "synthetic_judge",
        "separate-judge-secret",
        identity="Synthetic Judge",
        role="adjudicator",
    )
    for name, password in [
        ("synthetic_reviewer", "test-secret-long-reviewer"),
        ("synthetic_judge", "separate-judge-secret"),
    ]:
        client.cookies.set("review_protocol_session", p.login(name, password))
        assert client.get("/api/evaluation/review-observations?download=true").status_code == 403
