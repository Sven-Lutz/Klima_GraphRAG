"""Small synthetic contract checks. These are never human research labels."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from crawler.core.retrieval import RetrievalError, RetrievalMode, RetrievalRequest
from crawler.web.app import create_app
from crawler.web.bavaria import BavariaPreviewRetriever


def workspace(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    ledger.write_bytes(b"technical fixture")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    calls = []

    def discover(question, ags, k):
        calls.append((question, ags, k))
        return {
            "scope_ags": ags or [],
            "results": [
                {
                    "unit_id": "unit-1",
                    "sha256": "a" * 64,
                    "ordinal": 3,
                    "heading": "Planung",
                    "page_ref": "4",
                    "text": "Für 2024 geplant. Keine Aussage zur Umsetzung.",
                    "source_url": "https://example.org/quelle.pdf",
                    "source_frame_ags": ["09162111"],
                    "score": 1.2,
                }
            ],
        }

    return SimpleNamespace(
        municipalities={"09162111": {"name": "Technical fixture"}},
        db=ledger,
        source_databases=[],
        relations_db=None,
        manifest_path=manifest.resolve(),
        snapshot_id="technical-snapshot",
        discover=discover,
    ), calls


def request(w, **kwargs):
    return RetrievalRequest(
        query="Wärmeplanung",
        mode=RetrievalMode.LEXICAL,
        manifest_path=str(w.manifest_path),
        **kwargs,
    )


def test_lexical_adapter_preserves_identity_scope_and_original_text(tmp_path):
    w, calls = workspace(tmp_path)
    result = BavariaPreviewRetriever(w).retrieve(
        request(w, municipality_ids=("09162111",), topic="Förderung", reference_year=2024)
    )
    assert calls == [("Wärmeplanung Förderung", ["09162111"], 8)]
    assert result.items[0].document.document_id == "source-version:" + "a" * 64
    assert result.items[0].assertions == ()
    assert result.items[0].document.municipality_ids == ("09162111",)
    assert result.query == "Wärmeplanung"
    assert any("keine Gültigkeitsprüfung" in x for x in result.warnings)
    assert any("Quellenrahmen ist keine Aussagegeltung" in x for x in result.warnings)


def test_year_is_post_top_k_text_mention_not_zero_policy_claim(tmp_path):
    w, _ = workspace(tmp_path)
    result = BavariaPreviewRetriever(w).retrieve(request(w, reference_year=2030))
    assert not result.items
    assert "top-k" in result.warnings[-1]


def test_no_silent_semantic_fallback_and_snapshot_changes_fail_closed(tmp_path):
    w, _ = workspace(tmp_path)
    retriever = BavariaPreviewRetriever(w)
    with pytest.raises(RetrievalError, match="method_unavailable"):
        retriever.retrieve(replace(request(w), mode=RetrievalMode.SEMANTIC))
    w.db.write_bytes(b"changed")
    with pytest.raises(RetrievalError, match="snapshot_changed"):
        retriever.retrieve(request(w))


def test_filters_validate_real_ags_shape(tmp_path):
    w, _ = workspace(tmp_path)
    with pytest.raises((ValueError, RetrievalError)):
        request(w, municipality_ids=("not-ags",)).validate()


class TechnicalService:
    def status(self):
        return {"technical": True}

    def execute(self, options):
        return {"question": options.question, "technical": True}


def test_local_http_boundary_body_cap_and_no_internal_errors():
    client = TestClient(create_app(TechnicalService()))
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/status", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/query", content=b"x" * 24577).status_code == 413
    response = client.post("/api/query", json={"question": "Technischer Test"})
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "no-store" == response.headers["cache-control"]
    assert client.get("/api/source/missing/missing").status_code == 404


def test_query_budget_is_bounded():
    client = TestClient(create_app(TechnicalService()))
    for _ in range(12):
        assert client.post("/api/query", json={"question": "Technischer Test"}).status_code == 200
    assert client.post("/api/query", json={"question": "Technischer Test"}).status_code == 429


def test_interface_is_local_assets_and_no_automatic_truth_claim():
    root = Path(__file__).resolve().parents[1] / "crawler/web/static"
    js = (root / "app.js").read_text()
    assert "innerHTML" not in js
    assert "citation_resolution_success===true" in js
    assert "a.status==='answered'" in js
    html = (root / "index.html").read_text()
    assert "Noch kein freigegebenes unabhängiges Human Gold" in html
    assert 'aria-live="polite"' in html
    assert "https://cdn" not in html


def test_original_reader_is_hash_bound_size_limited_and_passive(tmp_path):
    import gzip
    import hashlib

    from crawler.web.bavaria import read_original

    data = (
        b"<html><body><script>alert(1)</script><form>secret</form>"
        b"<p>Technical source</p></body></html>"
    )
    path = tmp_path / "source.gz"
    path.write_bytes(gzip.compress(data))
    record = {"raw_path": str(path), "raw_bytes": len(data)}
    sha = hashlib.sha256(data).hexdigest()
    passive, media, _ = read_original(record, sha, [tmp_path])
    assert media == "text/html"
    assert b"<script" not in passive and b"<form" not in passive
    assert b"Technical source" in passive
    assert read_original(record, sha, [tmp_path], raw=True)[0] == data
    with pytest.raises(RetrievalError, match="integrity"):
        read_original(record, "0" * 64, [tmp_path])
    with pytest.raises(RetrievalError, match="not_available"):
        read_original(record, sha, [tmp_path / "other"])
    with pytest.raises(RetrievalError, match="not_available"):
        read_original({**record, "raw_bytes": 17 * 1024**2}, sha, [tmp_path])


def test_explicit_entity_navigation_does_not_admit_thematic_neighbours(tmp_path, monkeypatch):
    from crawler.core.retrieval import (
        DocumentSource,
        GraphAssertion,
        RetrievalResult,
        RetrievedEvidence,
    )

    w, _ = workspace(tmp_path)
    w.entities = {"actor": {"label": "Bundesministerium für Wirtschaft und Klimaschutz"}}

    def item(unit, actor):
        assertion = GraphAssertion(
            unit,
            "artifact",
            "run",
            actor,
            actor,
            "FUNDS",
            "project",
            "Technical project",
            "Technical quote",
        )
        return RetrievedEvidence(
            "technical-snapshot",
            unit,
            "segment",
            unit,
            unit,
            None,
            DocumentSource("doc", "https://example.org", "context", ()),
            "Technical quote",
            (),
            (),
            (assertion,),
            (),
            1,
            1,
        )

    a, b = item("one", "actor"), item("two", "other")
    monkeypatch.setattr(
        "crawler.web.bavaria.source_connection", lambda *args: SimpleNamespace(close=lambda: None)
    )
    w.relation_retrieval = lambda *args, **kwargs: (
        RetrievalResult("q", "graph", "technical-snapshot", "projection", (a, b)),
        {
            "scope_ags": [],
            "selected_relations": [
                {
                    "relation_id": "one",
                    "required_source_ids": ["one"],
                    "record": {"claim_scope_ags": ["09162111"], "reference_time": "2024"},
                },
                {"relation_id": "two", "required_source_ids": ["two"], "record": {}},
            ],
        },
    )
    result = BavariaPreviewRetriever(w).retrieve(
        replace(
            request(w),
            mode=RetrievalMode.GRAPH,
            query=(
                "Welche Beziehungen nennen die Quellen zu "
                "Bundesministerium für Wirtschaft und Klimaschutz?"
            ),
            projection_id="projection",
        )
    )
    assert [x.source_id for x in result.items] == ["one"]
    assert result.items[0].rank == 1
    assert result.items[0].assertions[0].required_source_ids == ("one",)
    assert result.items[0].assertions[0].reference_time == "2024"
    assert result.items[0].graph_artifact_ids == ("artifact",)


def test_http_timeout_keeps_single_worker_until_it_finishes():
    import threading
    import time

    entered, release = threading.Event(), threading.Event()

    class Slow(TechnicalService):
        def execute(self, options):
            entered.set()
            release.wait(1)
            return super().execute(options)

    with TestClient(create_app(Slow(), response_timeout=0.01)) as client:
        assert client.post("/api/query", json={"question": "Technischer Test"}).status_code == 504
        assert entered.is_set()
        assert client.post("/api/query", json={"question": "Technischer Test"}).status_code == 429
        release.set()
        for _ in range(20):
            time.sleep(0.01)
            result = client.post("/api/query", json={"question": "Technischer Test"})
            if result.status_code != 429:
                break
        assert result.status_code == 200


def test_immutable_export_store_is_bounded_and_does_not_overwrite():
    from crawler.web.service import ImmutableResultStore

    with pytest.raises(ValueError):
        ImmutableResultStore(capacity=0)
    store = ImmutableResultStore(capacity=2)
    one = store.put("technical-one")
    two = store.put("technical-two")
    three = store.put("technical-three")
    assert store.get(one) is None
    assert store.get(two).canonical == "technical-two"
    assert store.get(three).canonical == "technical-three"


def test_inferred_empty_scope_survives_canonical_json_and_markdown(tmp_path):
    from crawler.core.retrieval import render_retrieval_json, render_retrieval_markdown

    w, _ = workspace(tmp_path)
    w.discover = lambda *args, **kwargs: {"results": [], "scope_ags": ["09162111"]}
    result = BavariaPreviewRetriever(w).retrieve(request(w))
    assert result.search_scope.resolution == "from_query"
    assert result.search_scope.requested_ags == ()
    assert "09162111" in render_retrieval_json(result)
    rendered = render_retrieval_markdown(result)
    assert "09162111" in rendered and "technical-snapshot" in rendered


def test_archive_rejects_non_regular_targets_without_opening_them(tmp_path):
    import os

    from crawler.web.bavaria import read_original

    pipe = tmp_path / "not-an-original"
    os.mkfifo(pipe)
    with pytest.raises(RetrievalError, match="not_available"):
        read_original({"raw_path": str(pipe), "raw_bytes": 12}, "0" * 64, [tmp_path])
