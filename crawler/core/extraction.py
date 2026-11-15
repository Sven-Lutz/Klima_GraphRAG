"""Canonical graph-extraction run lifecycle and atomic SQLite writer.

Transaction ownership is deliberately narrow: public mutation functions use a
SQLite savepoint and never commit the caller's transaction.  One successful
source write includes immutable evidence, the verified graph artifact, its
outcome, and counters in the same savepoint.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, Sequence

from crawler.core.database import RuntimeDatabase
from crawler.core.provenance import (
    GraphProvenance,
    ProvenanceError,
    SourceType,
    ensure_provenance_v4,
    get_or_create_evidence,
)

RunStatus = Literal[
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
    "interrupted",
]
SourceOutcome = Literal["inserted", "skipped", "rejected", "failed"]
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "completed_with_errors", "failed", "cancelled", "interrupted"}
)


class ExtractionLifecycleError(RuntimeError):
    """Raised when a run would violate the canonical extraction lifecycle."""


class DuplicateRunSourceError(ExtractionLifecycleError):
    """Raised when one source is selected or persisted twice in one run."""


class RunNotResumableError(ExtractionLifecycleError):
    """Raised when resume would change an experiment or duplicate completed work."""


class ExtractionCancelledError(ExtractionLifecycleError):
    """Explicit cooperative cancellation requested by the caller."""


@dataclass(frozen=True, slots=True)
class ExtractionConfiguration:
    provider: str
    model: str
    prompt_version: str
    parameters: Mapping[str, object]
    seed: int | None
    source_type: SourceType

    def canonical_parameters_json(self) -> str:
        return json.dumps(
            dict(self.parameters), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True, slots=True)
class SelectedSource:
    source_type: SourceType
    source_id: str
    source_text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GraphArtifact:
    legacy_rowid: int
    segment_hash: str
    graph_json: str
    entity_count: int
    relationship_count: int


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    status: RunStatus
    planned_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_detail_text(value: str, *, limit: int = 300) -> str:
    message = " ".join(value.split())
    lowered = message.lower()
    if any(secret in lowered for secret in ("password", "authorization:", "api_key", "token=")):
        return "sensitive provider or persistence detail redacted"
    message = re.sub(
        r"(?:https?|(?:bolt|neo4j)(?:\+(?:s|ssc))?)://[^\s'\"]+",
        "[endpoint]",
        message,
        flags=re.IGNORECASE,
    )
    return message[:limit]


def safe_error_summary(error: BaseException, *, limit: int = 300) -> str:
    """Return bounded diagnostics without tracebacks, credentials, or topology."""
    return _safe_detail_text(f"{type(error).__name__}: {error}", limit=limit)


def experiment_fingerprint(
    configuration: ExtractionConfiguration,
    database: RuntimeDatabase,
) -> str:
    payload = {
        "provider": configuration.provider,
        "model": configuration.model,
        "prompt_version": configuration.prompt_version,
        "parameters": dict(configuration.parameters),
        "seed": configuration.seed,
        "source_type": configuration.source_type,
        "database_snapshot_id": database.snapshot_id,
        "database_id": database.database_id,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"extraction-config-sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"SAVEPOINT {name}")


def _rollback_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
    conn.execute(f"RELEASE SAVEPOINT {name}")


def _release_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"RELEASE SAVEPOINT {name}")


def initialize_extraction_run(
    conn: sqlite3.Connection,
    *,
    configuration: ExtractionConfiguration,
    database: RuntimeDatabase,
    selected_sources: Sequence[SelectedSource],
    run_id: str | None = None,
) -> str:
    """Persist a new experiment and its deterministic input ledger before work."""
    ensure_provenance_v4(conn)
    identifier = run_id or str(uuid.uuid4())
    seen: set[tuple[str, str]] = set()
    for source in selected_sources:
        key = (source.source_type, source.source_id)
        if source.source_type != configuration.source_type:
            raise ExtractionLifecycleError("Selected source type does not match run configuration")
        if key in seen:
            raise DuplicateRunSourceError(
                f"Duplicate source selected within run: {source.source_type}:{source.source_id}"
            )
        if not source.source_id.strip():
            raise ExtractionLifecycleError("Selected source ID must not be empty")
        seen.add(key)

    name = "initialize_extraction_run"
    _savepoint(conn, name)
    try:
        timestamp = utc_now()
        parameters_json = configuration.canonical_parameters_json()
        fingerprint = experiment_fingerprint(configuration, database)
        details = json.dumps(
            {
                "database_path_source": database.source,
                "input_order": "ordinal",
                "lifecycle_schema": 4,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO graph_extraction_runs(
                run_id, extraction_model, prompt_version, source_type, started_at,
                status, details_json, provider, parameters_json, seed,
                database_snapshot_id, planned_count, status_details,
                experiment_fingerprint, database_id, database_live_state_id, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                configuration.model,
                configuration.prompt_version,
                configuration.source_type,
                timestamp,
                details,
                configuration.provider,
                parameters_json,
                configuration.seed,
                database.snapshot_id,
                len(selected_sources),
                "input_set_persisted",
                fingerprint,
                database.database_id,
                database.live_state_id,
                timestamp,
            ),
        )
        for ordinal, source in enumerate(selected_sources):
            conn.execute(
                """
                INSERT INTO extraction_run_sources(
                    extraction_run_id, source_type, source_id, ordinal,
                    source_content_hash, selected_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    source.source_type,
                    source.source_id,
                    ordinal,
                    source.content_hash,
                    timestamp,
                ),
            )
    except Exception:
        _rollback_savepoint(conn, name)
        raise
    else:
        _release_savepoint(conn, name)
    return identifier


