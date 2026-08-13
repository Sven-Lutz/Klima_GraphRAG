"""Canonical, snapshot-scoped Neo4j projection of audited SQLite graph artifacts.

SQLite evidence and extraction records are authoritative. Neo4j receives a
reproducible, projection-namespaced read model; it is never used to infer that
projection work completed. A separate local SQLite ledger owns lifecycle and
batch bookkeeping so a failed transaction can be resumed deterministically.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from crawler.core.database import (
    connect_existing_read_only,
    connect_existing_read_write,
    create_sqlite_database,
)
from crawler.core.extraction import safe_error_summary
from crawler.core.provenance import validate_provenance_v4_schema
from crawler.core.snapshots import SnapshotBinding

PROJECTION_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1
DEFAULT_BATCH_SIZE = 100
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
_RUN_STATUSES = frozenset({"completed", "completed_with_errors"})
_SAFE_URI_SCHEMES = frozenset({"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"})
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\wäöüÄÖÜß\- ]+", re.UNICODE)


class ProjectionError(RuntimeError):
    """Base error for projection validation, writing, or reconciliation."""


class ProjectionScopeError(ProjectionError):
    """Raised when selected runs do not belong to the manifested source scope."""


class ProjectionLedgerError(ProjectionError):
    """Raised when local projection bookkeeping conflicts with an intended run."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-sha256:{digest}"


def _normalise_name(value: str) -> str:
    return _WS.sub(" ", str(value or "").strip())


def _entity_identity_name(value: str) -> str:
    normalised = _normalise_name(value).lower()
    normalised = _PUNCT.sub("", normalised)
    return _WS.sub(" ", normalised).strip()


def _projection_key(projection_id: str, kind: str, canonical_id: str) -> str:
    return _digest(f"{kind}-projection-key", [projection_id, canonical_id])


