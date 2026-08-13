"""Opt-in contract test for a disposable Neo4j 5 service.

Run only with the repository's disposable Compose fixture (see
``tests/integration/README.md``). No production endpoint is accepted implicitly.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

from crawler.core.answer_generation import (
    AnswerRequest,
    AnswerStatus,
    CanonicalAnswerGenerator,
    GenerationParameters,
    ProviderResponse,
)
from crawler.core.database import connect_existing_read_only
from crawler.core.neo4j_projection import (
    Neo4jProjectionWriter,
    Neo4jTarget,
    ProjectionConfiguration,
    ProjectionError,
    ProjectionLedger,
    build_projection_plan,
    execute_projection,
)
from crawler.core.retrieval import (
    CanonicalRetriever,
    Neo4jReadBackend,
    RetrievalMode,
    RetrievalRequest,
)
from tests.test_neo4j_projection import projection_fixture
from tests.test_retrieval import FixtureEmbedder, _fixture

pytestmark = [pytest.mark.network, pytest.mark.neo4j_integration]


def _live_configuration() -> tuple[ProjectionConfiguration, str]:
    if os.environ.get("GRAPHRAG_NEO4J_TEST_DISPOSABLE") != "1":
        pytest.skip("disposable Neo4j integration service was not explicitly enabled")
    uri = os.environ.get("GRAPHRAG_NEO4J_TEST_URI", "bolt://127.0.0.1:17687")
    if urlsplit(uri).hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("live projection tests only accept an explicitly disposable loopback service")
    password = os.environ.get("GRAPHRAG_NEO4J_TEST_PASSWORD", "projection-test-password")
    return (
        ProjectionConfiguration(
            target=Neo4jTarget(uri, "neo4j", "neo4j"),
            batch_size=1,
            query_timeout_seconds=15,
        ),
        password,
    )


def test_disposable_neo4j_clean_repeat_and_constraints(tmp_path):
    configuration, password = _live_configuration()
    binding, run_ids = projection_fixture(tmp_path)
    plan = build_projection_plan(binding, run_ids, configuration)
    ledger = ProjectionLedger(tmp_path / "live-projection-ledger.sqlite")
    writer = Neo4jProjectionWriter(configuration, password=password)
    try:
        first = execute_projection(plan, configuration, ledger, writer)
        first_counts = writer.namespace_counts(plan.projection_id)
        second = execute_projection(plan, configuration, ledger, writer)
        second_counts = writer.namespace_counts(plan.projection_id)
        with writer.driver.session(database=configuration.target.database) as session:
            constraint_count = session.run(
                "SHOW CONSTRAINTS YIELD properties "
                "WHERE properties = ['projection_key'] RETURN count(*) AS count"
            ).single()["count"]
            required_indexes = session.run(
                """
                SHOW INDEXES YIELD name
                WHERE name IN ['entity_canonical_id', 'evidence_canonical_id',
                               'assertion_relation_type', 'document_canonical_id']
                RETURN collect(name) AS names
                """
            ).single()["names"]
            assertion_paths = session.run(
                """
                MATCH (:GraphArtifact {projection_id:$projection_id})-[:HAS_ASSERTION]->
                      (a:Assertion)-[:SUPPORTED_BY]->(e:Evidence)-[:DERIVED_FROM]->(source)
                RETURN count(a) AS count,
                       count(DISTINCT a.extraction_run_id) AS run_count,
                       count(DISTINCT e.evidence_id) AS evidence_count,
                       collect(DISTINCT labels(source)[0]) AS source_labels
                """,
                projection_id=plan.projection_id,
            ).single()
    finally:
        writer.close()
        ledger.close()

    assert first["status"] == second["status"] == "completed"
    assert first_counts == second_counts == dict(plan.expected_node_counts)
    assert constraint_count >= len(plan.expected_node_counts)
    assert set(required_indexes) == {
        "entity_canonical_id",
        "evidence_canonical_id",
        "assertion_relation_type",
        "document_canonical_id",
    }
    assert assertion_paths["count"] == 4
    assert assertion_paths["run_count"] == 3
    assert assertion_paths["evidence_count"] == 2
    assert set(assertion_paths["source_labels"]) == {"Segment", "Chunk"}


def test_disposable_neo4j_partial_failure_resume_and_scope_coexistence(tmp_path):
    configuration, password = _live_configuration()
    binding, run_ids = projection_fixture(tmp_path)
    plan = build_projection_plan(binding, run_ids, configuration)
    narrower = build_projection_plan(binding, run_ids[:1], configuration)
    ledger = ProjectionLedger(tmp_path / "live-resume-ledger.sqlite")
    real_writer = Neo4jProjectionWriter(configuration, password=password)

    class FailSecondBatchOnce:
        def __init__(self):
            self.failed = False

        def ensure_schema(self):
            real_writer.ensure_schema()

        def database_identity(self):
            return real_writer.database_identity()

        def initialize_scope(self, selected_plan):
            real_writer.initialize_scope(selected_plan)

        def write_batch(self, selected_plan, batch):
            if batch.ordinal == 1 and not self.failed:
                self.failed = True
                raise RuntimeError("injected interruption between live transactions")
            real_writer.write_batch(selected_plan, batch)

        def namespace_counts(self, projection_id):
            return real_writer.namespace_counts(projection_id)

        def close(self):
            pass

    failing_writer = FailSecondBatchOnce()
    try:
        with pytest.raises(ProjectionError, match="batch 1 failed"):
            execute_projection(plan, configuration, ledger, failing_writer)
        failed = ledger.record(plan.projection_id)
        completed = execute_projection(plan, configuration, ledger, failing_writer)
        narrow_completed = execute_projection(narrower, configuration, ledger, real_writer)
        full_counts = real_writer.namespace_counts(plan.projection_id)
        narrow_counts = real_writer.namespace_counts(narrower.projection_id)
    finally:
        real_writer.close()
        ledger.close()

    assert failed["status"] == "failed"
    assert failed["projected_count"] == 1
    assert completed["status"] == narrow_completed["status"] == "completed"
    assert full_counts == dict(plan.expected_node_counts)
    assert narrow_counts == dict(narrower.expected_node_counts)
    assert plan.projection_id != narrower.projection_id


def test_disposable_neo4j_bounded_retrieval_resolves_sqlite_provenance(tmp_path):
    configuration, password = _live_configuration()
    binding, run_ids = projection_fixture(tmp_path)
    plan = build_projection_plan(binding, run_ids, configuration)
    ledger = ProjectionLedger(tmp_path / "live-retrieval-ledger.sqlite")
    writer = Neo4jProjectionWriter(configuration, password=password)
    backend = Neo4jReadBackend(writer.driver, database=configuration.target.database)

    class Provider:
        name = "fixture"

        def generate(self, generation_input):
            assert generation_input.evidence_context
            return ProviderResponse(
                "Live graph evidence is documented [C1] [C2].",
                model=generation_input.model,
            )

    class Recorder:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

    try:
        execute_projection(plan, configuration, ledger, writer)
        retrieval_request = RetrievalRequest(
            query="Stadtwerke Solaranlage",
            manifest_path=str(binding.manifest_path),
            mode=RetrievalMode.GRAPH,
            top_k=10,
            projection_id=plan.projection_id,
            extraction_run_ids=plan.extraction_run_ids,
        )
        retriever = CanonicalRetriever(graph_backend=backend)
        result = retriever.retrieve(retrieval_request)
        recorder = Recorder()
        answer = CanonicalAnswerGenerator(
            retriever=retriever,
            providers={"fixture": Provider()},
            run_recorder=recorder,
        ).generate(
            AnswerRequest(
                question=retrieval_request.query,
                retrieval_request=retrieval_request,
                database_snapshot_id=plan.snapshot_id,
                generation_provider="fixture",
                generation_model="fixture-answer-model",
                generation_parameters=GenerationParameters(seed=42),
            )
        )
        with writer.driver.session(database=configuration.target.database) as session:
            representative = session.run(
                """
                MATCH (p:Projection {projection_id:$projection_id})-[:PROJECTS_SNAPSHOT]->
                      (snapshot:DatabaseSnapshot)
                MATCH (p)-[:INCLUDES_RUN]->(run:ExtractionRun)-[:PRODUCED]->
                      (artifact:GraphArtifact)-[:HAS_ASSERTION]->(assertion:Assertion)-
                      [:SUPPORTED_BY]->(evidence:Evidence)-[:FROM_DOCUMENT]->(document:Document)
                MATCH (evidence)-[:DERIVED_FROM]->(source)
                MATCH (evidence)-[:ASSOCIATED_WITH]->(municipality:Municipality)
                WITH snapshot, run, artifact, assertion, evidence, document, source,
                     collect(DISTINCT municipality.municipality_id) AS municipality_ids
                RETURN snapshot.snapshot_id AS snapshot_id, run.run_id AS run_id,
                       artifact.artifact_id AS artifact_id,
                       assertion.assertion_id AS assertion_id,
                       evidence.evidence_id AS evidence_id,
                       document.document_id AS document_id,
                       labels(source)[0] AS source_label,
                       municipality_ids, keys(evidence) AS evidence_properties
                ORDER BY evidence_id, assertion_id LIMIT 1
                """,
                projection_id=plan.projection_id,
            ).single()
    finally:
        # The backend and writer share one driver; close it exactly once.
        writer.close()
        ledger.close()

    assert {item.source_type for item in result.items} == {"segment", "chunk"}
    assert answer.status == AnswerStatus.ANSWERED
    assert {citation.source_type for citation in answer.citations} == {"segment", "chunk"}
    assert recorder.records[-1].status == "succeeded"
    assert all(item.assertions for item in result.items)
    assert all(item.database_snapshot_id == plan.snapshot_id for item in result.items)
    assert representative["snapshot_id"] == plan.snapshot_id
    assert representative["run_id"] in plan.extraction_run_ids
    assert representative["artifact_id"] in {artifact["artifact_id"] for artifact in plan.artifacts}
    assert representative["assertion_id"]
    assert representative["source_label"] in {"Segment", "Chunk"}
    assert set(representative["municipality_ids"]) == {"muni-1", "muni-2"}
    assert "evidence_text" not in representative["evidence_properties"]
    assert "source_text" not in representative["evidence_properties"]

    with connect_existing_read_only(binding.database) as connection:
        for item in result.items:
            evidence = connection.execute(
                """
                SELECT source_type, source_id, segment_id, chunk_id, document_id,
                       source_url, evidence_text
                FROM evidence_snapshots WHERE evidence_id=?
                """,
                (item.evidence_id,),
            ).fetchone()
            municipalities = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT municipality_id FROM evidence_municipalities "
                    "WHERE evidence_id=? ORDER BY municipality_id",
                    (item.evidence_id,),
                )
            )
            assert evidence == (
                item.source_type,
                item.source_id,
                item.segment_id,
                item.chunk_id,
                item.document.document_id,
                item.document.source_url,
                item.evidence_text,
            )
            assert municipalities == item.document.municipality_ids


def test_disposable_neo4j_hybrid_grounded_answer_path(tmp_path):
    configuration, password = _live_configuration()
    _source, binding, _unused_plan, _embedding_artifact_id = _fixture(tmp_path)
    plan = build_projection_plan(binding, ("segment-run", "chunk-run"), configuration)
    ledger = ProjectionLedger(tmp_path / "live-hybrid-answer-ledger.sqlite")
    writer = Neo4jProjectionWriter(configuration, password=password)
    backend = Neo4jReadBackend(writer.driver, database=configuration.target.database)

    class Provider:
        name = "fixture"

        def __init__(self):
            self.inputs = []

        def generate(self, generation_input):
            self.inputs.append(generation_input)
            return ProviderResponse(
                "Hybrid evidence is documented [C1] [C2].",
                model=generation_input.model,
            )

    class Recorder:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

    provider = Provider()
    recorder = Recorder()
    try:
        execute_projection(plan, configuration, ledger, writer)
        request = RetrievalRequest(
            query="Stadtwerke Solaranlage",
            manifest_path=str(binding.manifest_path),
            mode=RetrievalMode.HYBRID,
            top_k=10,
            projection_id=plan.projection_id,
            extraction_run_ids=plan.extraction_run_ids,
        )
        answer = CanonicalAnswerGenerator(
            retriever=CanonicalRetriever(
                embedder=FixtureEmbedder(),
                graph_backend=backend,
            ),
            providers={"fixture": provider},
            run_recorder=recorder,
        ).generate(
            AnswerRequest(
                question=request.query,
                retrieval_request=request,
                database_snapshot_id=plan.snapshot_id,
                generation_provider="fixture",
                generation_model="fixture-answer-model",
                generation_parameters=GenerationParameters(seed=42),
            )
        )
    finally:
        writer.close()
        ledger.close()

    assert answer.status == AnswerStatus.ANSWERED
    assert answer.retrieval_scope.mode == "hybrid"
    assert answer.retrieval_scope.projection_id == plan.projection_id
    assert answer.retrieval_scope.embedding_artifact_id == _embedding_artifact_id
    assert any(
        {trace.method for trace in item.retrieval_traces} == {"semantic", "graph"}
        for item in answer.retrieval_result.items
    )
    assert provider.inputs[0].allowed_citation_labels
    assert recorder.records[-1].retrieval_mode == "hybrid"
    assert recorder.records[-1].embedding_artifact_id == _embedding_artifact_id