def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | tuple:
    row = conn.execute(
        """
        SELECT run_id, status, source_type, extraction_model, prompt_version,
               provider, parameters_json, seed, experiment_fingerprint,
               database_snapshot_id, database_id, database_live_state_id,
               heartbeat_at, planned_count, succeeded_count, failed_count, skipped_count
        FROM graph_extraction_runs WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise ExtractionLifecycleError(f"Unknown extraction run: {run_id}")
    return row


def is_stale_running_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
) -> bool:
    row = _run_row(conn, run_id)
    if row[1] != "running":
        return False
    heartbeat_value = conn.execute(
        "SELECT COALESCE(heartbeat_at, started_at) FROM graph_extraction_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()[0]
    heartbeat = datetime.fromisoformat(str(heartbeat_value).replace("Z", "+00:00"))
    return heartbeat <= (now or datetime.now(timezone.utc)) - stale_after


def stale_running_run_ids(
    conn: sqlite3.Connection,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute("SELECT run_id FROM graph_extraction_runs WHERE status='running'")
        if is_stale_running_run(conn, str(row[0]), stale_after=stale_after, now=now)
    ]


def resume_extraction_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    configuration: ExtractionConfiguration,
    database: RuntimeDatabase,
    stale_after: timedelta,
    now: datetime | None = None,
) -> list[str]:
    """Resume the same experiment and return only sources lacking outcomes."""
    ensure_provenance_v4(conn)
    row = _run_row(conn, run_id)
    expected = (
        configuration.source_type,
        configuration.model,
        configuration.prompt_version,
        configuration.provider,
        configuration.canonical_parameters_json(),
        configuration.seed,
        experiment_fingerprint(configuration, database),
        database.snapshot_id,
        database.database_id,
    )
    if tuple(row[2:11]) != expected:
        raise RunNotResumableError("Resume configuration or database identity does not match run")
    status = str(row[1])
    stale = status == "running" and is_stale_running_run(
        conn, run_id, stale_after=stale_after, now=now
    )
    if status != "interrupted" and not stale:
        raise RunNotResumableError(
            f"Run {run_id} is not interrupted or stale-running (status={status})"
        )
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    conn.execute(
        "UPDATE graph_extraction_runs SET status='running', completed_at=NULL, "
        "heartbeat_at=?, status_details='resumed_existing_run' WHERE run_id=?",
        (timestamp, run_id),
    )
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT s.source_id FROM extraction_run_sources s
            LEFT JOIN extraction_source_outcomes o
              ON o.extraction_run_id=s.extraction_run_id
             AND o.source_type=s.source_type AND o.source_id=s.source_id
            WHERE s.extraction_run_id=? AND o.source_id IS NULL
            ORDER BY s.ordinal
            """,
            (run_id,),
        )
    ]


def selected_source_hash(conn: sqlite3.Connection, run_id: str, source_id: str) -> str:
    row = conn.execute(
        "SELECT source_content_hash FROM extraction_run_sources "
        "WHERE extraction_run_id=? AND source_id=?",
        (run_id, source_id),
    ).fetchone()
    if row is None:
        raise ExtractionLifecycleError(f"Source {source_id!r} is not selected for run {run_id}")
    return str(row[0])


