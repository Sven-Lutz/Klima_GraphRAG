"""Canonical, snapshot-bound retrieval over SQLite evidence and Neo4j projections.

SQLite is authoritative for source text and provenance. Neo4j is an optional,
bounded navigation index whose identities are resolved back to the manifested
SQLite snapshot before a result is returned.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlsplit

import numpy as np

from crawler.core.database import connect_existing_read_only
from crawler.core.neo4j_projection import ProjectionScopeError, graph_artifact_id_for
from crawler.core.provenance import evidence_id_for
from crawler.core.snapshots import SnapshotBinding, validate_snapshot_manifest

EMBEDDING_SCHEMA_VERSION = 1
DEFAULT_GRAPH_TIMEOUT_SECONDS = 15.0
MAX_TOP_K = 100
MAX_GRAPH_CANDIDATES = 500
RRF_CONSTANT = 60


class RetrievalError(RuntimeError):
    """Base error for a retrieval request that cannot be trusted."""


class EmbeddingLineageError(RetrievalError):
    """Raised when vectors are legacy, incomplete, or lineage-incompatible."""


class ProjectionMismatchError(RetrievalError):
    """Raised when Neo4j does not exactly match the requested snapshot scope."""


class RetrievalMode(str, Enum):
    SEMANTIC = "semantic"
    GRAPH = "graph"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider: str
    model: str
    model_revision: str
    vector_dimension: int
    preprocessing_id: str

    def validate(self) -> None:
        values = (self.provider, self.model, self.model_revision, self.preprocessing_id)
        if any(not value.strip() for value in values) or self.vector_dimension < 1:
            raise EmbeddingLineageError("Embedding identity fields must be explicit and non-empty")


class QueryEmbedder(Protocol):
    identity: EmbeddingIdentity

    def embed_query(self, text: str) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class DocumentSource:
    document_id: str
    source_url: str
    heading_context: str | None
    municipality_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphAssertion:
    assertion_id: str
    graph_artifact_id: str
    extraction_run_id: str
    subject_id: str
    subject_name: str
    relation_type: str
    object_id: str
    object_name: str
    evidence_excerpt: str | None


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    method: str
    source_rank: int
    source_score: float
    fusion_score: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    database_snapshot_id: str
    evidence_id: str
    source_type: str
    source_id: str
    segment_id: str
    chunk_id: str | None
    document: DocumentSource
    evidence_text: str
    extraction_run_ids: tuple[str, ...]
    graph_artifact_ids: tuple[str, ...]
    assertions: tuple[GraphAssertion, ...]
    retrieval_traces: tuple[RetrievalTrace, ...]
    retrieval_score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    manifest_path: str
    mode: RetrievalMode = RetrievalMode.SEMANTIC
    top_k: int = 8
    projection_id: str | None = None
    extraction_run_ids: tuple[str, ...] = ()
    graph_hops: int = 1

    def validate(self) -> None:
        if not isinstance(self.mode, RetrievalMode):
            raise RetrievalError("mode must be a RetrievalMode value")
        if not self.query.strip():
            raise RetrievalError("Retrieval query must not be empty")
        if not 1 <= self.top_k <= MAX_TOP_K:
            raise RetrievalError(f"top_k must be between 1 and {MAX_TOP_K}")
        if self.graph_hops != 1:
            raise RetrievalError("Canonical graph retrieval currently supports exactly one hop")
        if self.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID} and not self.projection_id:
            raise RetrievalError("Graph and hybrid retrieval require an explicit projection_id")


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    mode: str
    database_snapshot_id: str
    projection_id: str | None
    items: tuple[RetrievedEvidence, ...]
    embedding_artifact_id: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GraphReadBackend(Protocol):
    def read(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> list[Mapping[str, Any]]: ...

    def close(self) -> None: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_to_blob(vector: np.ndarray) -> bytes:
    values = np.asarray(vector, dtype="<f4")
    return values.tobytes(order="C")


def blob_to_vector(blob: bytes, expected_dimension: int) -> np.ndarray:
    if len(blob) != expected_dimension * 4:
        raise EmbeddingLineageError(
            f"Embedding blob has {len(blob) // 4} dimensions; expected {expected_dimension}"
        )
    return np.frombuffer(blob, dtype="<f4").astype(np.float32, copy=True)


def ensure_embedding_schema(connection: sqlite3.Connection) -> None:
    """Create the versioned vector-artifact contract on a writable live database."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS embedding_artifacts (
          artifact_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          model_revision TEXT NOT NULL,
          vector_dimension INTEGER NOT NULL CHECK(vector_dimension > 0),
          preprocessing_id TEXT NOT NULL,
          source_database_id TEXT NOT NULL,
          corpus_fingerprint TEXT NOT NULL,
          planned_count INTEGER NOT NULL,
          embedded_count INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
          created_at TEXT NOT NULL,
          completed_at TEXT,
          UNIQUE(provider, model, model_revision, vector_dimension, preprocessing_id,
                 source_database_id, corpus_fingerprint)
        );
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
          artifact_id TEXT NOT NULL,
          chunk_id TEXT NOT NULL,
          source_content_hash TEXT NOT NULL,
          vector_dimension INTEGER NOT NULL CHECK(vector_dimension > 0),
          embedding BLOB NOT NULL,
          embedded_at TEXT NOT NULL,
          PRIMARY KEY(artifact_id, chunk_id),
          FOREIGN KEY(artifact_id) REFERENCES embedding_artifacts(artifact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_chunk
          ON chunk_embeddings(chunk_id, artifact_id);
        """
    )


def chunk_corpus_rows(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return stable chunk ID, content hash, and text without consulting rowids."""
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(chunks)")}
    if not {"chunk_id", "text"} <= columns:
        raise EmbeddingLineageError("chunks lacks the canonical chunk_id/text contract")
    hash_expression = "chunk_hash" if "chunk_hash" in columns else "NULL"
    rows = connection.execute(
        f"SELECT chunk_id, {hash_expression}, text FROM chunks ORDER BY chunk_id"
    ).fetchall()
    result = []
    for chunk_id, stored_hash, text in rows:
        content_hash = _sha256(str(text))
        if stored_hash and str(stored_hash) != content_hash:
            raise EmbeddingLineageError(
                f"Stored content hash disagrees with chunk text for {chunk_id}"
            )
        result.append((str(chunk_id), content_hash, str(text)))
    return result


def corpus_fingerprint(rows: Sequence[tuple[str, str, str]]) -> str:
    return "chunk-corpus-sha256:" + _sha256(
        _canonical_json([[chunk_id, content_hash] for chunk_id, content_hash, _ in rows])
    )


def embedding_artifact_id(
    identity: EmbeddingIdentity,
    *,
    source_database_id: str,
    corpus_identity: str,
) -> str:
    identity.validate()
    return "embedding-artifact-sha256:" + _sha256(
        _canonical_json(
            {
                **asdict(identity),
                "schema_version": EMBEDDING_SCHEMA_VERSION,
                "source_database_id": source_database_id,
                "corpus_fingerprint": corpus_identity,
            }
        )
    )


def _manifest_source_database_id(binding: SnapshotBinding) -> str:
    source = binding.manifest.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("database_id"), str):
        raise RetrievalError("Snapshot manifest lacks its source database identity")
    return str(source["database_id"])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _select_embedding_artifact(
    connection: sqlite3.Connection,
    binding: SnapshotBinding,
    identity: EmbeddingIdentity,
) -> tuple[str, list[tuple[str, str, str]]]:
    identity.validate()
    if not _table_exists(connection, "embedding_artifacts") or not _table_exists(
        connection, "chunk_embeddings"
    ):
        raise EmbeddingLineageError(
            "Only legacy/unverifiable chunk embeddings are present; regenerate canonical vectors"
        )
    rows = chunk_corpus_rows(connection)
    fingerprint = corpus_fingerprint(rows)
    source_database_id = _manifest_source_database_id(binding)
    matches = connection.execute(
        """
        SELECT artifact_id, planned_count, embedded_count, status
        FROM embedding_artifacts
        WHERE schema_version=? AND provider=? AND model=? AND model_revision=?
          AND vector_dimension=? AND preprocessing_id=? AND source_database_id=?
          AND corpus_fingerprint=?
        ORDER BY artifact_id
        """,
        (
            EMBEDDING_SCHEMA_VERSION,
            identity.provider,
            identity.model,
            identity.model_revision,
            identity.vector_dimension,
            identity.preprocessing_id,
            source_database_id,
            fingerprint,
        ),
    ).fetchall()
    if len(matches) != 1:
        raise EmbeddingLineageError(
            "No unique completed embedding artifact matches this model, corpus, and snapshot source"
        )
    artifact_id, planned, embedded, status = matches[0]
    artifact_rows = connection.execute(
        """
        SELECT chunk_id, source_content_hash, vector_dimension, length(embedding)
        FROM chunk_embeddings WHERE artifact_id=? ORDER BY chunk_id
        """,
        (artifact_id,),
    ).fetchall()
    actual = len(artifact_rows)
    expected = len(rows)
    complete = (
        status == "completed"
        and int(planned) == expected
        and int(embedded) == expected
        and actual == expected
    )
    if not complete:
        raise EmbeddingLineageError(
            f"Embedding corpus is partial: expected {expected}, artifact reports "
            f"{embedded}, and stores {actual}"
        )
    expected_rows = [(chunk_id, content_hash) for chunk_id, content_hash, _ in rows]
    actual_rows = [(str(row[0]), str(row[1])) for row in artifact_rows]
    if actual_rows != expected_rows:
        raise EmbeddingLineageError(
            "Embedding corpus membership or source-content identity does not exactly match "
            "the canonical chunk corpus"
        )
    for chunk_id, _content_hash, dimension, byte_length in artifact_rows:
        if int(dimension) != identity.vector_dimension:
            raise EmbeddingLineageError("Embedding artifact mixes vector dimensions")
        if int(byte_length) != identity.vector_dimension * 4:
            raise EmbeddingLineageError(
                f"Embedding blob for chunk {chunk_id} disagrees with its vector dimension"
            )
    return str(artifact_id), rows


