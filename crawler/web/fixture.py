"""Controlled, generated fixture runtime for the interactive research application."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from crawler.core.answer_generation import (
    CanonicalAnswerGenerator,
    GenerationInput,
    JsonlAnswerRunRecorder,
    ProviderResponse,
)
from crawler.core.database import (
    bind_live_database_state,
    connect_existing_read_write,
    create_sqlite_database,
    database_id_for_path,
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
    Neo4jTarget,
    ProjectionConfiguration,
    build_projection_plan,
)
from crawler.core.provenance import GraphProvenance, ensure_provenance_v4
from crawler.core.retrieval import (
    CanonicalRetriever,
    EmbeddingIdentity,
    chunk_corpus_rows,
    corpus_fingerprint,
    embedding_artifact_id,
    ensure_embedding_schema,
    vector_to_blob,
)
from crawler.core.snapshots import export_database_snapshot
from crawler.web.service import (
    ProviderAvailability,
    ProviderGate,
    ResearchApplicationService,
)

FIXTURE_QUESTION = "Welche Solarprojekte planen die Stadtwerke?"
FIXTURE_NOTICE = "Controlled fixture data — not real municipal research data."
FIXTURE_TEXTS = (
    "Die Stadtwerke Beispiel planen eine Solaranlage auf dem Rathaus mit 20 kWp.",
    "Die Gemeinde Beispielstadt prüft zusätzlich ein Wärmenetz im Ortskern.",
)
IDENTITY = EmbeddingIdentity(
    provider="fixture",
    model="fixture-embedding",
    model_revision="revision-1",
    vector_dimension=2,
    preprocessing_id="fixture-chunks-v1",
)


class FixtureEmbedder:
    identity = IDENTITY

    def embed_query(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0] if "solar" in text.lower() else [0.0, 1.0])


class DeterministicFixtureProvider:
    name = "fixture"

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        labels = " ".join(f"[{label}]" for label in generation_input.allowed_citation_labels[:2])
        return ProviderResponse(
            f"Die Stadtwerke planen eine Solaranlage auf dem Rathaus mit 20 kWp {labels}.",
            model=generation_input.model,
            input_tokens=64,
            output_tokens=18,
            total_tokens=82,
            finish_reason="stop",
        )


class FixtureGraphBackend:
    def __init__(self, plan) -> None:
        self.plan = plan

    def read(self, cypher, parameters, *, timeout_seconds):
        del parameters, timeout_seconds
        if "p:Projection" in cypher:
            return [
                {
                    "projection_id": self.plan.projection_id,
                    "snapshot_id": self.plan.snapshot_id,
                    "run_ids_json": json.dumps(self.plan.extraction_run_ids),
                    "schema_version": self.plan.projection_schema_version,
                }
            ]
        rows = []
        for artifact in self.plan.artifacts:
            for assertion in artifact["assertions"]:
                rows.append(
                    {
                        "assertion_id": assertion["assertion_id"],
                        "extraction_run_id": artifact["run_id"],
                        "relation_type": assertion["relation_type"],
                        "evidence_excerpt": assertion["evidence_excerpt"],
                        "graph_artifact_id": artifact["artifact_id"],
                        "evidence_id": artifact["evidence_id"],
                        "source_type": artifact["source_type"],
                        "source_id": artifact["source_id"],
                        "segment_id": artifact["segment_id"],
                        "chunk_id": artifact["chunk_id"],
                        "document_id": artifact["document_id"],
                        "source_url": artifact["source_url"],
                        "subject_id": assertion["subject_id"],
                        "subject_name": assertion["subject_name"],
                        "object_id": assertion["object_id"],
                        "object_name": assertion["object_name"],
                    }
                )
        return rows

    def close(self) -> None:
        pass


def _create_source(path: Path) -> None:
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
              'fixture-doc-solar', '09162000', 'https://example.test/fixture/solar'
            );
            INSERT INTO documents_raw VALUES (
              'fixture-doc-heat', '09163000', 'https://example.test/fixture/heat'
            );
            INSERT INTO municipality_documents VALUES ('09162000', 'fixture-doc-solar');
            INSERT INTO municipality_documents VALUES ('09163000', 'fixture-doc-solar');
            INSERT INTO municipality_documents VALUES ('09163000', 'fixture-doc-heat');
            INSERT INTO segments VALUES (
              'fixture-segment-solar', 'fixture-doc-solar', 0, 'body',
              'Die Stadtwerke Beispiel planen eine Solaranlage auf dem Rathaus mit 20 kWp.',
              'fixture-hash-solar', NULL, 30, 0, 'Kommunale Energieprojekte'
            );
            INSERT INTO segments VALUES (
              'fixture-segment-heat', 'fixture-doc-heat', 0, 'body',
              'Die Gemeinde Beispielstadt prüft zusätzlich ein Wärmenetz im Ortskern.',
              'fixture-hash-heat', NULL, 20, 0, 'Wärmeplanung'
            );
            INSERT INTO chunks VALUES (
              'fixture-chunk-solar', 'fixture-doc-solar', '09162000',
              'fixture-segment-solar', 0,
              'Die Stadtwerke Beispiel planen eine Solaranlage auf dem Rathaus mit 20 kWp.',
              'Kommunale Energieprojekte', 30
            );
            INSERT INTO chunks VALUES (
              'fixture-chunk-heat', 'fixture-doc-heat', '09163000',
              'fixture-segment-heat', 0,
              'Die Gemeinde Beispielstadt prüft zusätzlich ein Wärmenetz im Ortskern.',
              'Wärmeplanung', 20
            );
            """
        )
        ensure_provenance_v4(connection)