def _assert_source_pending(
    conn: sqlite3.Connection, run_id: str, source_type: SourceType, source_id: str
) -> None:
    selected = conn.execute(
        "SELECT 1 FROM extraction_run_sources "
        "WHERE extraction_run_id=? AND source_type=? AND source_id=?",
        (run_id, source_type, source_id),
    ).fetchone()
    if selected is None:
        raise ExtractionLifecycleError(
            f"Source {source_type}:{source_id} is not in run {run_id}'s input ledger"
        )
    outcome = conn.execute(
        "SELECT outcome FROM extraction_source_outcomes "
        "WHERE extraction_run_id=? AND source_type=? AND source_id=?",
        (run_id, source_type, source_id),
    ).fetchone()
    if outcome is not None:
        raise DuplicateRunSourceError(
            f"Duplicate source in run {run_id}: {source_type}:{source_id} "
            f"already has outcome {outcome[0]}"
        )


def _refresh_counters(conn: sqlite3.Connection, run_id: str) -> tuple[int, int, int, int]:
    inserted, failed, skipped, total = conn.execute(
        """
        SELECT
          SUM(CASE WHEN outcome='inserted' THEN 1 ELSE 0 END),
          SUM(CASE WHEN outcome IN ('failed', 'rejected') THEN 1 ELSE 0 END),
          SUM(CASE WHEN outcome='skipped' THEN 1 ELSE 0 END),
          COUNT(*)
        FROM extraction_source_outcomes WHERE extraction_run_id=?
        """,
        (run_id,),
    ).fetchone()
    values = (int(inserted or 0), int(failed or 0), int(skipped or 0), int(total or 0))
    conn.execute(
        "UPDATE graph_extraction_runs SET succeeded_count=?, failed_count=?, "
        "skipped_count=?, heartbeat_at=? WHERE run_id=?",
        (*values[:3], utc_now(), run_id),
    )
    return values