def _municipalities(connection: sqlite3.Connection, document_id: str) -> tuple[str, ...]:
    values: set[str] = set()
    if _table_exists(connection, "municipality_documents"):
        values.update(
            str(row[0])
            for row in connection.execute(
                "SELECT municipality_id FROM municipality_documents "
                "WHERE document_id=? ORDER BY municipality_id",
                (document_id,),
            )
        )
    row = connection.execute(
        "SELECT municipality_id FROM documents_raw WHERE document_id=?", (document_id,)
    ).fetchone()
    if row and row[0] is not None:
        values.add(str(row[0]))
    return tuple(sorted(values))


def _validated_source_url(value: Any, document_id: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RetrievalError(f"Document {document_id} lacks a canonical HTTP(S) source URL")
    return url


def _required_municipalities(connection: sqlite3.Connection, document_id: str) -> tuple[str, ...]:
    municipalities = _municipalities(connection, document_id)
    if not municipalities:
        raise RetrievalError(f"Document {document_id} lacks municipality provenance")
    return municipalities


def _semantic_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    embedder: QueryEmbedder,
) -> tuple[list[RetrievedEvidence], str]:
    with connect_existing_read_only(binding.database) as connection:
        artifact_id, corpus = _select_embedding_artifact(connection, binding, embedder.identity)
        query_vector = np.asarray(embedder.embed_query(request.query), dtype=np.float32)
        if query_vector.ndim != 1 or len(query_vector) != embedder.identity.vector_dimension:
            raise EmbeddingLineageError("Query embedding dimension disagrees with its identity")
        norm = float(np.linalg.norm(query_vector))
        if not math.isfinite(norm) or norm == 0:
            raise EmbeddingLineageError("Query embedding must contain a finite non-zero vector")
        query_vector = query_vector / norm

        content_hashes = {chunk_id: content_hash for chunk_id, content_hash, _ in corpus}
        rows = connection.execute(
            """
            SELECT c.chunk_id, c.segment_id, c.document_id, c.text, c.heading_context,
                   d.url_canonical, ce.source_content_hash, ce.vector_dimension, ce.embedding
            FROM chunk_embeddings ce
            JOIN chunks c ON c.chunk_id=ce.chunk_id
            JOIN documents_raw d ON d.document_id=c.document_id
            WHERE ce.artifact_id=?
            ORDER BY c.chunk_id
            """,
            (artifact_id,),
        ).fetchall()
        scored: list[tuple[float, tuple[Any, ...]]] = []
        for row in rows:
            chunk_id = str(row[0])
            if str(row[6]) != content_hashes.get(chunk_id):
                raise EmbeddingLineageError(f"Embedding content hash mismatch for chunk {chunk_id}")
            if int(row[7]) != embedder.identity.vector_dimension:
                raise EmbeddingLineageError("Embedding artifact mixes vector dimensions")
            vector = blob_to_vector(row[8], embedder.identity.vector_dimension)
            vector_norm = float(np.linalg.norm(vector))
            if not math.isfinite(vector_norm) or vector_norm == 0:
                raise EmbeddingLineageError(f"Chunk {chunk_id} has an invalid embedding")
            score = float((vector / vector_norm) @ query_vector)
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1][0])))

        result: list[RetrievedEvidence] = []
        for rank, (score, row) in enumerate(scored[: request.top_k], start=1):
            chunk_id, segment_id, document_id, text, heading, url = row[:6]
            if segment_id is None:
                raise RetrievalError(f"Chunk {chunk_id} lacks its required parent segment")
            content_hash = content_hashes[str(chunk_id)]
            result.append(
                RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id_for("chunk", str(chunk_id), content_hash),
                    source_type="chunk",
                    source_id=str(chunk_id),
                    segment_id=str(segment_id),
                    chunk_id=str(chunk_id),
                    document=DocumentSource(
                        document_id=str(document_id),
                        source_url=_validated_source_url(url, str(document_id)),
                        heading_context=str(heading) if heading is not None else None,
                        municipality_ids=_required_municipalities(connection, str(document_id)),
                    ),
                    evidence_text=str(text),
                    extraction_run_ids=(),
                    graph_artifact_ids=(),
                    assertions=(),
                    retrieval_traces=(RetrievalTrace("semantic", rank, score),),
                    retrieval_score=score,
                    rank=rank,
                )
            )
    return result, artifact_id