def _safe_target_uri(uri: str) -> str:
    """Return a credential-free endpoint identity suitable for a local ledger."""
    parsed = urlsplit(uri)
    if parsed.scheme not in _SAFE_URI_SCHEMES or not parsed.hostname:
        raise ProjectionScopeError(
            "Neo4j URI must explicitly use bolt/neo4j (optionally +s/+ssc) and include a host"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ProjectionScopeError(
            "Neo4j URI must not embed credentials; supply the explicit user and password input"
        )
    if parsed.query or parsed.fragment:
        raise ProjectionScopeError("Neo4j URI must not contain query parameters or a fragment")
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname + (f":{parsed.port}" if parsed.port is not None else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


@dataclass(frozen=True, slots=True)
class Neo4jTarget:
    uri: str
    database: str
    username: str

    def validate(self) -> None:
        _safe_target_uri(self.uri)
        if not self.database.strip() or not self.username.strip():
            raise ProjectionScopeError("Neo4j database and username must be explicit")

    @property
    def target_id(self) -> str:
        self.validate()
        return _digest(
            "neo4j-target",
            {
                "uri": _safe_target_uri(self.uri),
                "database": self.database,
                "username": self.username,
            },
        )


@dataclass(frozen=True, slots=True)
class ProjectionConfiguration:
    target: Neo4jTarget
    batch_size: int = DEFAULT_BATCH_SIZE
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS
    project_evidence_excerpt: bool = True

    def validate(self) -> None:
        self.target.validate()
        if self.batch_size < 1 or self.batch_size > 10_000:
            raise ProjectionScopeError("Projection batch size must be between 1 and 10000")
        if self.query_timeout_seconds <= 0:
            raise ProjectionScopeError("Neo4j query timeout must be positive")


@dataclass(frozen=True, slots=True)
class ProjectionBatch:
    ordinal: int
    artifacts: tuple[Mapping[str, Any], ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ProjectionPlan:
    projection_id: str
    snapshot_id: str
    snapshot_database_id: str
    extraction_run_ids: tuple[str, ...]
    projection_schema_version: int
    configuration_fingerprint: str
    target: Neo4jTarget
    source_artifact_counts: Mapping[str, int]
    expected_node_counts: Mapping[str, int]
    scope: Mapping[str, Any]
    artifacts: tuple[Mapping[str, Any], ...]

    def batches(self, batch_size: int) -> tuple[ProjectionBatch, ...]:
        result: list[ProjectionBatch] = []
        for ordinal, offset in enumerate(range(0, len(self.artifacts), batch_size)):
            artifacts = self.artifacts[offset : offset + batch_size]
            result.append(
                ProjectionBatch(
                    ordinal=ordinal,
                    artifacts=artifacts,
                    fingerprint=_digest(
                        "neo4j-projection-batch",
                        [self.projection_id, [row["artifact_id"] for row in artifacts]],
                    ),
                )
            )
        return tuple(result)


class ProjectionWriter(Protocol):
    """Bounded Neo4j write boundary used by production and deterministic tests."""

    def ensure_schema(self) -> None: ...

    def database_identity(self) -> Mapping[str, str]: ...

    def initialize_scope(self, plan: ProjectionPlan) -> None: ...

    def write_batch(self, plan: ProjectionPlan, batch: ProjectionBatch) -> None: ...

    def namespace_counts(self, projection_id: str) -> Mapping[str, int]: ...

    def close(self) -> None: ...


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _load_canonical_names(connection: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(connection, "entity_canonical"):
        return {}
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(entity_canonical)")}
    if not {"original_name", "canonical_name"} <= columns:
        raise ProjectionScopeError("entity_canonical has an incompatible schema")
    result: dict[str, str] = {}
    for original, canonical in connection.execute(
        "SELECT original_name, canonical_name FROM entity_canonical "
        "ORDER BY lower(original_name), canonical_name"
    ):
        key = _normalise_name(str(original)).lower()
        value = _normalise_name(str(canonical))
        if not key or not value:
            continue
        if key in result and result[key] != value:
            raise ProjectionScopeError(
                f"Entity resolution maps {original!r} to conflicting canonical names"
            )
        result[key] = value
    return result


def _resolve_entity_name(value: str, canonical_names: Mapping[str, str]) -> str:
    name = _normalise_name(value)
    return canonical_names.get(name.lower(), name)


def _selected_runs(
    connection: sqlite3.Connection,
    binding: SnapshotBinding,
    run_ids: tuple[str, ...],
) -> dict[str, Mapping[str, Any]]:
    placeholders = ",".join("?" for _ in run_ids)
    connection.row_factory = sqlite3.Row
    rows = list(
        connection.execute(
            f"""
            SELECT run_id, extraction_model, prompt_version, source_type, status,
                   database_snapshot_id, database_id, provider, parameters_json, seed,
                   started_at, completed_at, succeeded_count
            FROM graph_extraction_runs WHERE run_id IN ({placeholders})
            """,
            run_ids,
        )
    )
    by_id = {str(row["run_id"]): dict(row) for row in rows}
    missing = sorted(set(run_ids) - set(by_id))
    if missing:
        raise ProjectionScopeError(f"Unknown extraction run(s): {', '.join(missing)}")

    manifest_source = binding.manifest.get("source")
    manifested_source_id = (
        str(manifest_source.get("database_id")) if isinstance(manifest_source, dict) else ""
    )
    for run_id in run_ids:
        run = by_id[run_id]
        if run["status"] not in _RUN_STATUSES:
            raise ProjectionScopeError(
                f"Extraction run {run_id} is {run['status']!r}; only completed canonical runs "
                "can be projected"
            )
        claimed_snapshot = run["database_snapshot_id"]
        if claimed_snapshot and claimed_snapshot != binding.database.snapshot_id:
            raise ProjectionScopeError(
                f"Extraction run {run_id} is bound to a different database snapshot"
            )
        if not claimed_snapshot and run["database_id"] != manifested_source_id:
            raise ProjectionScopeError(
                f"Extraction run {run_id} does not originate from the manifested database"
            )
    return by_id


def _source_hierarchy(
    connection: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    segment_id: str,
    chunk_id: str | None,
    document_id: str,
    source_url: str,
) -> None:
    document = connection.execute(
        "SELECT url_canonical FROM documents_raw WHERE document_id=?", (document_id,)
    ).fetchone()
    if document is None:
        raise ProjectionScopeError(f"Source document {document_id!r} does not exist")
    if str(document[0]) != source_url:
        raise ProjectionScopeError(
            f"Source document {document_id!r} URL disagrees with immutable evidence"
        )
    if source_type == "segment":
        row = connection.execute(
            "SELECT document_id FROM segments WHERE segment_id=?", (segment_id,)
        ).fetchone()
        if row is None or str(row[0]) != document_id or source_id != segment_id or chunk_id:
            raise ProjectionScopeError(
                f"Segment source {source_id!r} has an inconsistent document hierarchy"
            )
        return
    if source_type != "chunk" or chunk_id != source_id:
        raise ProjectionScopeError(
            f"Unsupported or malformed source identity {source_type}:{source_id}"
        )
    row = connection.execute(
        """
        SELECT c.document_id, c.segment_id, s.document_id
        FROM chunks c LEFT JOIN segments s ON s.segment_id=c.segment_id
        WHERE c.chunk_id=?
        """,
        (chunk_id,),
    ).fetchone()
    if row is None or tuple(str(value) for value in row) != (
        document_id,
        segment_id,
        document_id,
    ):
        raise ProjectionScopeError(
            f"Chunk source {source_id!r} has an inconsistent parent segment/document hierarchy"
        )


def _municipalities_for_selected_evidence(
    connection: sqlite3.Connection, run_ids: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    placeholders = ",".join("?" for _ in run_ids)
    result: dict[str, list[str]] = {}
    for evidence_id, municipality_id in connection.execute(
        f"""
        SELECT DISTINCT g.evidence_id, em.municipality_id
        FROM graph_triplets g
        JOIN evidence_municipalities em ON em.evidence_id=g.evidence_id
        WHERE g.extraction_run_id IN ({placeholders}) AND g.provenance_status='verified'
        ORDER BY g.evidence_id, em.municipality_id
        """,
        run_ids,
    ):
        result.setdefault(str(evidence_id), []).append(str(municipality_id))
    return {key: tuple(values) for key, values in result.items()}


def build_projection_plan(
    binding: SnapshotBinding,
    extraction_run_ids: Sequence[str],
    configuration: ProjectionConfiguration,
) -> ProjectionPlan:
    """Validate and materialize one immutable projection scope before any graph write."""
    configuration.validate()
    run_ids = tuple(
        sorted(set(str(value).strip() for value in extraction_run_ids if str(value).strip()))
    )
    if not run_ids:
        raise ProjectionScopeError("At least one explicit extraction run ID is required")
    snapshot_id = binding.database.snapshot_id
    if not snapshot_id:
        raise ProjectionScopeError("Neo4j projection requires a validated snapshot manifest")

    projection_id = _digest(
        "neo4j-projection",
        {
            "schema_version": PROJECTION_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "extraction_run_ids": run_ids,
            "target_id": configuration.target.target_id,
        },
    )
    config_fingerprint = _digest(
        "neo4j-projection-config",
        {
            "projection_id": projection_id,
            "batch_size": configuration.batch_size,
            "query_timeout_seconds": configuration.query_timeout_seconds,
            "project_evidence_excerpt": configuration.project_evidence_excerpt,
        },
    )

    with connect_existing_read_only(binding.database) as connection:
        validate_provenance_v4_schema(connection)
        connection.row_factory = sqlite3.Row
        runs = _selected_runs(connection, binding, run_ids)
        canonical_names = _load_canonical_names(connection)
        municipalities = _municipalities_for_selected_evidence(connection, run_ids)
        placeholders = ",".join("?" for _ in run_ids)
        rows = list(
            connection.execute(
                f"""
                SELECT g.extraction_run_id, g.source_type, g.source_id, g.segment_id,
                       g.chunk_id, g.document_id, g.municipality_id, g.source_url,
                       g.extraction_model, g.prompt_version, g.evidence_id, g.graph_json,
                       g.entity_count, g.relationship_count, e.content_hash, e.created_at,
                       o.outcome
                FROM graph_triplets g
                JOIN evidence_snapshots e
                  ON e.evidence_id=g.evidence_id AND e.source_type=g.source_type
                 AND e.source_id=g.source_id AND e.document_id=g.document_id
                 AND e.segment_id IS g.segment_id AND e.chunk_id IS g.chunk_id
                 AND e.source_url=g.source_url AND e.evidence_text=g.source_text
                JOIN extraction_source_outcomes o
                  ON o.extraction_run_id=g.extraction_run_id
                 AND o.source_type=g.source_type AND o.source_id=g.source_id
                WHERE g.extraction_run_id IN ({placeholders})
                  AND g.provenance_status='verified'
                ORDER BY g.extraction_run_id, g.source_type, g.source_id
                """,
                run_ids,
            )
        )

        source_counts = {run_id: 0 for run_id in run_ids}
        entity_specs: dict[str, tuple[str, str, str]] = {}
        parsed_rows: list[tuple[sqlite3.Row, list[dict[str, Any]], list[dict[str, Any]]]] = []
        for row in rows:
            run_id = str(row["extraction_run_id"])
            run = runs[run_id]
            if row["outcome"] != "inserted":
                raise ProjectionScopeError(
                    f"Verified artifact in run {run_id} lacks inserted outcome"
                )
            if (row["source_type"], row["extraction_model"], row["prompt_version"]) != (
                run["source_type"],
                run["extraction_model"],
                run["prompt_version"],
            ):
                raise ProjectionScopeError(
                    f"Artifact identity disagrees with extraction run {run_id}"
                )
            _source_hierarchy(
                connection,
                source_type=str(row["source_type"]),
                source_id=str(row["source_id"]),
                segment_id=str(row["segment_id"]),
                chunk_id=str(row["chunk_id"]) if row["chunk_id"] is not None else None,
                document_id=str(row["document_id"]),
                source_url=str(row["source_url"]),
            )
            evidence_municipalities = municipalities.get(str(row["evidence_id"]), ())
            if (
                not evidence_municipalities
                or str(row["municipality_id"]) not in evidence_municipalities
            ):
                raise ProjectionScopeError(
                    f"Evidence {row['evidence_id']} has incomplete municipality provenance"
                )
            try:
                graph = json.loads(str(row["graph_json"]))
            except json.JSONDecodeError as exc:
                raise ProjectionScopeError(
                    f"Artifact {run_id}:{row['source_id']} has invalid JSON"
                ) from exc
            entities = graph.get("entities") if isinstance(graph, dict) else None
            assertions = graph.get("relationships") if isinstance(graph, dict) else None
            if not isinstance(entities, list) or not isinstance(assertions, list):
                raise ProjectionScopeError(
                    f"Artifact {run_id}:{row['source_id']} has invalid graph shape"
                )
            if len(entities) != row["entity_count"] or len(assertions) != row["relationship_count"]:
                raise ProjectionScopeError(f"Artifact {run_id}:{row['source_id']} count mismatch")
            clean_entities: list[dict[str, Any]] = []
            for entity in entities:
                if not isinstance(entity, dict):
                    raise ProjectionScopeError("Graph entity must be an object")
                resolved_name = _resolve_entity_name(str(entity.get("name", "")), canonical_names)
                entity_type = _normalise_name(str(entity.get("type", "")))
                category = _normalise_name(str(entity.get("category", "")))
                if not resolved_name or not entity_type or not category:
                    raise ProjectionScopeError("Graph entity identity fields must not be empty")
                identity_name = _entity_identity_name(resolved_name)
                existing = entity_specs.get(identity_name)
                specification = (resolved_name, entity_type, category)
                if existing is not None and existing[1:] != specification[1:]:
                    raise ProjectionScopeError(
                        f"Ambiguous entity endpoint {resolved_name!r} has conflicting type/category"
                    )
                entity_specs[identity_name] = existing or specification
                clean_entities.append({**entity, "resolved_name": resolved_name})
            parsed_rows.append((row, clean_entities, assertions))
            source_counts[run_id] += 1

        for run_id in run_ids:
            if source_counts[run_id] != int(runs[run_id]["succeeded_count"]):
                raise ProjectionScopeError(
                    f"Extraction run {run_id} has {runs[run_id]['succeeded_count']} successful "
                    f"outcomes but {source_counts[run_id]} verified graph artifacts"
                )

        artifacts: list[dict[str, Any]] = []
        document_ids: set[str] = set()
        segment_ids: set[str] = set()
        chunk_ids: set[str] = set()
        municipality_ids: set[str] = set()
        evidence_ids: set[str] = set()
        entity_ids: set[str] = set()
        mention_count = 0
        assertion_count = 0
        for row, entities, assertions in parsed_rows:
            run_id = str(row["extraction_run_id"])
            source_type = str(row["source_type"])
            source_id = str(row["source_id"])
            artifact_id = _digest("graph-artifact", [run_id, source_type, source_id])
            evidence_id = str(row["evidence_id"])
            document_id = str(row["document_id"])
            segment_id = str(row["segment_id"])
            chunk_id = str(row["chunk_id"]) if row["chunk_id"] is not None else None
            run = runs[run_id]
            mention_rows: list[dict[str, Any]] = []
            for ordinal, entity in enumerate(entities):
                resolved_name = str(entity["resolved_name"])
                identity_name = _entity_identity_name(resolved_name)
                canonical_name, entity_type, category = entity_specs[identity_name]
                entity_id = _digest("entity", [identity_name, entity_type, category])
                entity_ids.add(entity_id)
                mention_id = _digest("entity-mention", [artifact_id, ordinal, entity_id])
                mention_rows.append(
                    {
                        "mention_id": mention_id,
                        "mention_key": _projection_key(projection_id, "mention", mention_id),
                        "entity_id": entity_id,
                        "entity_key": _projection_key(projection_id, "entity", entity_id),
                        "entity_name": canonical_name,
                        "entity_type": entity_type,
                        "category": category,
                        "observed_name": _normalise_name(str(entity.get("name", ""))),
                        "status": _normalise_name(str(entity.get("status", ""))) or None,
                        "metrics_json": _canonical_json(entity.get("metrics") or {}),
                        "ordinal": ordinal,
                    }
                )
            assertion_rows: list[dict[str, Any]] = []
            for ordinal, assertion in enumerate(assertions):
                if not isinstance(assertion, dict):
                    raise ProjectionScopeError("Graph assertion must be an object")
                source_name = _entity_identity_name(
                    _resolve_entity_name(str(assertion.get("source_entity", "")), canonical_names)
                )
                target_name = _entity_identity_name(
                    _resolve_entity_name(str(assertion.get("target_entity", "")), canonical_names)
                )
                subject_spec = entity_specs.get(source_name)
                object_spec = entity_specs.get(target_name)
                if not subject_spec or not object_spec:
                    raise ProjectionScopeError(
                        f"Assertion endpoint in {run_id}:{source_id} is not an entity in the "
                        "selected projection scope"
                    )
                subject_id = _digest("entity", [source_name, subject_spec[1], subject_spec[2]])
                object_id = _digest("entity", [target_name, object_spec[1], object_spec[2]])
                entity_ids.update((subject_id, object_id))
                relation_type = _normalise_name(str(assertion.get("relation_type", ""))).upper()
                if not relation_type:
                    raise ProjectionScopeError("Assertion relation_type must not be empty")
                excerpt = _normalise_name(str(assertion.get("evidence", "")))
                assertion_id = _digest(
                    "assertion",
                    [artifact_id, ordinal, subject_id, relation_type, object_id, excerpt],
                )
                assertion_rows.append(
                    {
                        "assertion_id": assertion_id,
                        "assertion_key": _projection_key(projection_id, "assertion", assertion_id),
                        "subject_key": _projection_key(projection_id, "entity", subject_id),
                        "object_key": _projection_key(projection_id, "entity", object_id),
                        "subject_id": subject_id,
                        "subject_name": subject_spec[0],
                        "subject_type": subject_spec[1],
                        "subject_category": subject_spec[2],
                        "object_id": object_id,
                        "object_name": object_spec[0],
                        "object_type": object_spec[1],
                        "object_category": object_spec[2],
                        "relation_type": relation_type,
                        "evidence_excerpt": (
                            excerpt[:500] if configuration.project_evidence_excerpt else None
                        ),
                        "ordinal": ordinal,
                    }
                )
            evidence_municipalities = municipalities[evidence_id]
            artifact = {
                "projection_id": projection_id,
                "artifact_id": artifact_id,
                "artifact_key": _projection_key(projection_id, "artifact", artifact_id),
                "artifact_content_hash": hashlib.sha256(
                    str(row["graph_json"]).encode("utf-8")
                ).hexdigest(),
                "run_id": run_id,
                "run_key": _projection_key(projection_id, "run", run_id),
                "source_type": source_type,
                "source_id": source_id,
                "document_id": document_id,
                "document_key": _projection_key(projection_id, "document", document_id),
                "segment_id": segment_id,
                "segment_key": _projection_key(projection_id, "segment", segment_id),
                "chunk_id": chunk_id,
                "chunk_key": (
                    _projection_key(projection_id, "chunk", chunk_id) if chunk_id else None
                ),
                "source_url": str(row["source_url"]),
                "evidence_id": evidence_id,
                "evidence_key": _projection_key(projection_id, "evidence", evidence_id),
                "evidence_content_hash": str(row["content_hash"]),
                "evidence_created_at": str(row["created_at"]),
                "extraction_model": str(run["extraction_model"]),
                "prompt_version": str(run["prompt_version"]),
                "municipalities": [
                    {
                        "municipality_id": value,
                        "municipality_key": _projection_key(projection_id, "municipality", value),
                    }
                    for value in evidence_municipalities
                ],
                "mentions": mention_rows,
                "assertions": assertion_rows,
            }
            artifacts.append(artifact)
            document_ids.add(document_id)
            segment_ids.add(segment_id)
            if chunk_id:
                chunk_ids.add(chunk_id)
            municipality_ids.update(evidence_municipalities)
            evidence_ids.add(evidence_id)
            mention_count += len(mention_rows)
            assertion_count += len(assertion_rows)

    expected_counts = {
        "Projection": 1,
        "DatabaseSnapshot": 1,
        "ExtractionRun": len(run_ids),
        "Municipality": len(municipality_ids),
        "Document": len(document_ids),
        "Segment": len(segment_ids),
        "Chunk": len(chunk_ids),
        "Evidence": len(evidence_ids),
        "GraphArtifact": len(artifacts),
        "Entity": len(entity_ids),
        "EntityMention": mention_count,
        "Assertion": assertion_count,
    }
    scope = {
        "projection_id": projection_id,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_database_id": binding.database.database_id,
        "extraction_run_ids": run_ids,
        "source_artifact_counts": source_counts,
        "expected_node_counts": expected_counts,
        "target_id": configuration.target.target_id,
    }
    return ProjectionPlan(
        projection_id=projection_id,
        snapshot_id=snapshot_id,
        snapshot_database_id=binding.database.database_id,
        extraction_run_ids=run_ids,
        projection_schema_version=PROJECTION_SCHEMA_VERSION,
        configuration_fingerprint=config_fingerprint,
        target=configuration.target,
        source_artifact_counts=source_counts,
        expected_node_counts=expected_counts,
        scope=scope,
        artifacts=tuple(artifacts),
    )


class ProjectionLedger:
    """Authoritative local lifecycle record for Neo4j projection attempts."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if self.path.exists():
            self.connection = connect_existing_read_write(self.path, wal=False)
        else:
            self.connection = create_sqlite_database(
                self.path, purpose="projection_ledger", wal=False
            )
        self.connection.row_factory = sqlite3.Row
        try:
            self._ensure_schema()
        except Exception:
            self.connection.close()
            raise

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projection_ledger_schema (
              version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS neo4j_projections (
              projection_id TEXT PRIMARY KEY,
              projection_schema_version INTEGER NOT NULL,
              database_snapshot_id TEXT NOT NULL,
              snapshot_database_id TEXT NOT NULL,
              extraction_run_ids_json TEXT NOT NULL,
              source_artifact_counts_json TEXT NOT NULL,
              expected_node_counts_json TEXT NOT NULL,
              neo4j_target_id TEXT NOT NULL,
              neo4j_database TEXT NOT NULL,
              neo4j_database_identity_json TEXT,
              configuration_fingerprint TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN (
                'pending','running','completed','failed','interrupted'
              )),
              projected_count INTEGER NOT NULL DEFAULT 0,
              failed_count INTEGER NOT NULL DEFAULT 0,
              error_category TEXT,
              error_summary TEXT
            );
            CREATE TABLE IF NOT EXISTS neo4j_projection_batches (
              projection_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL,
              fingerprint TEXT NOT NULL,
              artifact_count INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
              attempts INTEGER NOT NULL DEFAULT 0,
              started_at TEXT,
              completed_at TEXT,
              error_category TEXT,
              error_summary TEXT,
              PRIMARY KEY(projection_id, ordinal),
              FOREIGN KEY(projection_id) REFERENCES neo4j_projections(projection_id)
            );
            """
        )
        existing = self.connection.execute(
            "SELECT version FROM projection_ledger_schema ORDER BY version"
        ).fetchall()
        if not existing:
            self.connection.execute(
                "INSERT INTO projection_ledger_schema VALUES (?, ?)",
                (LEDGER_SCHEMA_VERSION, _utc_now()),
            )
        elif [int(row[0]) for row in existing] != [LEDGER_SCHEMA_VERSION]:
            raise ProjectionLedgerError("Unsupported projection ledger schema")
        expected_columns = {
            "projection_ledger_schema": {"version", "applied_at"},
            "neo4j_projections": {
                "projection_id",
                "projection_schema_version",
                "database_snapshot_id",
                "snapshot_database_id",
                "extraction_run_ids_json",
                "source_artifact_counts_json",
                "expected_node_counts_json",
                "neo4j_target_id",
                "neo4j_database",
                "neo4j_database_identity_json",
                "configuration_fingerprint",
                "started_at",
                "completed_at",
                "updated_at",
                "status",
                "projected_count",
                "failed_count",
                "error_category",
                "error_summary",
            },
            "neo4j_projection_batches": {
                "projection_id",
                "ordinal",
                "fingerprint",
                "artifact_count",
                "status",
                "attempts",
                "started_at",
                "completed_at",
                "error_category",
                "error_summary",
            },
        }
        for table, expected in expected_columns.items():
            actual = {
                str(row[1]) for row in self.connection.execute(f'PRAGMA table_info("{table}")')
            }
            if actual != expected:
                raise ProjectionLedgerError(f"Projection ledger table {table} has schema drift")

    def prepare(self, plan: ProjectionPlan, batches: Sequence[ProjectionBatch]) -> str:
        now = _utc_now()
        row = self.connection.execute(
            "SELECT * FROM neo4j_projections WHERE projection_id=?", (plan.projection_id,)
        ).fetchone()
        immutable = (
            plan.projection_schema_version,
            plan.snapshot_id,
            plan.snapshot_database_id,
            _canonical_json(plan.extraction_run_ids),
            _canonical_json(plan.source_artifact_counts),
            _canonical_json(plan.expected_node_counts),
            plan.target.target_id,
            plan.target.database,
            plan.configuration_fingerprint,
        )
        if row is None:
            self.connection.execute(
                """
                INSERT INTO neo4j_projections(
                  projection_id, projection_schema_version, database_snapshot_id,
                  snapshot_database_id, extraction_run_ids_json,
                  source_artifact_counts_json, expected_node_counts_json,
                  neo4j_target_id, neo4j_database, configuration_fingerprint,
                  updated_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (plan.projection_id, *immutable[:-1], immutable[-1], now),
            )
            previous_status = "pending"
        else:
            actual = tuple(
                row[name]
                for name in (
                    "projection_schema_version",
                    "database_snapshot_id",
                    "snapshot_database_id",
                    "extraction_run_ids_json",
                    "source_artifact_counts_json",
                    "expected_node_counts_json",
                    "neo4j_target_id",
                    "neo4j_database",
                    "configuration_fingerprint",
                )
            )
            if actual != immutable:
                raise ProjectionLedgerError(
                    "Projection ledger conflicts with the intended immutable scope/configuration"
                )
            previous_status = str(row["status"])

        existing_batches = list(
            self.connection.execute(
                "SELECT ordinal, fingerprint, artifact_count FROM neo4j_projection_batches "
                "WHERE projection_id=? ORDER BY ordinal",
                (plan.projection_id,),
            )
        )
        intended = [(batch.ordinal, batch.fingerprint, len(batch.artifacts)) for batch in batches]
        if existing_batches and [tuple(row) for row in existing_batches] != intended:
            raise ProjectionLedgerError(
                "Projection batch plan changed and cannot be resumed safely"
            )
        if not existing_batches:
            self.connection.executemany(
                "INSERT INTO neo4j_projection_batches VALUES (?, ?, ?, ?, 'pending', 0, "
                "NULL, NULL, NULL, NULL)",
                [(plan.projection_id, *item) for item in intended],
            )
        if previous_status == "completed":
            self.connection.execute(
                "UPDATE neo4j_projection_batches SET status='pending', started_at=NULL, "
                "completed_at=NULL, error_category=NULL, error_summary=NULL WHERE projection_id=?",
                (plan.projection_id,),
            )
        else:
            self.connection.execute(
                "UPDATE neo4j_projection_batches SET status='pending', started_at=NULL, "
                "error_category=NULL, error_summary=NULL WHERE projection_id=? AND status IN "
                "('running','failed')",
                (plan.projection_id,),
            )
        self.connection.execute(
            """
            UPDATE neo4j_projections SET status='running', started_at=COALESCE(started_at, ?),
              completed_at=NULL, updated_at=?, failed_count=0,
              error_category=NULL, error_summary=NULL WHERE projection_id=?
            """,
            (now, now, plan.projection_id),
        )
        return previous_status

    def pending_ordinals(self, projection_id: str) -> tuple[int, ...]:
        return tuple(
            int(row[0])
            for row in self.connection.execute(
                "SELECT ordinal FROM neo4j_projection_batches WHERE projection_id=? "
                "AND status!='completed' ORDER BY ordinal",
                (projection_id,),
            )
        )

    def start_batch(self, projection_id: str, ordinal: int) -> None:
        self.connection.execute(
            "UPDATE neo4j_projection_batches SET status='running', attempts=attempts+1, "
            "started_at=?, completed_at=NULL WHERE projection_id=? AND ordinal=?",
            (_utc_now(), projection_id, ordinal),
        )

    def complete_batch(self, projection_id: str, ordinal: int) -> None:
        self.connection.execute(
            "UPDATE neo4j_projection_batches SET status='completed', completed_at=?, "
            "error_category=NULL, error_summary=NULL WHERE projection_id=? AND ordinal=?",
            (_utc_now(), projection_id, ordinal),
        )
        self._refresh_counts(projection_id)

    def fail_batch(self, projection_id: str, ordinal: int, error: BaseException) -> None:
        summary = safe_error_summary(error)
        self.connection.execute(
            "UPDATE neo4j_projection_batches SET status='failed', error_category=?, "
            "error_summary=? WHERE projection_id=? AND ordinal=?",
            (type(error).__name__, summary, projection_id, ordinal),
        )
        self.fail_projection(projection_id, error)

    def _refresh_counts(self, projection_id: str) -> None:
        projected = self.connection.execute(
            "SELECT COALESCE(SUM(artifact_count),0) FROM neo4j_projection_batches "
            "WHERE projection_id=? AND status='completed'",
            (projection_id,),
        ).fetchone()[0]
        self.connection.execute(
            "UPDATE neo4j_projections SET projected_count=?, updated_at=? WHERE projection_id=?",
            (projected, _utc_now(), projection_id),
        )

    def fail_projection(
        self, projection_id: str, error: BaseException, *, status: str = "failed"
    ) -> None:
        failed = self.connection.execute(
            "SELECT COALESCE(SUM(artifact_count),0) FROM neo4j_projection_batches "
            "WHERE projection_id=? AND status='failed'",
            (projection_id,),
        ).fetchone()[0]
        self.connection.execute(
            """
            UPDATE neo4j_projections SET status=?, failed_count=?, updated_at=?,
              error_category=?, error_summary=? WHERE projection_id=?
            """,
            (
                status,
                failed,
                _utc_now(),
                type(error).__name__,
                safe_error_summary(error),
                projection_id,
            ),
        )

    def interrupt(self, projection_id: str, error: BaseException) -> None:
        self.fail_projection(projection_id, error, status="interrupted")

    def complete(
        self, projection_id: str, database_identity: Mapping[str, str], artifact_count: int
    ) -> None:
        pending = self.connection.execute(
            "SELECT COUNT(*) FROM neo4j_projection_batches WHERE projection_id=? "
            "AND status!='completed'",
            (projection_id,),
        ).fetchone()[0]
        if pending:
            raise ProjectionLedgerError("Cannot complete a projection with incomplete batches")
        self.connection.execute(
            """
            UPDATE neo4j_projections SET status='completed', completed_at=?, updated_at=?,
              projected_count=?, failed_count=0, neo4j_database_identity_json=?,
              error_category=NULL, error_summary=NULL WHERE projection_id=?
            """,
            (
                _utc_now(),
                _utc_now(),
                artifact_count,
                _canonical_json(database_identity),
                projection_id,
            ),
        )

    def record(self, projection_id: str) -> Mapping[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM neo4j_projections WHERE projection_id=?", (projection_id,)
        ).fetchone()
        if row is None:
            raise ProjectionLedgerError(f"Unknown projection {projection_id}")
        return dict(row)

    def close(self) -> None:
        self.connection.close()


SCHEMA_CYPHER = (
    *(
        f"CREATE CONSTRAINT {label.lower()}_projection_key IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.projection_key IS UNIQUE"
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
    ),
    "CREATE INDEX entity_canonical_id IF NOT EXISTS FOR (n:Entity) ON (n.entity_id)",
    "CREATE INDEX evidence_canonical_id IF NOT EXISTS FOR (n:Evidence) ON (n.evidence_id)",
    "CREATE INDEX assertion_relation_type IF NOT EXISTS FOR (n:Assertion) ON (n.relation_type)",
    "CREATE INDEX document_canonical_id IF NOT EXISTS FOR (n:Document) ON (n.document_id)",
)


SCOPE_CYPHER = """
MERGE (p:Projection {projection_key: $projection_key})
ON CREATE SET p.projection_id=$projection_id, p.schema_version=$schema_version,
              p.snapshot_id=$snapshot_id, p.run_ids_json=$run_ids_json,
              p.configuration_fingerprint=$configuration_fingerprint
MERGE (s:DatabaseSnapshot {projection_key: $snapshot_key})
ON CREATE SET s.snapshot_id=$snapshot_id, s.database_identity=$snapshot_database_id,
              s.projection_id=$projection_id
MERGE (p)-[:PROJECTS_SNAPSHOT]->(s)
WITH p
UNWIND $runs AS row
MERGE (r:ExtractionRun {projection_key: row.run_key})
ON CREATE SET r.run_id=row.run_id, r.projection_id=$projection_id,
              r.snapshot_id=$snapshot_id
MERGE (p)-[:INCLUDES_RUN]->(r)
"""


ARTIFACT_CYPHER = """
UNWIND $rows AS row
MATCH (run:ExtractionRun {projection_key: row.run_key})
MERGE (document:Document {projection_key: row.document_key})
ON CREATE SET document.document_id=row.document_id, document.projection_id=row.projection_id,
              document.snapshot_id=$snapshot_id, document.source_url=row.source_url
MERGE (segment:Segment {projection_key: row.segment_key})
ON CREATE SET segment.segment_id=row.segment_id, segment.projection_id=row.projection_id,
              segment.snapshot_id=$snapshot_id
MERGE (document)-[:HAS_SEGMENT]->(segment)
MERGE (evidence:Evidence {projection_key: row.evidence_key})
ON CREATE SET evidence.evidence_id=row.evidence_id,
              evidence.projection_id=row.projection_id, evidence.snapshot_id=$snapshot_id,
              evidence.source_type=row.source_type, evidence.source_id=row.source_id,
              evidence.segment_id=row.segment_id, evidence.chunk_id=row.chunk_id,
              evidence.document_id=row.document_id, evidence.source_url=row.source_url,
              evidence.content_hash=row.evidence_content_hash,
              evidence.created_at=row.evidence_created_at,
              evidence.authoritative_store='sqlite'
MERGE (evidence)-[:FROM_DOCUMENT]->(document)
MERGE (artifact:GraphArtifact {projection_key: row.artifact_key})
ON CREATE SET artifact.artifact_id=row.artifact_id,
              artifact.projection_id=row.projection_id, artifact.snapshot_id=$snapshot_id,
              artifact.extraction_run_id=row.run_id, artifact.source_type=row.source_type,
              artifact.source_id=row.source_id, artifact.evidence_id=row.evidence_id,
              artifact.extraction_model=row.extraction_model,
              artifact.prompt_version=row.prompt_version,
              artifact.content_hash=row.artifact_content_hash
MERGE (run)-[:PRODUCED]->(artifact)
MERGE (artifact)-[:USES_EVIDENCE]->(evidence)
FOREACH (_ IN CASE WHEN row.source_type='segment' THEN [1] ELSE [] END |
  MERGE (evidence)-[:DERIVED_FROM]->(segment))
FOREACH (_ IN CASE WHEN row.source_type='chunk' THEN [1] ELSE [] END |
  MERGE (chunk:Chunk {projection_key: row.chunk_key})
  ON CREATE SET chunk.chunk_id=row.chunk_id, chunk.projection_id=row.projection_id,
                chunk.snapshot_id=$snapshot_id
  MERGE (segment)-[:HAS_CHUNK]->(chunk)
  MERGE (evidence)-[:DERIVED_FROM]->(chunk))
"""


MUNICIPALITY_CYPHER = """
UNWIND $rows AS row
MATCH (document:Document {projection_key: row.document_key})
MATCH (evidence:Evidence {projection_key: row.evidence_key})
UNWIND row.municipalities AS value
MERGE (municipality:Municipality {projection_key: value.municipality_key})
ON CREATE SET municipality.municipality_id=value.municipality_id,
              municipality.projection_id=row.projection_id,
              municipality.snapshot_id=$snapshot_id
MERGE (document)-[:ASSOCIATED_WITH]->(municipality)
MERGE (evidence)-[:ASSOCIATED_WITH]->(municipality)
"""


MENTION_CYPHER = """
UNWIND $rows AS row
MATCH (artifact:GraphArtifact {projection_key: row.artifact_key})
MATCH (evidence:Evidence {projection_key: row.evidence_key})
UNWIND row.mentions AS value
MERGE (entity:Entity {projection_key: value.entity_key})
ON CREATE SET entity.entity_id=value.entity_id, entity.projection_id=row.projection_id,
              entity.snapshot_id=$snapshot_id, entity.name=value.entity_name,
              entity.type=value.entity_type, entity.category=value.category
MERGE (mention:EntityMention {projection_key: value.mention_key})
ON CREATE SET mention.mention_id=value.mention_id, mention.projection_id=row.projection_id,
              mention.snapshot_id=$snapshot_id, mention.observed_name=value.observed_name,
              mention.status=value.status, mention.metrics_json=value.metrics_json,
              mention.ordinal=value.ordinal, mention.extraction_run_id=row.run_id,
              mention.source_type=row.source_type, mention.source_id=row.source_id
MERGE (artifact)-[:HAS_MENTION]->(mention)
MERGE (mention)-[:REFERS_TO]->(entity)
MERGE (mention)-[:SUPPORTED_BY]->(evidence)
"""


ASSERTION_CYPHER = """
UNWIND $rows AS row
MATCH (artifact:GraphArtifact {projection_key: row.artifact_key})
MATCH (evidence:Evidence {projection_key: row.evidence_key})
UNWIND row.assertions AS value
MERGE (subject:Entity {projection_key: value.subject_key})
ON CREATE SET subject.entity_id=value.subject_id, subject.projection_id=row.projection_id,
              subject.snapshot_id=$snapshot_id, subject.name=value.subject_name,
              subject.type=value.subject_type, subject.category=value.subject_category
MERGE (object:Entity {projection_key: value.object_key})
ON CREATE SET object.entity_id=value.object_id, object.projection_id=row.projection_id,
              object.snapshot_id=$snapshot_id, object.name=value.object_name,
              object.type=value.object_type, object.category=value.object_category
MERGE (assertion:Assertion {projection_key: value.assertion_key})
ON CREATE SET assertion.assertion_id=value.assertion_id,
              assertion.projection_id=row.projection_id, assertion.snapshot_id=$snapshot_id,
              assertion.relation_type=value.relation_type,
              assertion.evidence_excerpt=value.evidence_excerpt,
              assertion.excerpt_is_derived_cache=true,
              assertion.ordinal=value.ordinal, assertion.extraction_run_id=row.run_id,
              assertion.extraction_model=row.extraction_model,
              assertion.prompt_version=row.prompt_version,
              assertion.source_type=row.source_type, assertion.source_id=row.source_id
MERGE (artifact)-[:HAS_ASSERTION]->(assertion)
MERGE (assertion)-[:SUBJECT]->(subject)
MERGE (assertion)-[:OBJECT]->(object)
MERGE (assertion)-[:SUPPORTED_BY]->(evidence)
"""


class Neo4jProjectionWriter:
    """Production write adapter. Every batch is one bounded Neo4j transaction."""

    def __init__(
        self,
        configuration: ProjectionConfiguration,
        *,
        password: str,
        driver: Any | None = None,
    ):
        configuration.validate()
        if not password:
            raise ProjectionScopeError("Neo4j password must be supplied explicitly")
        self.configuration = configuration
        if driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise ProjectionError(
                    "Neo4j Python driver is not installed; install crawler requirements"
                ) from exc
            driver = GraphDatabase.driver(
                configuration.target.uri,
                auth=(configuration.target.username, password),
            )
        self.driver = driver
        self.driver.verify_connectivity()

    def _run_transaction(self, statements: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        timeout = self.configuration.query_timeout_seconds

        def work(transaction: Any) -> None:
            try:
                import neo4j
            except ImportError:
                neo4j_query = None
            else:
                neo4j_query = neo4j.Query
            for cypher, parameters in statements:
                query = neo4j_query(cypher, timeout=timeout) if neo4j_query else cypher
                transaction.run(query, parameters).consume()

        with self.driver.session(database=self.configuration.target.database) as session:
            session.execute_write(work)

    def ensure_schema(self) -> None:
        self._run_transaction([(statement, {}) for statement in SCHEMA_CYPHER])

    def database_identity(self) -> Mapping[str, str]:
        with self.driver.session(database=self.configuration.target.database) as session:
            record = session.run("CALL db.info() YIELD id, name RETURN id, name").single(
                strict=True
            )
        return {
            "configured_database": self.configuration.target.database,
            "target_id": self.configuration.target.target_id,
            "server_database_id": str(record["id"]),
            "server_database_name": str(record["name"]),
        }

    def initialize_scope(self, plan: ProjectionPlan) -> None:
        parameters = {
            "projection_key": _projection_key(plan.projection_id, "projection", plan.projection_id),
            "projection_id": plan.projection_id,
            "schema_version": plan.projection_schema_version,
            "snapshot_id": plan.snapshot_id,
            "snapshot_database_id": plan.snapshot_database_id,
            "snapshot_key": _projection_key(plan.projection_id, "snapshot", plan.snapshot_id),
            "run_ids_json": _canonical_json(plan.extraction_run_ids),
            "configuration_fingerprint": plan.configuration_fingerprint,
            "runs": [
                {
                    "run_id": run_id,
                    "run_key": _projection_key(plan.projection_id, "run", run_id),
                }
                for run_id in plan.extraction_run_ids
            ],
        }
        self._run_transaction([(SCOPE_CYPHER, parameters)])

    def write_batch(self, plan: ProjectionPlan, batch: ProjectionBatch) -> None:
        parameters = {"rows": list(batch.artifacts), "snapshot_id": plan.snapshot_id}
        self._run_transaction(
            [
                (ARTIFACT_CYPHER, parameters),
                (MUNICIPALITY_CYPHER, parameters),
                (MENTION_CYPHER, parameters),
                (ASSERTION_CYPHER, parameters),
            ]
        )

    def namespace_counts(self, projection_id: str) -> Mapping[str, int]:
        result: dict[str, int] = {}
        with self.driver.session(database=self.configuration.target.database) as session:
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
                record = session.run(
                    f"MATCH (n:{label} {{projection_id: $projection_id}}) RETURN count(n) AS count",
                    projection_id=projection_id,
                ).single()
                result[label] = int(record["count"])
        return result

    def close(self) -> None:
        self.driver.close()


def execute_projection(
    plan: ProjectionPlan,
    configuration: ProjectionConfiguration,
    ledger: ProjectionLedger,
    writer: ProjectionWriter,
) -> Mapping[str, Any]:
    """Execute or resume a projection, completing only after exact namespace verification."""
    batches = plan.batches(configuration.batch_size)
    ledger.prepare(plan, batches)
    try:
        writer.ensure_schema()
        database_identity = writer.database_identity()
        writer.initialize_scope(plan)
        by_ordinal = {batch.ordinal: batch for batch in batches}
        for ordinal in ledger.pending_ordinals(plan.projection_id):
            batch = by_ordinal[ordinal]
            ledger.start_batch(plan.projection_id, ordinal)
            try:
                writer.write_batch(plan, batch)
            except Exception as exc:
                ledger.fail_batch(plan.projection_id, ordinal, exc)
                raise ProjectionError(
                    f"Neo4j projection batch {ordinal} failed; local ledger permits resume"
                ) from exc
            ledger.complete_batch(plan.projection_id, ordinal)
        actual_counts = dict(writer.namespace_counts(plan.projection_id))
        if actual_counts != dict(plan.expected_node_counts):
            error = ProjectionError(
                "Neo4j namespace verification failed: expected "
                f"{dict(plan.expected_node_counts)!r}, found {actual_counts!r}"
            )
            ledger.fail_projection(plan.projection_id, error)
            raise error
        ledger.complete(plan.projection_id, database_identity, len(plan.artifacts))
        return ledger.record(plan.projection_id)
    except (KeyboardInterrupt, SystemExit) as exc:
        ledger.interrupt(plan.projection_id, exc)
        raise
    except Exception as exc:
        if ledger.record(plan.projection_id)["status"] != "failed":
            ledger.fail_projection(plan.projection_id, exc)
        raise


def iter_projection_manifest_rows(ledger_path: str | Path) -> Iterator[Mapping[str, Any]]:
    """Read versioned local projection records for operational inspection tooling."""
    ledger = ProjectionLedger(ledger_path)
    try:
        for row in ledger.connection.execute(
            "SELECT * FROM neo4j_projections ORDER BY updated_at, projection_id"
        ):
            yield dict(row)
    finally:
        ledger.close()