def _persist_graph_run(path: Path) -> None:
    source_text = FIXTURE_TEXTS[0]
    entities = [
        {"name": "Stadtwerke Beispiel", "type": "Akteur", "category": "Strom"},
        {"name": "Solaranlage Rathaus", "type": "Infrastruktur", "category": "Strom"},
    ]
    relationships = [
        {
            "source_entity": "Stadtwerke Beispiel",
            "relation_type": "PLANT",
            "target_entity": "Solaranlage Rathaus",
            "evidence": "Die Stadtwerke planen eine Solaranlage auf dem Rathaus.",
        }
    ]
    database = bind_live_database_state(resolve_runtime_database(path))
    with connect_existing_read_write(path) as connection:
        for source_type, source_id, run_id, table in (
            ("chunk", "fixture-chunk-solar", "fixture-chunk-run", "chunks"),
            ("segment", "fixture-segment-solar", "fixture-segment-run", "segments"),
        ):
            configuration = ExtractionConfiguration(
                provider="fixture",
                model="fixture-extraction-model",
                prompt_version="fixture-extraction-v1",
                parameters={"temperature": 0},
                seed=42,
                source_type=source_type,
            )
            initialize_extraction_run(
                connection,
                configuration=configuration,
                database=database,
                selected_sources=[SelectedSource(source_type, source_id, source_text)],
                run_id=run_id,
            )
            rowid = connection.execute(
                f"SELECT rowid FROM {table} WHERE {source_type}_id=?", (source_id,)
            ).fetchone()[0]
            persist_graph_artifact(
                connection,
                provenance=GraphProvenance(
                    source_type=source_type,
                    source_id=source_id,
                    segment_id="fixture-segment-solar",
                    chunk_id="fixture-chunk-solar" if source_type == "chunk" else None,
                    document_id="fixture-doc-solar",
                    municipality_id="09162000",
                    source_url="https://example.test/fixture/solar",
                    source_text=source_text,
                    extraction_model="fixture-extraction-model",
                    prompt_version="fixture-extraction-v1",
                    extraction_run_id=run_id,
                ),
                artifact=GraphArtifact(
                    legacy_rowid=int(rowid),
                    segment_hash=hashlib.sha256(source_text.encode()).hexdigest(),
                    graph_json=json.dumps(
                        {"entities": entities, "relationships": relationships},
                        ensure_ascii=False,
                    ),
                    entity_count=2,
                    relationship_count=1,
                ),
            )
            finalize_extraction_run(connection, run_id)


def _install_embeddings(path: Path) -> None:
    with connect_existing_read_write(path) as connection:
        ensure_embedding_schema(connection)
        corpus = chunk_corpus_rows(connection)
        artifact_id = embedding_artifact_id(
            IDENTITY,
            source_database_id=database_id_for_path(path),
            corpus_identity=corpus_fingerprint(corpus),
        )
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO embedding_artifacts VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                IDENTITY.provider,
                IDENTITY.model,
                IDENTITY.model_revision,
                IDENTITY.vector_dimension,
                IDENTITY.preprocessing_id,
                database_id_for_path(path),
                corpus_fingerprint(corpus),
                len(corpus),
                len(corpus),
                "completed",
                now,
                now,
            ),
        )
        for ordinal, (chunk_id, content_hash, _text) in enumerate(corpus):
            vector = np.array([1.0, 0.0] if ordinal == 1 else [0.0, 1.0])
            if chunk_id == "fixture-chunk-solar":
                vector = np.array([1.0, 0.0])
            connection.execute(
                "INSERT INTO chunk_embeddings VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, chunk_id, content_hash, 2, vector_to_blob(vector), now),
            )


def create_fixture_service(runtime_dir: Path) -> ResearchApplicationService:
    """Generate an isolated canonical fixture snapshot and application service."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source = runtime_dir / "fixture-source.sqlite"
    snapshot = runtime_dir / "fixture-snapshot.sqlite"
    for path in (source, snapshot, snapshot.with_suffix(".sqlite.manifest.json")):
        if path.exists():
            path.unlink()
    _create_source(source)
    _persist_graph_run(source)
    _install_embeddings(source)
    binding = export_database_snapshot(source, snapshot)
    plan = build_projection_plan(
        binding,
        ("fixture-chunk-run", "fixture-segment-run"),
        ProjectionConfiguration(Neo4jTarget("bolt://127.0.0.1:17687", "neo4j", "fixture-readonly")),
    )
    retriever = CanonicalRetriever(
        embedder=FixtureEmbedder(),
        graph_backend=FixtureGraphBackend(plan),
    )
    provider = DeterministicFixtureProvider()
    generator = CanonicalAnswerGenerator(
        retriever=retriever,
        providers={provider.name: provider},
        run_recorder=JsonlAnswerRunRecorder(runtime_dir / "answer-runs.jsonl"),
    )
    return ResearchApplicationService(
        retriever=retriever,
        generator=generator,
        manifest_path=str(binding.manifest_path),
        database_snapshot_id=str(binding.database.snapshot_id),
        projection_id=plan.projection_id,
        extraction_run_ids=plan.extraction_run_ids,
        provider_gate=ProviderGate(
            availability=ProviderAvailability.ANSWER_GENERATION_READY,
            acceptance_gate="fixture_only_live_provider_open",
            provider="fixture",
            model="fixture-answer-model",
            message=(
                "Fixture generation is ready. Live Ollama acceptance remains OPEN after timeout."
            ),
        ),
        demo_mode=True,
        demo_notice=FIXTURE_NOTICE,
    )