_GRAPH_SCOPE_QUERY = """
MATCH (p:Projection {projection_id: $projection_id})
RETURN p.projection_id AS projection_id, p.snapshot_id AS snapshot_id,
       p.run_ids_json AS run_ids_json, p.schema_version AS schema_version
LIMIT 2
"""

_GRAPH_RETRIEVAL_QUERY = """
MATCH (projection:Projection {projection_id: $projection_id})-[:INCLUDES_RUN]->
      (run:ExtractionRun {projection_id: $projection_id})-[:PRODUCED]->
      (artifact:GraphArtifact {projection_id: $projection_id})
MATCH (artifact)-[:HAS_ASSERTION]->(assertion:Assertion {projection_id: $projection_id})
MATCH (artifact)-[:USES_EVIDENCE]->(evidence:Evidence {projection_id: $projection_id})
MATCH (assertion)-[:SUPPORTED_BY]->(evidence)
MATCH (assertion)-[:SUBJECT|OBJECT]->(entity:Entity {projection_id: $projection_id})
WHERE any(term IN $terms WHERE toLower(entity.name) CONTAINS term)
  AND assertion.extraction_run_id = run.run_id
  AND artifact.extraction_run_id = run.run_id
MATCH (assertion)-[:SUBJECT]->(subject:Entity {projection_id: $projection_id})
MATCH (assertion)-[:OBJECT]->(object:Entity {projection_id: $projection_id})
RETURN DISTINCT assertion.assertion_id AS assertion_id,
       run.run_id AS extraction_run_id,
       assertion.relation_type AS relation_type,
       assertion.evidence_excerpt AS evidence_excerpt,
       artifact.artifact_id AS graph_artifact_id,
       evidence.evidence_id AS evidence_id,
       evidence.source_type AS source_type, evidence.source_id AS source_id,
       evidence.segment_id AS segment_id, evidence.chunk_id AS chunk_id,
       evidence.document_id AS document_id, evidence.source_url AS source_url,
       subject.entity_id AS subject_id, subject.name AS subject_name,
       object.entity_id AS object_id, object.name AS object_name
ORDER BY assertion.assertion_id, evidence.evidence_id
LIMIT $candidate_limit
"""


