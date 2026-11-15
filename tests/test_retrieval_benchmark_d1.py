"""Synthetic engineering inputs only; never Human Gold."""

import copy

import pytest

from research.gold_studio.retrieval_benchmark.contract import (
    digest,
    evidence_key,
    reviewer_candidates,
    union_pool,
    validate_qrels,
)


def fixture():
    task = {
        "task_id": "TECH-D1",
        "retrieval_class": "passage",
        "query_frozen": True,
        "query_sha256": digest("TEST QUERY"),
        "snapshot_id": "TEST-SNAPSHOT",
        "evidence_access_sha256": "TEST-ACCESS",
        "allowed_original_sha256": ["source-v1"],
    }
    row = {
        "evidence_id": "alias-1",
        "source_sha256": "source-v1",
        "locator": {"ordinal": 1, "page": 1},
        "text": "SYNTHETIC ORIGINAL EVIDENCE",
    }
    task["verified_evidence_ids"] = [evidence_key(row)]
    runs = [
        {
            **task,
            "method": method,
            "run_id": "run-" + method,
            "method_config": {"version": "SYNTHETIC-v1"},
            "information_condition": "shared_relations",
            "candidates": [row],
        }
        for method in ["bm25", "graph"]
    ]
    return task, row, runs


def test_order_invariance_and_lossless_union():
    t, e, runs = fixture()
    a = union_pool(t, [e], runs)
    assert a == union_pool(t, [e], list(reversed(runs)))
    assert len(a["candidates"]) == 1 and len(a["candidates"][0]["origins"]) == 3
    assert a["candidates"][0]["qrel"] is None
    view = reviewer_candidates(a)[0]
    assert not set(view) & {"method", "origins", "rank", "qrel", "score", "aliases"}


def test_versions_locations_and_aliases():
    t, e, runs = fixture()
    alternate = {**e, "evidence_id": "verified-location-alias"}
    p = union_pool(t, [e, alternate], [])
    assert p["candidates"][0]["aliases"] == ["alias-1", "verified-location-alias"]
    assert evidence_key(e) != evidence_key({**e, "locator": {"ordinal": 2}})
    assert evidence_key(e) != evidence_key({**e, "source_sha256": "source-v2"})
    with pytest.raises(ValueError):
        union_pool(t, [{**e, "text_sha256": "WRONG"}], [])


@pytest.mark.parametrize(
    "key", ["task_id", "retrieval_class", "query_sha256", "snapshot_id", "evidence_access_sha256"]
)
def test_incompatible_runs_rejected(key):
    t, e, runs = fixture()
    runs[0][key] = "OTHER"
    with pytest.raises(ValueError):
        union_pool(t, [e], runs)


def test_no_independent_query_no_method_pool():
    t, e, runs = fixture()
    t["query_frozen"] = False
    assert union_pool(t, [e], [])["status"] == "FOCAL_ONLY_NOT_METHOD_POOL"
    with pytest.raises(ValueError):
        union_pool(t, [e], runs)


def test_qrels_missingness_groups_and_negative_distinctions():
    task = {"pool": [{"id": "a"}, {"id": "b"}], "retrieval_class": "bundle"}
    p = {
        "relevance": {"a": "sufficient", "b": "contradictory"},
        "complete": "yes",
        "search_status": "not_searched",
        "qrel_groups": [{"aspect": "support or counterevidence", "alternatives": ["a", "b"]}],
    }
    validate_qrels(p, task)
    missing = copy.deepcopy(p)
    del missing["relevance"]["b"]
    with pytest.raises(ValueError):
        validate_qrels(missing, task)
    technical = copy.deepcopy(p)
    technical["relevance"]["b"] = "not_checkable"
    with pytest.raises(ValueError):
        validate_qrels(technical, task)
    for bad in [{**p, "qrel_groups": []}, {**p, "search_status": "not_found_in_searched_scope"}]:
        with pytest.raises(ValueError):
            validate_qrels(bad, task)


def test_opt_in_store_contract_preserves_legacy_relevance():
    from research.gold_studio.store import Store

    store = Store.__new__(Store)  # Pure form validation, no annotation database.
    task = {"pool": [{"id": "a"}], "qrel_contract": "retrieval-qrels-v1"}
    payload = {
        "original_inspected": True,
        "relevance": {"a": "contextual"},
        "complete": "unclear",
        "search_status": "not_searched",
        "qrel_groups": [],
    }
    store.validate("retrieval", payload, task)
    for value in ["sufficient", "partial", "irrelevant", "contradictory"]:
        store.validate("retrieval", {**payload, "relevance": {"a": value}}, task)
    legacy = {"pool": [{"id": "a"}]}
    store.validate("retrieval", {**payload, "relevance": {"a": "relevant"}}, legacy)
    with pytest.raises(ValueError):
        store.validate("retrieval", {**payload, "relevance": {"a": "relevant"}}, task)


def test_separate_identity_catalogue_cannot_be_replaced_by_similar_ids():
    t, e, runs = fixture()
    runs[0]["candidates"] = [{**e, "text": "OTHER CLAIM WITH SAME SOURCE AND ALIAS"}]
    with pytest.raises(ValueError, match="verified"):
        union_pool(t, [e], runs)
    del t["verified_evidence_ids"]
    with pytest.raises(ValueError, match="catalogue"):
        union_pool(t, [e], runs)
