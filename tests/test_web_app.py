"""Interactive application contracts over the canonical research backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crawler.core.answer_generation import (
    CanonicalAnswerGenerator,
    GenerationInput,
    ProviderResponse,
    ProviderTimeoutError,
)
from crawler.core.retrieval import RetrievalMode, RetrievalResult
from crawler.core.snapshots import validate_snapshot_manifest
from crawler.web.app import create_app
from crawler.web.fixture import FIXTURE_QUESTION, create_fixture_service
from crawler.web.runtime import LazyNeo4jReadBackend, create_production_service
from crawler.web.service import QueryOptions, ResearchApplicationService


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    return create_fixture_service(tmp_path_factory.mktemp("web-fixture"))


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def _query(client, *, mode="hybrid", generate_answer=True, question=FIXTURE_QUESTION):
    return client.post(
        "/api/query",
        json={
            "question": question,
            "mode": mode,
            "top_k": 8,
            "max_evidence_items": 8,
            "max_evidence_characters": 24000,
            "generate_answer": generate_answer,
        },
    )


def test_initial_state_is_explicit_and_provider_gate_is_visible(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "No query yet" in page.text
    assert "Retrieval-only exploration remains available" in page.text
    status = client.get("/api/status").json()
    assert status["provider_gate"]["acceptance_gate"] == "fixture_only_live_provider_open"
    assert "OPEN" in status["provider_gate"]["message"]


def test_fixture_mode_is_unambiguously_labeled(client):
    status = client.get("/api/status").json()
    assert status["demo_mode"] is True
    assert status["demo_notice"] == "Controlled fixture data — not real municipal research data."
    assert "demo-banner" in client.get("/").text


def test_retrieval_only_workflow_returns_canonical_result(client):
    response = _query(client, generate_answer=False)
    assert response.status_code == 200
    payload = response.json()
    assert payload["result_kind"] == "retrieval"
    assert payload["answer_result"] is None
    assert payload["retrieval_result"]["mode"] == "hybrid"
    assert payload["evidence"]


@pytest.mark.parametrize("mode", ["semantic", "graph", "hybrid"])
def test_all_supported_retrieval_modes_use_real_canonical_path(client, mode):
    payload = _query(client, mode=mode).json()
    assert payload["answer_result"]["status"] == "answered"
    assert payload["retrieval_result"]["mode"] == mode
    assert payload["retrieval_result"]["database_snapshot_id"].startswith(
        "snapshot-manifest-sha256:"
    )


def test_grounded_answer_render_contract_and_citation_mapping(client):
    payload = _query(client).json()
    assert payload["answer_result"]["grounding"]["citation_resolution_success"] is True
    cited_parts = [part for part in payload["inline_answer_parts"] if part["kind"] == "citation"]
    assert [part["label"] for part in cited_parts] == ["C1", "C2"]
    for part in cited_parts:
        matching = [
            item for item in payload["evidence"] if item["citation"]["label"] == part["label"]
        ]
        assert len(matching) == 1


def test_multiple_municipalities_and_segment_chunk_citations(client):
    payload = _query(client, mode="graph").json()
    assert {item["source_type"] for item in payload["evidence"]} == {"segment", "chunk"}
    assert any(len(item["document"]["municipality_ids"]) == 2 for item in payload["evidence"])
    assert any(item["citation"]["chunk_id"] is None for item in payload["evidence"])
    assert any(item["citation"]["chunk_id"] is not None for item in payload["evidence"])


def test_graph_assertion_selection_resolves_supporting_evidence(client):
    payload = _query(client, mode="graph").json()
    assertions = [edge for edge in payload["graph"]["edges"] if edge["kind"] == "assertion"]
    assert assertions
    evidence_ids = {item["evidence_id"] for item in payload["evidence"]}
    assert all(set(edge["evidence_ids"]) <= evidence_ids for edge in assertions)
    assert all(not node["id"].isdigit() for node in payload["graph"]["nodes"])
    assert "does not by itself prove truth" in payload["graph"]["notice"]


def test_no_source_url_is_fabricated(client):
    payload = _query(client).json()
    result_urls = {item["document"]["source_url"] for item in payload["evidence"]}
    citation_urls = {
        citation["source_url"] for citation in payload["citation_registry"]["citations"]
    }
    assert citation_urls <= result_urls
    javascript = client.get("/static/app.js").text
    assert 'item.document.source_url\n    ? el("a"' in javascript


def test_exports_reuse_canonical_markdown_and_json_renderers(client):
    payload = _query(client).json()
    markdown = client.get(f"/api/export/{payload['result_id']}.md")
    exported_json = client.get(f"/api/export/{payload['result_id']}.json")
    assert markdown.status_code == 200
    assert "# Answer" in markdown.text and "## Sources" in markdown.text
    assert exported_json.status_code == 200
    assert json.loads(exported_json.text)["status"] == "answered"
    missing = client.get("/api/export/result-missing.json")
    assert missing.status_code == 404


class ResponseProvider:
    name = "fixture"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def generate(self, generation_input: GenerationInput):
        if self.error:
            raise self.error
        return ProviderResponse(self.response, model=generation_input.model)


@pytest.mark.parametrize(
    ("provider", "status", "failure_code"),
    [
        (ResponseProvider("INSUFFICIENT_EVIDENCE: Missing detail."), "insufficient_evidence", None),
        (
            ResponseProvider("This factual sentence has no citation."),
            "grounding_failed",
            "grounding_validation_failed",
        ),
        (
            ResponseProvider(error=ProviderTimeoutError("Generation provider timed out")),
            "generation_failed",
            "provider_timeout",
        ),
        (
            ResponseProvider(error=ValueError("internal secret")),
            "generation_failed",
            "provider_error",
        ),
    ],
)
def test_answer_failure_and_empty_states_remain_typed(service, provider, status, failure_code):
    original = service.generator.providers
    service.generator.providers = {"fixture": provider}
    try:
        result = service.execute(
            QueryOptions(FIXTURE_QUESTION, RetrievalMode.SEMANTIC, 8, 8, 24000, True)
        )
    finally:
        service.generator.providers = original
    assert result["answer_result"]["status"] == status
    if failure_code:
        assert result["answer_result"]["failure"]["code"] == failure_code
    if status == "grounding_failed":
        assert result["answer_result"]["answer_text"] == "This factual sentence has no citation."


def test_provider_unavailable_is_a_canonical_answer_result(service):
    original = service.generator.providers
    service.generator.providers = {}
    try:
        result = service.execute(
            QueryOptions(FIXTURE_QUESTION, RetrievalMode.SEMANTIC, 8, 8, 24000, True)
        )
    finally:
        service.generator.providers = original
    assert result["answer_result"]["status"] == "generation_failed"
    assert result["answer_result"]["failure"]["code"] == "provider_unavailable"
    assert result["evidence"]


class EmptyRetriever:
    def retrieve(self, request):
        return RetrievalResult(
            query=request.query,
            mode=request.mode.value,
            database_snapshot_id=self.snapshot_id,
            projection_id=request.projection_id,
            items=(),
        )


def test_no_evidence_becomes_canonical_insufficient_result(service):
    retriever = EmptyRetriever()
    retriever.snapshot_id = service.database_snapshot_id
    generator = CanonicalAnswerGenerator(
        retriever=retriever,
        providers=service.generator.providers,
        run_recorder=service.generator.run_recorder,
    )
    isolated = ResearchApplicationService(
        retriever=retriever,
        generator=generator,
        manifest_path=service.manifest_path,
        database_snapshot_id=service.database_snapshot_id,
        provider_gate=service.provider_gate,
        demo_mode=True,
    )
    result = isolated.execute(
        QueryOptions("No evidence question", RetrievalMode.SEMANTIC, 8, 8, 24000, True)
    )
    assert result["answer_result"]["status"] == "insufficient_evidence"
    assert result["evidence"] == []


def test_malformed_configuration_and_safe_unexpected_errors(client):
    malformed = client.post("/api/query", json={"question": "", "top_k": 0})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "malformed_configuration"

    class UnsafeFailureService:
        def status(self):
            return {}

        def execute(self, _options):
            raise RuntimeError("postgres://admin:secret@production.internal/database")

        def export(self, _result_id, _format_name):
            return None

    safe_client = TestClient(create_app(UnsafeFailureService()))
    response = _query(safe_client, generate_answer=False)
    assert response.status_code == 500
    assert "secret" not in response.text
    assert "production.internal" not in response.text
    assert response.json()["error"]["code"] == "backend_failure"


def test_presentation_layer_has_no_retrieval_database_or_provider_logic():
    root = Path(__file__).resolve().parents[1]
    presentation = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "crawler/web/static/app.js",
            "crawler/web/static/index.html",
            "crawler/web/static/app.css",
        )
    ).lower()
    for prohibited in ("sqlite", "cypher", "ollama", "select *", "neo4j internal"):
        assert prohibited not in presentation


def test_production_composition_requires_exact_database_and_defers_neo4j(
    service, tmp_path, monkeypatch
):
    binding = validate_snapshot_manifest(service.manifest_path)
    monkeypatch.setenv("KLIMA_UI_SNAPSHOT_MANIFEST", service.manifest_path)
    monkeypatch.setenv("KLIMA_UI_DB_PATH", str(binding.database.path))
    monkeypatch.setenv("KLIMA_UI_ANSWER_RUN_LOG", str(tmp_path / "answer-runs.jsonl"))
    monkeypatch.setenv("KLIMA_UI_NEO4J_ENABLED", "1")
    monkeypatch.setenv("KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED", "1")
    monkeypatch.setenv("KLIMA_RETRIEVAL_NEO4J_URI", "bolt://127.0.0.1:17687")
    monkeypatch.setenv("KLIMA_RETRIEVAL_NEO4J_USER", "fixture-readonly")
    monkeypatch.setenv("KLIMA_RETRIEVAL_NEO4J_PASSWORD", "fixture-password")
    monkeypatch.setenv("KLIMA_RETRIEVAL_NEO4J_DATABASE", "neo4j")

    production = create_production_service()

    assert production.status()["provider_gate"]["availability"] == "retrieval_only_ready"
    assert isinstance(production.retriever.graph_backend, LazyNeo4jReadBackend)
    assert production.retriever.graph_backend._backend is None

    monkeypatch.setenv("KLIMA_UI_DB_PATH", str(tmp_path / "wrong.sqlite"))
    with pytest.raises(RuntimeError, match="exactly match"):
        create_production_service()