def _query_terms(query: str) -> list[str]:
    return sorted({term.lower() for term in re.findall(r"[\wäöüß-]+", query) if len(term) >= 3})


def _validate_graph_scope(
    backend: GraphReadBackend,
    binding: SnapshotBinding,
    request: RetrievalRequest,
    timeout_seconds: float,
) -> tuple[str, ...]:
    rows = backend.read(
        _GRAPH_SCOPE_QUERY,
        {"projection_id": request.projection_id},
        timeout_seconds=timeout_seconds,
    )
    if len(rows) != 1:
        raise ProjectionMismatchError("Selected Neo4j projection is missing or ambiguous")
    row = rows[0]
    if row.get("projection_id") != request.projection_id:
        raise ProjectionMismatchError("Neo4j returned a different projection identity")
    if row.get("snapshot_id") != binding.database.snapshot_id:
        raise ProjectionMismatchError("Neo4j projection belongs to a different snapshot")
    try:
        run_ids = tuple(sorted(str(value) for value in json.loads(str(row["run_ids_json"]))))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProjectionMismatchError("Neo4j projection run scope is malformed") from exc
    if not run_ids:
        raise ProjectionMismatchError("Neo4j projection has an empty extraction-run scope")
    if row.get("schema_version") != 1:
        raise ProjectionMismatchError("Neo4j projection schema version is unsupported")
    expected = tuple(sorted(request.extraction_run_ids))
    if expected and run_ids != expected:
        raise ProjectionMismatchError("Neo4j projection extraction-run scope does not match")
    return run_ids


