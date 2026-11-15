"""Synthetic graph fixtures test navigation; no fixture becomes production data or Gold."""

from types import SimpleNamespace

import pytest

from crawler.web.graph_explorer import GraphExplorer


def fixture_graph():
    entities = {
        k: {"label": k, "entity_type": "Organization", "identity_basis": "technical fixture"}
        for k in "abc"
    }
    relations = []
    evidence = {}
    for rid, sub, obj in [("r1", "a", "b"), ("r2", "b", "c")]:
        evidence[rid] = {"unit_id": "unit-" + rid, "page_ref": "2", "heading": "fixture"}
        relations.append(
            {
                "relation_id": rid,
                "subject_id": sub,
                "object_id": obj,
                "predicate": "FUNDS",
                "evidence_id": rid,
                "record": {
                    "source_sha256": "a" * 64,
                    "quote": "technical quote",
                    "reference_time": "2024",
                    "status": "PROVISIONAL",
                    "claim_scope_ags": [],
                },
            }
        )
    return SimpleNamespace(
        entities=entities,
        relation_rows=relations,
        evidence_rows=evidence,
        snapshot_id="fixture",
        manifest={"artifacts": {"relations": {"sha256": "b" * 64}}},
        relation_payload=lambda r: {"required_source_ids": ["unit-" + r["relation_id"]]},
    )


def test_graph_without_ordinal_is_navigable_and_has_stable_two_hop_chain():
    g = GraphExplorer(fixture_graph())
    r = g.subgraph("a", depth=2, target="c")
    assert r["chain"] == ["r1", "r2"]
    assert r["edges"][0]["locator"] == {"ordinal": None, "page": "2", "heading": "fixture"}
    assert r == g.subgraph("a", depth=2, target="c")
    assert g.subgraph("a", 1, target="c")["chain"] == []
    assert g.subgraph("a", 2, year=2023)["edges"] == []
    assert g.overview()["human_gold"] is False


def test_unknown_graph_scope_fails_and_filters_do_not_invent_edges():
    g = GraphExplorer(fixture_graph())
    with pytest.raises(ValueError):
        g.subgraph("missing")
    with pytest.raises(ValueError):
        g.subgraph("a", depth=4)
    assert not g.subgraph("a", predicate="DOES_NOT_EXIST")["edges"]


def test_navigation_is_independent_of_storage_order_and_preserves_all_edge_provenance():
    first = fixture_graph()
    second = fixture_graph()
    second.relation_rows.reverse()
    a = GraphExplorer(first).subgraph("a", 3, target="c")
    b = GraphExplorer(second).subgraph("a", 3, target="c")
    assert a == b
    for edge in a["edges"]:
        assert edge["source_sha256"] == "a" * 64
        assert edge["unit_id"] in edge["context_source_ids"]
        assert edge["validation"] == "PROVISIONAL"
    assert a["traversal"].startswith("Undirected")


def test_large_ego_network_is_bounded_and_explicitly_truncated():
    w = fixture_graph()
    for i in range(150):
        key = f"x{i:03}"
        w.entities[key] = {"label": key, "entity_type": "Project", "identity_basis": "fixture"}
        edge = dict(w.relation_rows[0])
        edge.update(relation_id=key, object_id=key)
        w.relation_rows.append(edge)
    result = GraphExplorer(w).subgraph("a", 3)
    assert len(result["nodes"]) <= 60
    assert len(result["edges"]) <= 100
    assert result["truncated"]


def test_expansion_is_explicit_deterministic_and_leaves_unrelated_queries_unchanged():
    from crawler.web.bavaria import expand_query

    assert expand_query("Wärmeplanung") == expand_query("WÄRMEPLANUNG")
    assert "nahwärmenetz" in expand_query("Wärmeplanung")
    assert "agri-photovoltaikanlage" in expand_query("Photovoltaik")
    assert expand_query("Haushaltssatzung") == ()


def test_pdf_status_requires_original_visual_context_notice():
    w = fixture_graph()
    w.relation_rows[0]["predicate"] = "HAS_REPORTED_STATUS"
    edges = GraphExplorer(w).subgraph("a")["edges"]
    assert next(e for e in edges if e["id"] == "r1")["visual_original_recommended"] is True
