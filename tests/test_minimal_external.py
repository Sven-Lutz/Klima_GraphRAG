"""Review projection drops whole raw containers, rather than blacklisting a few names."""

import copy
import json
from pathlib import Path

import pytest

from crawler.web.minimal_external import MinimalExternalPreview

CANDIDATE = (
    Path(__file__).resolve().parents[1] / "docs/pilot/minimal_external_preview/CANDIDATE_FACTS.json"
)


def projector():
    return MinimalExternalPreview(json.loads(CANDIDATE.read_text()))


def test_query_and_both_exports_do_not_forward_unknown_nested_source_text():
    p = projector()
    fact = next(iter(p.facts.values()))
    secret = "FORBIDDEN_ORIGINAL_CONTEXT_PRIVATE_GOLD"
    raw = {
        "result_id": "r",
        "retrieval_result": {"database_snapshot_id": "snapshot", "items": [secret]},
        "evidence": [
            {
                "source_id": "unit-1",
                "evidence_text": secret,
                "document": {
                    "document_id": "source-version:" + fact["source_sha256"],
                    "heading_context": secret,
                },
                "assertions": [{"evidence_excerpt": secret}],
                "future_field": secret,
            }
        ],
        "citation_registry": {"quote": secret},
        "graph": {"nodes": [{"label": secret}]},
    }
    out = p.query(raw, "Eigene Forschungsfrage")
    for fmt in ("json", "markdown"):
        assert secret not in p.export(out, fmt)
    assert out["source_leads"][0]["facts"][0]["presentation"].startswith("OWN_FACTUAL")
    assert out["release_status"] == "TECHNICAL_REVIEW_ONLY_NOT_DEPLOYABLE"
    assert secret in json.dumps(raw)
    with pytest.raises(ValueError, match="Canonical/raw"):
        p.export(raw, "json")


def test_graph_raw_labels_quotes_locators_and_unselected_edges_are_not_exported():
    p = projector()
    f = next(iter(p.facts.values()))
    secret = "UNAPPROVED_LABEL_QUOTE_AND_PATH"
    edge = {
        "id": f["relation_id"],
        "source": f["subject_id"],
        "target": f["object_id"],
        "source_sha256": f["source_sha256"],
        "predicate": f["predicate"],
        "quote": secret,
        "locator": {"heading": secret},
        "future_field": secret,
    }
    raw = {
        "snapshot_id": "s",
        "nodes": [{"label": secret}],
        "edges": [edge, {"id": "not_selected", "quote": secret}],
    }
    out = p.graph(raw)
    assert len(out["edges"]) == 1
    assert secret not in p.export(out, "json")
    assert secret not in p.export(out, "markdown")
    assert p.graph(raw) == out
    changed = copy.deepcopy(raw)
    changed["edges"][0]["source_sha256"] = "wrong"
    with pytest.raises(ValueError, match="does not match"):
        p.graph(changed)


def test_review_candidate_cannot_be_relabelled_as_deployment_approval():
    spec = json.loads(CANDIDATE.read_text())
    spec["deployment_approved"] = True
    with pytest.raises(ValueError, match="cannot accept"):
        MinimalExternalPreview(spec)


def test_even_contract_labelled_exports_reject_added_raw_or_modified_fact_fields():
    p = projector()
    f = next(iter(p.facts.values()))
    out = p.query(
        {
            "result_id": "r",
            "retrieval_result": {"database_snapshot_id": "s"},
            "evidence": [
                {
                    "source_id": "u",
                    "document": {"document_id": "source-version:" + f["source_sha256"]},
                }
            ],
        },
        "q",
    )
    altered = copy.deepcopy(out)
    altered["canonical_result"] = {"evidence_text": "MUST_NOT_LEAK"}
    with pytest.raises(ValueError, match="Unexpected field"):
        p.export(altered, "json")
    altered = copy.deepcopy(out)
    altered["source_leads"][0]["facts"][0]["source"]["raw_path"] = "/private/research"
    with pytest.raises(ValueError, match="Unregistered"):
        p.export(altered, "markdown")


def test_contract_name_alone_cannot_enable_the_production_factory():
    from crawler.web.pilot_bundle import valid_rights

    with pytest.raises(ValueError, match="Explicit minimal statutory assessment"):
        valid_rights({"source_presentation": "minimal_external_preview"})