def _authoritative_evidence(
    connection: sqlite3.Connection,
    evidence_id: str,
    selected_runs: tuple[str, ...],
) -> tuple[Any, ...]:
    placeholders = ",".join("?" for _ in selected_runs)
    rows = connection.execute(
        f"""
        SELECT e.source_type, e.source_id, e.segment_id, e.chunk_id, e.document_id,
               e.source_url, e.evidence_text, g.extraction_run_id
        FROM evidence_snapshots e
        JOIN graph_triplets g ON g.evidence_id=e.evidence_id
        WHERE e.evidence_id=? AND g.provenance_status='verified'
          AND g.extraction_run_id IN ({placeholders})
        ORDER BY g.extraction_run_id
        """,
        (evidence_id, *selected_runs),
    ).fetchall()
    if not rows:
        raise ProjectionMismatchError(
            f"Neo4j evidence {evidence_id} cannot be resolved to verified SQLite evidence"
        )
    canonical = rows[0][:7]
    if any(row[:7] != canonical for row in rows[1:]):
        raise ProjectionMismatchError(f"SQLite evidence {evidence_id} has conflicting identities")
    return (*canonical, tuple(str(row[7]) for row in rows))


def _evidence_municipalities(connection: sqlite3.Connection, evidence_id: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT municipality_id FROM evidence_municipalities "
        "WHERE evidence_id=? ORDER BY municipality_id",
        (evidence_id,),
    ).fetchall()
    municipalities = tuple(str(row[0]) for row in rows)
    if not municipalities:
        raise ProjectionMismatchError(
            f"SQLite evidence {evidence_id} lacks immutable municipality provenance"
        )
    return municipalities


