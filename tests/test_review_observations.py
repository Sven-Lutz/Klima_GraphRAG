"""Synthetic free-research observations, separate from every evaluation catalog split."""

import hashlib

from test_review_protocol import SHA, form, protocol

from crawler.web.review_feedback import ReviewFeedback


def result():
    answer = "Technical statement [C1]."
    return dict(
        result_id="synthetic-result",
        snapshot="technical-snapshot",
        retrieval_result=dict(query="Synthetic free research question", items=[]),
        answer_result=dict(
            answer_text=answer, status="answered", generation=dict(provider="local_extractive")
        ),
        evidence=[dict(source_id="technical-unit")],
        answer_assessment=dict(
            claims=[
                dict(
                    id="synthetic-claim", text="Technical statement", source_ids=["technical-unit"]
                )
            ]
        ),
    )


def test_free_research_consolidation_evaluates_immutable_observation_without_catalog(tmp_path):
    p = protocol(tmp_path)
    feedback = ReviewFeedback(p)
    payload = result()
    observation = feedback.observations.capture(payload)
    oid = p.register(
        "owner",
        kind="claim",
        key="synthetic-free-answer",
        payload=dict(
            question=payload["retrieval_result"]["query"],
            claim_id="synthetic-claim",
            proposed_text=payload["answer_result"]["answer_text"],
            output_sha256=hashlib.sha256(
                payload["answer_result"]["answer_text"].encode()
            ).hexdigest(),
            review_observation_id=observation,
        ),
        sources=[dict(sha256=SHA, quote="Technical source passage")],
    )
    p.assign("owner", oid, ["alice", "bob"], "carol")
    for reviewer in ["alice", "bob"]:
        p.save(reviewer, oid, form(metric_judgments=dict(supported=True, citation_correct=False)))
    status = feedback.status()
    assert status["state"] == "READY"
    report = feedback.observations.report(status["gold_sha256"])
    assert report["cases"] == 1 and report["human_gold"] is False
    row = report["results"][0]
    assert row["metrics"]["answer_support"]["value"] == 1
    assert row["metrics"]["citation_correctness"]["value"] == 0
    assert row["metrics"]["temporal_validity"]["value"] is None
    assert row["metrics"]["answer_support"]["label_origin"] == "synthetic"
    assert "precision_at_k" in row["metrics"] and row["metrics"]["precision_at_k"]["value"] is None
    assert report["inference_invoked"] is False and report["catalog_modified"] is False
    assert feedback.observations.capture(payload) == observation


def test_observation_snapshot_mismatch_and_modified_cache_are_rejected(tmp_path):
    import pytest

    p = protocol(tmp_path)
    feedback = ReviewFeedback(p)
    with pytest.raises(ValueError, match="snapshot"):
        feedback.observations.capture({**result(), "snapshot": "foreign"})
    observation = feedback.observations.capture(result())
    path = feedback.observations.directory / "inputs" / (observation + ".json")
    path.write_text(path.read_text().replace("Technical statement", "Changed statement"))
    with pytest.raises(ValueError, match="integrity"):
        feedback.observations.load(observation)


def test_research_handoff_captures_only_session_owned_answer_without_session_secrets(tmp_path):
    import json

    from fastapi.testclient import TestClient
    from test_product_research import make_app

    from crawler.web.validation import attach_validation

    app = make_app(tmp_path)
    research = app.state.research_product
    attach_validation(
        app,
        research.service,
        research.service.workspace.discovery_index,
        tmp_path / "validation",
        True,
    )
    p = app.state.review_protocol
    feedback = app.state.review_feedback = ReviewFeedback(p)
    password = (
        (p.directory / "owner-credentials.txt").read_text().split("password: ")[1].splitlines()[0]
    )
    owner = TestClient(app)
    assert (
        owner.post(
            "/api/review-protocol/session", json=dict(username="owner", password=password)
        ).status_code
        == 200
    )
    response = owner.post(
        "/api/research/ask",
        json=dict(
            question="Welche Planung einer Wärmeversorgung ist dokumentiert?", mode="lexical"
        ),
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["answer_assessment"]["claims"]
    handoff = owner.post(
        "/api/review-protocol/handoff", json=dict(result_id=answer["result_id"], kind="claim")
    )
    assert handoff.status_code == 200, handoff.text
    obj = p.objects()[handoff.json()["object_id"]]
    observation_id = obj["payload"]["review_observation_id"]
    captured = feedback.observations.load(observation_id)
    assert captured["research_result_id"] == answer["result_id"]
    assert owner.cookies.get("klima_research_session") not in json.dumps(captured)
    stranger = TestClient(app)
    stranger.post("/api/review-protocol/session", json=dict(username="owner", password=password))
    other = stranger.post(
        "/api/review-protocol/handoff", json=dict(result_id=answer["result_id"], kind="claim")
    )
    assert other.status_code == 200
    assert "review_observation_id" not in p.objects()[other.json()["object_id"]]["payload"]