def record_source_outcome(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_type: SourceType,
    source_id: str,
    outcome: Literal["skipped", "rejected", "failed"],
    error_category: str | None = None,
    error_summary: str | None = None,
    retryable: bool = False,
) -> None:
    """Atomically persist one explicit non-success outcome and refresh counters."""
    name = "record_extraction_outcome"
    _savepoint(conn, name)
    try:
        run = _run_row(conn, run_id)
        if run[1] != "running":
            raise ExtractionLifecycleError(
                f"Cannot record source outcome for run with status {run[1]!r}"
            )
        _assert_source_pending(conn, run_id, source_type, source_id)
        conn.execute(
            """
            INSERT INTO extraction_source_outcomes(
                extraction_run_id, source_type, source_id, outcome, error_category,
                occurred_at, retryable, error_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_type,
                source_id,
                outcome,
                error_category[:80] if error_category else None,
                utc_now(),
                int(retryable),
                _safe_detail_text(error_summary) if error_summary else None,
            ),
        )
        _refresh_counters(conn, run_id)
    except Exception:
        _rollback_savepoint(conn, name)
        raise
    else:
        _release_savepoint(conn, name)


def persist_graph_artifact(
    conn: sqlite3.Connection,
    *,
    provenance: GraphProvenance,
    artifact: GraphArtifact,
) -> str:
    """Atomically persist evidence, verified graph, inserted outcome, and counters."""
    name = "persist_graph_source"
    _savepoint(conn, name)
    try:
        provenance.validate()
        try:
            graph = json.loads(artifact.graph_json)
        except json.JSONDecodeError as exc:
            raise ExtractionLifecycleError("Graph artifact is not valid JSON") from exc
        valid_shape = (
            isinstance(graph, dict)
            and isinstance(graph.get("entities"), list)
            and isinstance(graph.get("relationships"), list)
        )
        if not valid_shape:
            raise ExtractionLifecycleError(
                "Graph artifact must contain entity and relationship arrays"
            )
        if artifact.entity_count != len(graph["entities"]) or artifact.relationship_count != len(
            graph["relationships"]
        ):
            raise ExtractionLifecycleError("Graph artifact counts do not match its JSON payload")
        if artifact.entity_count < 0 or artifact.relationship_count < 0:
            raise ExtractionLifecycleError("Graph artifact counts must be non-negative")
        source_hash = hashlib.sha256(provenance.source_text.encode("utf-8")).hexdigest()
        if artifact.segment_hash != source_hash:
            raise ExtractionLifecycleError("Graph artifact source hash does not match evidence")
        _assert_source_pending(
            conn,
            provenance.extraction_run_id,
            provenance.source_type,
            provenance.source_id,
        )
        run = _run_row(conn, provenance.extraction_run_id)
        if run[1] != "running":
            raise ExtractionLifecycleError(f"Cannot persist source into run with status {run[1]!r}")
        if (run[2], run[3], run[4]) != (
            provenance.source_type,
            provenance.extraction_model,
            provenance.prompt_version,
        ):
            raise ProvenanceError("Artifact provenance does not match extraction run identity")
        expected_hash = selected_source_hash(
            conn, provenance.extraction_run_id, provenance.source_id
        )
        actual_hash = hashlib.sha256(provenance.source_text.encode("utf-8")).hexdigest()
        if expected_hash != actual_hash:
            raise ProvenanceError("Selected source content changed after run initialization")
        evidence_id = get_or_create_evidence(conn, provenance)
        try:
            conn.execute(
                """
                INSERT INTO graph_triplets(
                    municipality_id, document_id, segment_rowid, segment_hash,
                    model_name, graph_json, entity_count, relationship_count,
                    source_type, source_id, segment_id, chunk_id, source_url, source_text,
                    extraction_model, prompt_version, extraction_run_id,
                    provenance_status, evidence_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified', ?)
                """,
                (
                    provenance.municipality_id,
                    provenance.document_id,
                    artifact.legacy_rowid,
                    artifact.segment_hash,
                    provenance.extraction_model,
                    artifact.graph_json,
                    artifact.entity_count,
                    artifact.relationship_count,
                    provenance.source_type,
                    provenance.source_id,
                    provenance.segment_id,
                    provenance.chunk_id,
                    provenance.source_url,
                    provenance.source_text,
                    provenance.extraction_model,
                    provenance.prompt_version,
                    provenance.extraction_run_id,
                    evidence_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            duplicate = conn.execute(
                "SELECT id FROM graph_triplets WHERE extraction_run_id=? "
                "AND source_type=? AND source_id=?",
                (
                    provenance.extraction_run_id,
                    provenance.source_type,
                    provenance.source_id,
                ),
            ).fetchone()
            if duplicate is not None:
                raise DuplicateRunSourceError(
                    f"Graph artifact already exists for source {provenance.source_id!r} "
                    f"within run {provenance.extraction_run_id}"
                ) from exc
            raise
        conn.execute(
            """
            INSERT INTO extraction_source_outcomes(
                extraction_run_id, source_type, source_id, outcome, occurred_at, retryable
            ) VALUES (?, ?, ?, 'inserted', ?, 0)
            """,
            (
                provenance.extraction_run_id,
                provenance.source_type,
                provenance.source_id,
                utc_now(),
            ),
        )
        _refresh_counters(conn, provenance.extraction_run_id)
    except Exception:
        _rollback_savepoint(conn, name)
        raise
    else:
        _release_savepoint(conn, name)
        return evidence_id


def finalize_extraction_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    forced_status: Literal["failed", "cancelled", "interrupted"] | None = None,
    status_details: str | None = None,
) -> RunSummary:
    """Derive a terminal state from persisted outcomes, or record an explicit stop."""
    name = "finalize_extraction_run"
    _savepoint(conn, name)
    try:
        row = _run_row(conn, run_id)
        if row[1] != "running":
            raise ExtractionLifecycleError(
                f"Run {run_id} cannot be finalized from status {row[1]!r}"
            )
        planned = int(row[13])
        succeeded, failed, skipped, total = _refresh_counters(conn, run_id)
        if forced_status is not None:
            status: RunStatus = forced_status
        elif total < planned:
            raise ExtractionLifecycleError(
                f"Run {run_id} still has {planned - total} unfinished selected sources"
            )
        elif failed == 0:
            status = "completed"
        elif succeeded > 0:
            status = "completed_with_errors"
        else:
            status = "failed"
        details = status_details or (
            "no_inputs_selected" if planned == 0 else "derived_from_outcomes"
        )
        conn.execute(
            "UPDATE graph_extraction_runs SET status=?, completed_at=?, status_details=? "
            "WHERE run_id=?",
            (status, utc_now(), _safe_detail_text(details), run_id),
        )
    except Exception:
        _rollback_savepoint(conn, name)
        raise
    else:
        _release_savepoint(conn, name)
        return RunSummary(run_id, status, planned, succeeded, failed, skipped)