def _source_heading(
    connection: sqlite3.Connection, source_type: str, source_id: str, segment_id: str
) -> str | None:
    table, column, value = (
        ("chunks", "chunk_id", source_id)
        if source_type == "chunk"
        else ("segments", "segment_id", segment_id)
    )
    row = connection.execute(
        f"SELECT heading_context FROM {table} WHERE {column}=?", (value,)
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _graph_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    backend: GraphReadBackend,
    timeout_seconds: float,
) -> list[RetrievedEvidence]:
    selected_runs = _validate_graph_scope(backend, binding, request, timeout_seconds)
    terms = _query_terms(request.query)
    if not terms:
        return []
    candidate_limit = min(MAX_GRAPH_CANDIDATES, max(request.top_k * 20, request.top_k))
    rows = backend.read(
        _GRAPH_RETRIEVAL_QUERY,
        {
            "projection_id": request.projection_id,
            "terms": terms,
            "candidate_limit": candidate_limit,
        },
        timeout_seconds=timeout_seconds,
    )
    if len(rows) > candidate_limit:
        raise RetrievalError("Neo4j backend exceeded the application-owned result cap")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id:
            raise ProjectionMismatchError("Neo4j graph result omitted evidence identity")
        grouped[evidence_id].append(row)

    result: list[RetrievedEvidence] = []
    with connect_existing_read_only(binding.database) as connection:
        for evidence_id in sorted(grouped):
            source_type, source_id, segment_id, chunk_id, document_id, url, text, runs = (
                _authoritative_evidence(connection, evidence_id, selected_runs)
            )
            unique_rows: dict[str, Mapping[str, Any]] = {}
            for row in grouped[evidence_id]:
                assertion_id = str(row.get("assertion_id") or "")
                if not assertion_id:
                    raise ProjectionMismatchError("Neo4j assertion omitted its stable identity")
                if assertion_id in unique_rows and row != unique_rows[assertion_id]:
                    raise ProjectionMismatchError(
                        f"Neo4j assertion {assertion_id} has conflicting result records"
                    )
                unique_rows[assertion_id] = row
            graph_rows = [unique_rows[key] for key in sorted(unique_rows)]
            expected_identity = {
                "source_type": str(source_type),
                "source_id": str(source_id),
                "segment_id": str(segment_id),
                "chunk_id": str(chunk_id) if chunk_id is not None else None,
                "document_id": str(document_id),
                "source_url": str(url),
            }
            for row in graph_rows:
                actual_identity = {key: row.get(key) for key in expected_identity}
                if actual_identity != expected_identity:
                    raise ProjectionMismatchError(
                        f"Neo4j evidence {evidence_id} disagrees with authoritative SQLite"
                    )
                if str(row.get("extraction_run_id")) not in selected_runs:
                    raise ProjectionMismatchError("Neo4j assertion falls outside projection scope")
                if str(row.get("extraction_run_id")) not in runs:
                    raise ProjectionMismatchError(
                        "Neo4j assertion run has no verified SQLite graph artifact"
                    )
                try:
                    expected_artifact_id = graph_artifact_id_for(
                        str(row.get("extraction_run_id")), str(source_type), str(source_id)
                    )
                except ProjectionScopeError as exc:
                    raise ProjectionMismatchError(
                        f"SQLite evidence {evidence_id} has an invalid graph artifact scope"
                    ) from exc
                if row.get("graph_artifact_id") != expected_artifact_id:
                    raise ProjectionMismatchError(
                        "Neo4j graph artifact identity disagrees with authoritative SQLite scope"
                    )
                required_graph_fields = (
                    "graph_artifact_id",
                    "subject_id",
                    "subject_name",
                    "relation_type",
                    "object_id",
                    "object_name",
                )
                if any(not str(row.get(field) or "").strip() for field in required_graph_fields):
                    raise ProjectionMismatchError(
                        f"Neo4j assertion {row['assertion_id']} has incomplete identities"
                    )
            assertions = tuple(
                GraphAssertion(
                    assertion_id=str(row["assertion_id"]),
                    graph_artifact_id=str(row["graph_artifact_id"]),
                    extraction_run_id=str(row["extraction_run_id"]),
                    subject_id=str(row["subject_id"]),
                    subject_name=str(row["subject_name"]),
                    relation_type=str(row["relation_type"]),
                    object_id=str(row["object_id"]),
                    object_name=str(row["object_name"]),
                    evidence_excerpt=(
                        str(row["evidence_excerpt"])
                        if row.get("evidence_excerpt") is not None
                        else None
                    ),
                )
                for row in graph_rows
            )
            artifact_ids = tuple(sorted({item.graph_artifact_id for item in assertions}))
            result.append(
                RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id,
                    source_type=str(source_type),
                    source_id=str(source_id),
                    segment_id=str(segment_id),
                    chunk_id=str(chunk_id) if chunk_id is not None else None,
                    document=DocumentSource(
                        document_id=str(document_id),
                        source_url=_validated_source_url(url, str(document_id)),
                        heading_context=_source_heading(
                            connection, str(source_type), str(source_id), str(segment_id)
                        ),
                        municipality_ids=_evidence_municipalities(connection, evidence_id),
                    ),
                    evidence_text=str(text),
                    extraction_run_ids=tuple(
                        sorted({item.extraction_run_id for item in assertions})
                    ),
                    graph_artifact_ids=artifact_ids,
                    assertions=assertions,
                    retrieval_traces=(),
                    retrieval_score=0.0,
                    rank=0,
                )
            )
    result.sort(
        key=lambda item: (
            -len(item.assertions),
            item.evidence_id,
        )
    )
    return [
        replace(
            item,
            rank=rank,
            retrieval_score=1.0 / rank,
            retrieval_traces=(RetrievalTrace("graph", rank, 1.0 / rank),),
        )
        for rank, item in enumerate(result[: request.top_k], start=1)
    ]


