"""Fixture-only contract tests for the canonical Neo4j projection owner."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from crawler.core.database import (
    bind_live_database_state,
    create_sqlite_database,
    resolve_runtime_database,
)
from crawler.core.extraction import (
    ExtractionConfiguration,
    GraphArtifact,
    SelectedSource,
    finalize_extraction_run,
    initialize_extraction_run,
    persist_graph_artifact,
)
from crawler.core.neo4j_projection import (
    SCHEMA_CYPHER,
    Neo4jTarget,
    ProjectionBatch,
    ProjectionConfiguration,
    ProjectionError,
    ProjectionLedger,
    ProjectionLedgerError,
    ProjectionPlan,
    ProjectionScopeError,
    build_projection_plan,
    execute_projection,
)
from crawler.core.provenance import GraphProvenance, ensure_provenance_v4
from crawler.core.snapshots import SnapshotBinding, export_database_snapshot

SEGMENT_TEXT = "Die Stadtwerke bauen zwei Solaranlagen für die kommunale Energiewende."
CHUNK_TEXT = "Die Stadtwerke bauen eine Solaranlage im Rathaus."
ENTITIES = [
    {
        "name": "Stadtwerke Beispiel",
        "type": "Akteur",
        "category": "Strom",
        "status": "Existierend",
        "metrics": {},
    },
    {
        "name": "Solaranlage Rathaus",
        "type": "Infrastruktur",
        "category": "Strom",
        "status": "Geplant",
        "metrics": {"capacity": "20 kWp"},
    },
]
ASSERTIONS = [
    {
        "source_entity": "Stadtwerke Beispiel",
        "relation_type": "BAUT",
        "target_entity": "Solaranlage Rathaus",
        "evidence": "Die Stadtwerke bauen eine Solaranlage.",
    },
    {
        "source_entity": "Stadtwerke Beispiel",
        "relation_type": "PLANT",
        "target_entity": "Solaranlage Rathaus",
        "evidence": "Die Anlage ist geplant.",
    },
]


def _create_source_database(path: Path) -> None:
    with create_sqlite_database(path, purpose="fixture", wal=False) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE documents_raw (
              document_id TEXT PRIMARY KEY, municipality_id TEXT, url_canonical TEXT
            );
            CREATE TABLE municipality_documents (
              municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
              PRIMARY KEY(municipality_id, document_id)
            );
            CREATE TABLE segments (
              segment_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, order_index INTEGER,
              segment_type TEXT, text TEXT, segment_hash TEXT, page_ref TEXT,
              impact_score INTEGER, is_negative INTEGER, heading_context TEXT
            );
            CREATE TABLE chunks (
              chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, municipality_id TEXT,
              segment_id TEXT NOT NULL, chunk_index INTEGER, text TEXT,
              heading_context TEXT, impact_score INTEGER
            );
            CREATE TABLE graph_triplets (
              id INTEGER PRIMARY KEY AUTOINCREMENT, municipality_id TEXT NOT NULL,
              document_id TEXT NOT NULL, segment_rowid INTEGER NOT NULL,
              segment_hash TEXT NOT NULL, model_name TEXT NOT NULL, graph_json TEXT NOT NULL,
              entity_count INTEGER NOT NULL DEFAULT 0,
              relationship_count INTEGER NOT NULL DEFAULT 0,
              extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO documents_raw VALUES (
              'doc-1', 'muni-1', 'https://example.test/plan'
            );
            INSERT INTO municipality_documents VALUES ('muni-1', 'doc-1');
            INSERT INTO municipality_documents VALUES ('muni-2', 'doc-1');
            INSERT INTO segments VALUES (
              'seg-1', 'doc-1', 0, 'body',
              'Die Stadtwerke bauen zwei Solaranlagen für die kommunale Energiewende.',
              'unused', NULL, 30, 0, 'Energie'
            );
            INSERT INTO chunks VALUES (
              'chunk-1', 'doc-1', 'muni-1', 'seg-1', 0,
              'Die Stadtwerke bauen eine Solaranlage im Rathaus.', 'Energie', 30
            );
            """
        )
        ensure_provenance_v4(connection)