def _merge_evidence(primary: RetrievedEvidence, secondary: RetrievedEvidence) -> RetrievedEvidence:
    if (
        primary.database_snapshot_id,
        primary.source_type,
        primary.source_id,
        primary.segment_id,
        primary.chunk_id,
        primary.document.document_id,
        primary.document.source_url,
        primary.document.heading_context,
        primary.document.municipality_ids,
        primary.evidence_text,
    ) != (
        secondary.database_snapshot_id,
        secondary.source_type,
        secondary.source_id,
        secondary.segment_id,
        secondary.chunk_id,
        secondary.document.document_id,
        secondary.document.source_url,
        secondary.document.heading_context,
        secondary.document.municipality_ids,
        secondary.evidence_text,
    ):
        raise RetrievalError(f"Evidence identity collision for {primary.evidence_id}")
    assertions = {item.assertion_id: item for item in (*primary.assertions, *secondary.assertions)}
    return replace(
        primary,
        extraction_run_ids=tuple(
            sorted(set(primary.extraction_run_ids) | set(secondary.extraction_run_ids))
        ),
        graph_artifact_ids=tuple(
            sorted(set(primary.graph_artifact_ids) | set(secondary.graph_artifact_ids))
        ),
        assertions=tuple(assertions[key] for key in sorted(assertions)),
        retrieval_traces=primary.retrieval_traces + secondary.retrieval_traces,
    )


def reciprocal_rank_fusion(
    semantic: Sequence[RetrievedEvidence],
    graph: Sequence[RetrievedEvidence],
    *,
    top_k: int,
) -> list[RetrievedEvidence]:
    """Fuse incompatible score spaces deterministically using RRF and stable IDs."""
    if not 1 <= top_k <= MAX_TOP_K:
        raise RetrievalError(f"top_k must be between 1 and {MAX_TOP_K}")
    combined: dict[str, RetrievedEvidence] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for channel, source in (("semantic", semantic), ("graph", graph)):
        seen: set[str] = set()
        for source_rank, item in enumerate(source, start=1):
            if item.evidence_id in seen:
                raise RetrievalError(
                    f"{channel} retrieval returned duplicate evidence {item.evidence_id}"
                )
            seen.add(item.evidence_id)
            if item.rank != source_rank:
                raise RetrievalError(
                    f"{channel} retrieval ranks must be contiguous and match result order"
                )
            increment = 1.0 / (RRF_CONSTANT + source_rank)
            scores[item.evidence_id] += increment
            traced = replace(
                item,
                retrieval_traces=tuple(
                    replace(trace, fusion_score=increment) for trace in item.retrieval_traces
                ),
            )
            combined[item.evidence_id] = (
                _merge_evidence(combined[item.evidence_id], traced)
                if item.evidence_id in combined
                else traced
            )
    ordered = sorted(
        combined.values(), key=lambda item: (-scores[item.evidence_id], item.evidence_id)
    )
    return [
        replace(item, retrieval_score=scores[item.evidence_id], rank=rank)
        for rank, item in enumerate(ordered[:top_k], start=1)
    ]


class Neo4jReadBackend:
    """Read-only Neo4j adapter; queries are fixed above and never user-authored."""

    def __init__(self, driver: Any, *, database: str):
        if not database.strip():
            raise RetrievalError("Neo4j database must be explicit")
        self.driver = driver
        self.database = database

    def read(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> list[Mapping[str, Any]]:
        from neo4j import READ_ACCESS, Query

        with self.driver.session(
            database=self.database,
            default_access_mode=READ_ACCESS,
        ) as session:
            result = session.run(
                Query(cypher, timeout=timeout_seconds), parameters=dict(parameters)
            )
            return result.data()

    def close(self) -> None:
        self.driver.close()


class CanonicalRetriever:
    """Single production retrieval abstraction for semantic, graph, and hybrid modes."""

    def __init__(
        self,
        *,
        embedder: QueryEmbedder | None = None,
        graph_backend: GraphReadBackend | None = None,
        graph_timeout_seconds: float = DEFAULT_GRAPH_TIMEOUT_SECONDS,
    ):
        if graph_timeout_seconds <= 0:
            raise RetrievalError("Graph query timeout must be positive")
        self.embedder = embedder
        self.graph_backend = graph_backend
        self.graph_timeout_seconds = graph_timeout_seconds

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        request.validate()
        binding = validate_snapshot_manifest(request.manifest_path)
        semantic: list[RetrievedEvidence] = []
        graph: list[RetrievedEvidence] = []
        artifact_id = None
        if request.mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID}:
            if self.embedder is None:
                raise RetrievalError("Semantic retrieval requires an explicit query embedder")
            semantic, artifact_id = _semantic_retrieve(binding, request, self.embedder)
        if request.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}:
            if self.graph_backend is None:
                raise RetrievalError("Graph retrieval requires an explicit read-only backend")
            graph = _graph_retrieve(
                binding, request, self.graph_backend, self.graph_timeout_seconds
            )
        items = (
            reciprocal_rank_fusion(semantic, graph, top_k=request.top_k)
            if request.mode == RetrievalMode.HYBRID
            else semantic or graph
        )
        return RetrievalResult(
            query=request.query,
            mode=request.mode.value,
            database_snapshot_id=str(binding.database.snapshot_id),
            projection_id=request.projection_id,
            items=tuple(items),
            embedding_artifact_id=artifact_id,
        )