def _persist_run(
    path: Path,
    *,
    run_id: str,
    source_type: str,
    source_id: str,
    text: str,
    assertions: list[dict[str, Any]],
) -> None:
    graph = {"entities": ENTITIES, "relationships": assertions}
    database = bind_live_database_state(resolve_runtime_database(path))
    configuration = ExtractionConfiguration(
        provider="fixture",
        model="fixture-model",
        prompt_version="fixture-v1",
        parameters={"temperature": 0},
        seed=42,
        source_type=source_type,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        initialize_extraction_run(
            connection,
            configuration=configuration,
            database=database,
            selected_sources=[SelectedSource(source_type, source_id, text)],
            run_id=run_id,
        )
        legacy_rowid = connection.execute(
            "SELECT rowid FROM segments WHERE segment_id='seg-1'"
            if source_type == "segment"
            else "SELECT rowid FROM chunks WHERE chunk_id='chunk-1'"
        ).fetchone()[0]
        persist_graph_artifact(
            connection,
            provenance=GraphProvenance(
                source_type=source_type,
                source_id=source_id,
                segment_id="seg-1",
                chunk_id="chunk-1" if source_type == "chunk" else None,
                document_id="doc-1",
                municipality_id="muni-1",
                source_url="https://example.test/plan",
                source_text=text,
                extraction_model="fixture-model",
                prompt_version="fixture-v1",
                extraction_run_id=run_id,
            ),
            artifact=GraphArtifact(
                legacy_rowid=int(legacy_rowid),
                segment_hash=hashlib.sha256(text.encode()).hexdigest(),
                graph_json=json.dumps(graph, ensure_ascii=False),
                entity_count=len(ENTITIES),
                relationship_count=len(assertions),
            ),
        )
        finalize_extraction_run(connection, run_id)


def projection_fixture(tmp_path: Path) -> tuple[SnapshotBinding, tuple[str, ...]]:
    source = tmp_path / "source.sqlite"
    _create_source_database(source)
    _persist_run(
        source,
        run_id="segment-run-1",
        source_type="segment",
        source_id="seg-1",
        text=SEGMENT_TEXT,
        assertions=ASSERTIONS,
    )
    # Same immutable evidence, distinct extraction run and assertions.
    _persist_run(
        source,
        run_id="segment-run-2",
        source_type="segment",
        source_id="seg-1",
        text=SEGMENT_TEXT,
        assertions=ASSERTIONS[:1],
    )
    _persist_run(
        source,
        run_id="chunk-run-1",
        source_type="chunk",
        source_id="chunk-1",
        text=CHUNK_TEXT,
        assertions=ASSERTIONS[:1],
    )
    binding = export_database_snapshot(source, tmp_path / "snapshot.sqlite")
    return binding, ("segment-run-1", "segment-run-2", "chunk-run-1")


def _configuration(batch_size: int = 1) -> ProjectionConfiguration:
    return ProjectionConfiguration(
        target=Neo4jTarget("bolt://localhost:17687", "neo4j", "projection-writer"),
        batch_size=batch_size,
        query_timeout_seconds=5,
    )


class MemoryWriter:
    def __init__(self, fail_once_at: int | None = None, corrupt_counts: bool = False):
        self.fail_once_at = fail_once_at
        self.corrupt_counts = corrupt_counts
        self.failed = False
        self.schema_calls = 0
        self.batch_calls: Counter[int] = Counter()
        self.namespaces: dict[str, dict[str, set[str]]] = {}

    def ensure_schema(self) -> None:
        self.schema_calls += 1

    def database_identity(self):
        return {"server_database_id": "disposable-fixture", "configured_database": "neo4j"}

    def _namespace(self, projection_id: str) -> dict[str, set[str]]:
        return self.namespaces.setdefault(
            projection_id,
            {
                label: set()
                for label in (
                    "Projection",
                    "DatabaseSnapshot",
                    "ExtractionRun",
                    "Municipality",
                    "Document",
                    "Segment",
                    "Chunk",
                    "Evidence",
                    "GraphArtifact",
                    "Entity",
                    "EntityMention",
                    "Assertion",
                )
            },
        )

    def initialize_scope(self, plan: ProjectionPlan) -> None:
        namespace = self._namespace(plan.projection_id)
        namespace["Projection"].add(plan.projection_id)
        namespace["DatabaseSnapshot"].add(plan.snapshot_id)
        namespace["ExtractionRun"].update(plan.extraction_run_ids)

    def write_batch(self, plan: ProjectionPlan, batch: ProjectionBatch) -> None:
        self.batch_calls[batch.ordinal] += 1
        if self.fail_once_at == batch.ordinal and not self.failed:
            self.failed = True
            raise RuntimeError("disposable transaction failure")
        namespace = self._namespace(plan.projection_id)
        for artifact in batch.artifacts:
            namespace["Document"].add(artifact["document_key"])
            namespace["Segment"].add(artifact["segment_key"])
            if artifact["chunk_key"]:
                namespace["Chunk"].add(artifact["chunk_key"])
            namespace["Evidence"].add(artifact["evidence_key"])
            namespace["GraphArtifact"].add(artifact["artifact_key"])
            namespace["Municipality"].update(
                value["municipality_key"] for value in artifact["municipalities"]
            )
            for mention in artifact["mentions"]:
                namespace["Entity"].add(mention["entity_key"])
                namespace["EntityMention"].add(mention["mention_key"])
            for assertion in artifact["assertions"]:
                namespace["Entity"].update((assertion["subject_key"], assertion["object_key"]))
                namespace["Assertion"].add(assertion["assertion_key"])

    def namespace_counts(self, projection_id: str):
        counts = {label: len(values) for label, values in self._namespace(projection_id).items()}
        if self.corrupt_counts:
            counts["Assertion"] += 1
        return counts

    def close(self) -> None:
        pass


def test_plan_preserves_segment_chunk_municipalities_evidence_and_assertions(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    plan = build_projection_plan(binding, run_ids, _configuration())

    assert plan.source_artifact_counts == {
        "chunk-run-1": 1,
        "segment-run-1": 1,
        "segment-run-2": 1,
    }
    assert plan.expected_node_counts == {
        "Projection": 1,
        "DatabaseSnapshot": 1,
        "ExtractionRun": 3,
        "Municipality": 2,
        "Document": 1,
        "Segment": 1,
        "Chunk": 1,
        "Evidence": 2,
        "GraphArtifact": 3,
        "Entity": 2,
        "EntityMention": 6,
        "Assertion": 4,
    }
    segment = next(row for row in plan.artifacts if row["source_type"] == "segment")
    chunk = next(row for row in plan.artifacts if row["source_type"] == "chunk")
    assert segment["chunk_id"] is None
    assert chunk["chunk_id"] == "chunk-1"
    assert chunk["segment_id"] == "seg-1"
    assert {value["municipality_id"] for value in chunk["municipalities"]} == {
        "muni-1",
        "muni-2",
    }
    assert len({row["evidence_id"] for row in plan.artifacts}) == 2
    assert all("segment_rowid" not in row for row in plan.artifacts)


def test_clean_and_repeated_projection_are_idempotent(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    configuration = _configuration()
    plan = build_projection_plan(binding, run_ids, configuration)
    ledger = ProjectionLedger(tmp_path / "projection-ledger.sqlite")
    writer = MemoryWriter()
    try:
        first = execute_projection(plan, configuration, ledger, writer)
        first_counts = writer.namespace_counts(plan.projection_id)
        second = execute_projection(plan, configuration, ledger, writer)
    finally:
        ledger.close()

    assert first["status"] == second["status"] == "completed"
    assert first_counts == writer.namespace_counts(plan.projection_id)
    assert writer.batch_calls == Counter({0: 2, 1: 2, 2: 2})


def test_partial_failure_is_recorded_and_resume_skips_completed_batches(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    configuration = _configuration()
    plan = build_projection_plan(binding, run_ids, configuration)
    ledger = ProjectionLedger(tmp_path / "projection-ledger.sqlite")
    writer = MemoryWriter(fail_once_at=1)
    try:
        with pytest.raises(ProjectionError, match="batch 1 failed"):
            execute_projection(plan, configuration, ledger, writer)
        failed = ledger.record(plan.projection_id)
        assert failed["status"] == "failed"
        assert failed["projected_count"] == 1
        assert failed["failed_count"] == 1

        completed = execute_projection(plan, configuration, ledger, writer)
    finally:
        ledger.close()

    assert completed["status"] == "completed"
    assert completed["projected_count"] == 3
    assert completed["failed_count"] == 0
    assert writer.batch_calls == Counter({1: 2, 0: 1, 2: 1})


def test_changed_run_scope_coexists_without_stale_mixing(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    configuration = _configuration()
    all_runs = build_projection_plan(binding, run_ids, configuration)
    one_run = build_projection_plan(binding, (run_ids[0],), configuration)
    assert all_runs.projection_id != one_run.projection_id

    ledger = ProjectionLedger(tmp_path / "projection-ledger.sqlite")
    writer = MemoryWriter()
    try:
        execute_projection(all_runs, configuration, ledger, writer)
        execute_projection(one_run, configuration, ledger, writer)
    finally:
        ledger.close()
    assert set(writer.namespaces) == {all_runs.projection_id, one_run.projection_id}
    assert writer.namespace_counts(all_runs.projection_id)["GraphArtifact"] == 3
    assert writer.namespace_counts(one_run.projection_id)["GraphArtifact"] == 1


def test_changed_snapshot_creates_a_distinct_projection_namespace(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    second_binding = export_database_snapshot(
        Path(str(binding.manifest["source"]["origin_path"])),
        tmp_path / "second-snapshot.sqlite",
        created_at="2026-08-14T00:00:00+00:00",
    )
    configuration = _configuration()
    first = build_projection_plan(binding, run_ids, configuration)
    second = build_projection_plan(second_binding, run_ids, configuration)
    assert first.snapshot_database_id == second.snapshot_database_id
    assert first.snapshot_id != second.snapshot_id
    assert first.projection_id != second.projection_id


def test_namespace_verification_failure_never_records_completion(tmp_path):
    binding, run_ids = projection_fixture(tmp_path)
    configuration = _configuration()
    plan = build_projection_plan(binding, run_ids, configuration)
    ledger = ProjectionLedger(tmp_path / "projection-ledger.sqlite")
    writer = MemoryWriter(corrupt_counts=True)
    try:
        with pytest.raises(ProjectionError, match="namespace verification failed"):
            execute_projection(plan, configuration, ledger, writer)
        record = ledger.record(plan.projection_id)
    finally:
        ledger.close()
    assert record["status"] == "failed"
    assert record["completed_at"] is None


def test_projection_ledger_rejects_schema_drift(tmp_path):
    path = tmp_path / "drift.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE projection_ledger_schema (
              version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            INSERT INTO projection_ledger_schema VALUES (1, '2026-08-13T00:00:00+00:00');
            CREATE TABLE neo4j_projections (projection_id TEXT PRIMARY KEY);
            """
        )
    with pytest.raises(ProjectionLedgerError, match="schema drift"):
        ProjectionLedger(path)


def test_snapshot_mismatch_is_rejected_before_neo4j_write(tmp_path):
    source = tmp_path / "mismatch.sqlite"
    _create_source_database(source)
    _persist_run(
        source,
        run_id="mismatch-run",
        source_type="segment",
        source_id="seg-1",
        text=SEGMENT_TEXT,
        assertions=ASSERTIONS[:1],
    )
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE graph_extraction_runs SET database_snapshot_id='wrong-snapshot'")
    mismatch_binding = export_database_snapshot(source, tmp_path / "mismatch-snapshot.sqlite")
    with pytest.raises(ProjectionScopeError, match="different database snapshot"):
        build_projection_plan(mismatch_binding, ("mismatch-run",), _configuration())


def test_missing_successful_artifact_is_rejected_before_neo4j_write(tmp_path):
    source = tmp_path / "incomplete.sqlite"
    _create_source_database(source)
    _persist_run(
        source,
        run_id="incomplete-run",
        source_type="segment",
        source_id="seg-1",
        text=SEGMENT_TEXT,
        assertions=ASSERTIONS[:1],
    )
    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM graph_triplets WHERE extraction_run_id='incomplete-run'")
    binding = export_database_snapshot(source, tmp_path / "incomplete-snapshot.sqlite")
    with pytest.raises(ProjectionScopeError, match="successful outcomes but 0 verified"):
        build_projection_plan(binding, ("incomplete-run",), _configuration())


def test_constraints_cover_every_canonical_projection_label():
    constraints = "\n".join(SCHEMA_CYPHER)
    for label in (
        "Projection",
        "DatabaseSnapshot",
        "ExtractionRun",
        "Municipality",
        "Document",
        "Segment",
        "Chunk",
        "Evidence",
        "GraphArtifact",
        "Entity",
        "EntityMention",
        "Assertion",
    ):
        assert f"FOR (n:{label}) REQUIRE n.projection_key IS UNIQUE" in constraints


def test_neo4j_target_rejects_embedded_or_implicit_connection_identity():
    with pytest.raises(ProjectionScopeError, match="must not embed credentials"):
        Neo4jTarget("bolt://neo4j:password@localhost:7687", "neo4j", "writer").validate()
    with pytest.raises(ProjectionScopeError, match="query parameters or a fragment"):
        Neo4jTarget("bolt://localhost:7687?region=test", "neo4j", "writer").validate()
    with pytest.raises(ProjectionScopeError, match="database and username must be explicit"):
        Neo4jTarget("bolt://localhost:7687", "", "writer").validate()


def test_legacy_exporter_is_only_a_delegating_alias():
    from crawler.scripts import export_graph_to_neo4j

    with pytest.raises(RuntimeError, match="legacy Python exporter API was removed"):
        export_graph_to_neo4j.main()
