"""Fixture-only tests for the canonical graph extraction writer lifecycle."""

from __future__ import annotations

import hashlib
import inspect
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crawler.core.database import (
    RuntimeDatabase,
    bind_live_database_state,
    connect_existing_read_write,
    database_id_for_path,
    resolve_runtime_database,
)
from crawler.core.extraction import (
    DuplicateRunSourceError,
    ExtractionConfiguration,
    ExtractionLifecycleError,
    GraphArtifact,
    RunNotResumableError,
    SelectedSource,
    finalize_extraction_run,
    initialize_extraction_run,
    is_stale_running_run,
    persist_graph_artifact,
    record_source_outcome,
    resume_extraction_run,
)
from crawler.core.provenance import (
    GraphProvenance,
    ProvenanceError,
    ensure_provenance_v4,
    finish_extraction_run,
    start_extraction_run,
    validate_provenance_v4_schema,
)
from crawler.core.snapshots import export_database_snapshot, validate_snapshot_manifest
from crawler.scripts import extract_graph_ollama

TEXT = "Die Stadtwerke bauen eine Photovoltaikanlage für kommunalen Klimaschutz. " * 4


def _database(path: Path, *, count: int = 1, chunks: bool = False) -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE documents_raw (
                document_id TEXT PRIMARY KEY, municipality_id TEXT, url_canonical TEXT
            );
            CREATE TABLE segments (
                segment_id TEXT PRIMARY KEY, document_id TEXT, order_index INTEGER,
                segment_type TEXT, text TEXT, segment_hash TEXT, page_ref TEXT,
                impact_score INTEGER, is_negative INTEGER, heading_context TEXT
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY, document_id TEXT, municipality_id TEXT,
                segment_id TEXT, chunk_index INTEGER, text TEXT, heading_context TEXT,
                impact_score INTEGER
            );
            CREATE TABLE municipality_documents (
                municipality_id TEXT NOT NULL, document_id TEXT NOT NULL,
                PRIMARY KEY(municipality_id, document_id)
            );
            """
        )
        for number in range(1, count + 1):
            document_id = f"doc-{number}"
            segment_id = f"seg-{number}"
            conn.execute(
                "INSERT INTO documents_raw VALUES (?, ?, ?)",
                (document_id, "muni-1", f"https://example.test/{document_id}"),
            )
            conn.execute("INSERT INTO municipality_documents VALUES ('muni-1', ?)", (document_id,))
            conn.execute(
                "INSERT INTO segments VALUES (?, ?, ?, 'body', ?, 'hash', NULL, ?, 0, 'Head')",
                (segment_id, document_id, number - 1, TEXT, 30 - number),
            )
            if chunks:
                conn.execute(
                    "INSERT INTO chunks VALUES (?, ?, 'muni-1', ?, 0, ?, 'Head', ?)",
                    (f"chunk-{number}", document_id, segment_id, TEXT, 30 - number),
                )
    return path


def _graph(name: str = "Stadtwerke") -> extract_graph_ollama.KnowledgeGraph:
    return extract_graph_ollama.KnowledgeGraph(
        entities=[
            extract_graph_ollama.Entity(
                name=name, type="Akteur", category="Strom", status="Existierend"
            )
        ]
    )


def _run_row(path: Path, run_id: str) -> tuple:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT status, planned_count, succeeded_count, failed_count, skipped_count, "
            "database_snapshot_id FROM graph_extraction_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()


@pytest.mark.parametrize("chunk_mode", [False, True])
def test_successful_segment_and_chunk_runs(monkeypatch, tmp_path, chunk_mode):
    database = _database(tmp_path / "success.sqlite", chunks=True)
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())

    run_id = extract_graph_ollama.main(
        db_path=str(database), limit=1, min_score=1, use_chunks_table=chunk_mode
    )

    assert _run_row(database, run_id) == ("completed", 1, 1, 0, 0, None)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 1
        assert conn.execute("SELECT outcome FROM extraction_source_outcomes").fetchone() == (
            "inserted",
        )


def test_partial_failure_derives_completed_with_errors(monkeypatch, tmp_path):
    database = _database(tmp_path / "partial.sqlite", count=2)
    calls = iter([_graph("one"), RuntimeError("provider rejected second source")])

    def provider(**_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", provider)
    run_id = extract_graph_ollama.main(db_path=str(database), limit=2, min_score=1)

    assert _run_row(database, run_id) == ("completed_with_errors", 2, 1, 1, 0, None)


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (ConnectionError("ollama offline"), "provider_unavailable", 1),
        (
            extract_graph_ollama.MalformedProviderResponseError("invalid JSON"),
            "malformed_provider_result",
            0,
        ),
    ],
)
def test_total_provider_failure_is_explicit(monkeypatch, tmp_path, error, category, retryable):
    database = _database(tmp_path / f"{category}.sqlite")

    def provider(**_kwargs):
        raise error

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", provider)
    run_id = extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)

    assert _run_row(database, run_id) == ("failed", 1, 0, 1, 0, None)
    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT outcome, error_category, retryable FROM extraction_source_outcomes"
        ).fetchone() == ("failed", category, retryable)


def test_zero_input_run_is_completed_deterministically(monkeypatch, tmp_path):
    database = _database(tmp_path / "empty.sqlite")
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE segments SET impact_score=0")
    provider_calls = 0

    def provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _graph()

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", provider)
    run_id = extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)

    assert _run_row(database, run_id) == ("completed", 0, 0, 0, 0, None)
    assert provider_calls == 0


def test_input_selection_order_is_deterministic_for_equal_scores(tmp_path):
    database = _database(tmp_path / "ordered.sqlite")
    with sqlite3.connect(database) as conn:
        for number in (3, 1, 2):
            conn.execute(
                "INSERT INTO segments VALUES (?, 'doc-1', ?, 'body', ?, 'hash', NULL, "
                "20, 0, 'Head')",
                (f"tied-{number}", number, TEXT),
            )
        rows = extract_graph_ollama.get_chunks_for_extraction(
            conn.cursor(), limit=4, min_score=1, per_doc=5
        )
    assert [row["source_id"] for row in rows] == ["seg-1", "tied-1", "tied-2", "tied-3"]


def test_explicit_cancellation_persists_cancelled(monkeypatch, tmp_path):
    database = _database(tmp_path / "cancel.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())

    run_id = extract_graph_ollama.main(
        db_path=str(database),
        limit=1,
        min_score=1,
        cancellation_requested=lambda: True,
    )

    assert _run_row(database, run_id) == ("cancelled", 1, 0, 0, 0, None)


@pytest.mark.parametrize("parallel", [1, 2])
def test_cancellation_requested_during_provider_prevents_persistence(
    monkeypatch, tmp_path, parallel
):
    database = _database(tmp_path / "cancel-in-flight.sqlite", count=parallel)
    provider_finished = False

    def provider(**_kwargs):
        nonlocal provider_finished
        provider_finished = True
        return _graph()

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", provider)
    run_id = extract_graph_ollama.main(
        db_path=str(database),
        limit=parallel,
        min_score=1,
        parallel=parallel,
        cancellation_requested=lambda: provider_finished,
    )

    assert _run_row(database, run_id) == ("cancelled", parallel, 0, 0, 0, None)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0


def test_keyboard_interrupt_persists_and_resume_does_not_duplicate(monkeypatch, tmp_path):
    database = _database(tmp_path / "interrupt.sqlite", count=2)
    calls = 0

    def interrupt_second(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return _graph()

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        extract_graph_ollama.main(db_path=str(database), limit=2, min_score=1)
    with sqlite3.connect(database) as conn:
        run_id = conn.execute("SELECT run_id FROM graph_extraction_runs").fetchone()[0]
    assert _run_row(database, run_id) == ("interrupted", 2, 1, 0, 0, None)

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())
    resumed = extract_graph_ollama.main(
        db_path=str(database), limit=2, min_score=1, resume_run_id=run_id
    )

    assert resumed == run_id
    assert _run_row(database, run_id) == ("completed", 2, 2, 0, 0, None)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM extraction_source_outcomes").fetchone()[0] == 2


@pytest.mark.parametrize(
    ("chunk_mode", "corruption"),
    [
        (True, "UPDATE chunks SET document_id='contradictory-doc'"),
        (False, "DELETE FROM municipality_documents"),
        (True, "DELETE FROM municipality_documents"),
        (False, "UPDATE documents_raw SET url_canonical='not-a-url'"),
        (True, "DELETE FROM segments"),
        (False, "DELETE FROM documents_raw"),
    ],
)
def test_invalid_hierarchy_is_rejected_before_provider(
    monkeypatch, tmp_path, chunk_mode, corruption
):
    database = _database(tmp_path / "invalid.sqlite", chunks=True)
    with sqlite3.connect(database) as conn:
        conn.execute(corruption)
    provider_calls = 0

    def provider(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _graph()

    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", provider)
    run_id = extract_graph_ollama.main(
        db_path=str(database), limit=1, min_score=1, use_chunks_table=chunk_mode
    )

    assert provider_calls == 0
    assert _run_row(database, run_id) == ("failed", 1, 0, 1, 0, None)


def test_persistence_failure_rolls_back_evidence_and_artifact(monkeypatch, tmp_path):
    database = _database(tmp_path / "rollback.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())
    with sqlite3.connect(database) as conn:
        extract_graph_ollama.setup_db(conn.cursor())
        ensure_provenance_v4(conn)
        conn.execute(
            """
            CREATE TRIGGER fixture_reject_graph BEFORE INSERT ON graph_triplets
            BEGIN SELECT RAISE(ABORT, 'fixture persistence failure'); END
            """
        )

    run_id = extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)

    assert _run_row(database, run_id) == ("failed", 1, 0, 1, 0, None)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 0


@pytest.mark.parametrize(
    "failure_trigger",
    [
        "CREATE TRIGGER fail_evidence BEFORE INSERT ON evidence_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'evidence failure'); END",
        "CREATE TRIGGER fail_outcome BEFORE INSERT ON extraction_source_outcomes "
        "BEGIN SELECT RAISE(ABORT, 'outcome failure'); END",
        "CREATE TRIGGER fail_counter BEFORE UPDATE OF succeeded_count ON graph_extraction_runs "
        "WHEN NEW.succeeded_count > OLD.succeeded_count "
        "BEGIN SELECT RAISE(ABORT, 'counter failure'); END",
    ],
)
def test_atomic_writer_rolls_back_each_persistence_boundary(tmp_path, failure_trigger):
    conn, _runtime, _configuration, _run_id, provenance, artifact = _prepared_core_run(
        tmp_path / "atomic-boundary.sqlite"
    )
    try:
        conn.execute(failure_trigger)
        with pytest.raises((sqlite3.IntegrityError, ProvenanceError)):
            persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extraction_source_outcomes").fetchone()[0] == 0
        assert conn.execute(
            "SELECT succeeded_count, failed_count, skipped_count FROM graph_extraction_runs"
        ).fetchone() == (0, 0, 0)
    finally:
        conn.close()


def test_repeated_experiments_preserve_artifacts_and_reuse_evidence(monkeypatch, tmp_path):
    database = _database(tmp_path / "repeat.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())

    first = extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)
    second = extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)

    assert first != second
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 1
        distinct_runs = conn.execute(
            "SELECT COUNT(DISTINCT extraction_run_id) FROM graph_triplets"
        ).fetchone()[0]
        assert distinct_runs == 2


@pytest.mark.parametrize(
    ("change", "expected_distinct"),
    [("model", 2), ("prompt", 2)],
)
def test_same_source_with_different_configuration(monkeypatch, tmp_path, change, expected_distinct):
    database = _database(tmp_path / f"different-{change}.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())
    extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)
    if change == "model":
        extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1, model="model-2")
        column = "extraction_model"
    else:
        monkeypatch.setattr(extract_graph_ollama, "PROMPT_VERSION", "fixture-prompt-v2")
        extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)
        column = "prompt_version"
    with sqlite3.connect(database) as conn:
        distinct_configurations = conn.execute(
            f"SELECT COUNT(DISTINCT {column}) FROM graph_triplets"
        ).fetchone()[0]
        assert distinct_configurations == expected_distinct


def test_parameter_changes_create_distinct_experiments_without_overwrite(monkeypatch, tmp_path):
    database = _database(tmp_path / "different-parameters.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())

    first = extract_graph_ollama.main(
        db_path=str(database), limit=1, min_score=1, gleaning_rounds=1
    )
    second = extract_graph_ollama.main(
        db_path=str(database), limit=1, min_score=1, gleaning_rounds=2
    )

    with sqlite3.connect(database) as conn:
        fingerprints = conn.execute(
            "SELECT experiment_fingerprint FROM graph_extraction_runs "
            "WHERE run_id IN (?, ?) ORDER BY run_id",
            (first, second),
        ).fetchall()
        assert len({row[0] for row in fingerprints}) == 2
        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 2


def test_same_source_across_snapshot_bindings_preserves_artifact_history(tmp_path):
    database = _database(tmp_path / "snapshot-experiments.sqlite")
    configuration = ExtractionConfiguration(
        provider="fixture",
        model="fixture-model",
        prompt_version="fixture-v1",
        parameters={"temperature": 0},
        seed=7,
        source_type="segment",
    )
    with connect_existing_read_write(database) as conn:
        extract_graph_ollama.setup_db(conn.cursor())
        ensure_provenance_v4(conn)
        for snapshot_id in (
            "snapshot-manifest-sha256:first",
            "snapshot-manifest-sha256:second",
        ):
            runtime = RuntimeDatabase(
                path=database,
                source="explicit",
                database_id=database_id_for_path(database),
                snapshot_id=snapshot_id,
            )
            run_id = initialize_extraction_run(
                conn,
                configuration=configuration,
                database=runtime,
                selected_sources=[SelectedSource("segment", "seg-1", TEXT)],
            )
            provenance = GraphProvenance(
                source_type="segment",
                source_id="seg-1",
                segment_id="seg-1",
                chunk_id=None,
                document_id="doc-1",
                municipality_id="muni-1",
                source_url="https://example.test/doc-1",
                source_text=TEXT,
                extraction_model=configuration.model,
                prompt_version=configuration.prompt_version,
                extraction_run_id=run_id,
            )
            artifact = GraphArtifact(
                legacy_rowid=1,
                segment_hash=hashlib.sha256(TEXT.encode()).hexdigest(),
                graph_json='{"entities":[],"relationships":[]}',
                entity_count=0,
                relationship_count=0,
            )
            persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
            finalize_extraction_run(conn, run_id)

        assert conn.execute("SELECT COUNT(*) FROM graph_triplets").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(DISTINCT database_snapshot_id) FROM graph_extraction_runs"
            ).fetchone()[0]
            == 2
        )


def test_changed_source_text_creates_new_immutable_evidence(monkeypatch, tmp_path):
    database = _database(tmp_path / "changed.sqlite")
    monkeypatch.setattr(extract_graph_ollama, "extract_with_gleaning", lambda **_kwargs: _graph())
    extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE segments SET text=?", (TEXT + "Neue Fassung.",))
    extract_graph_ollama.main(db_path=str(database), limit=1, min_score=1)

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence_snapshots").fetchone()[0] == 2


def _prepared_core_run(path: Path):
    _database(path)
    runtime = bind_live_database_state(resolve_runtime_database(path))
    configuration = ExtractionConfiguration(
        provider="fixture",
        model="fixture-model",
        prompt_version="fixture-v1",
        parameters={"temperature": 0},
        seed=7,
        source_type="segment",
    )
    conn = connect_existing_read_write(path)
    extract_graph_ollama.setup_db(conn.cursor())
    ensure_provenance_v4(conn)
    run_id = initialize_extraction_run(
        conn,
        configuration=configuration,
        database=runtime,
        selected_sources=[SelectedSource("segment", "seg-1", TEXT)],
    )
    provenance = GraphProvenance(
        source_type="segment",
        source_id="seg-1",
        segment_id="seg-1",
        chunk_id=None,
        document_id="doc-1",
        municipality_id="muni-1",
        source_url="https://example.test/doc-1",
        source_text=TEXT,
        extraction_model="fixture-model",
        prompt_version="fixture-v1",
        extraction_run_id=run_id,
    )
    artifact = GraphArtifact(
        legacy_rowid=1,
        segment_hash=hashlib.sha256(TEXT.encode()).hexdigest(),
        graph_json='{"entities":[],"relationships":[]}',
        entity_count=0,
        relationship_count=0,
    )
    return conn, runtime, configuration, run_id, provenance, artifact


def test_duplicate_within_one_run_is_explicit_and_counters_stay_exact(tmp_path):
    conn, _runtime, _configuration, run_id, provenance, artifact = _prepared_core_run(
        tmp_path / "duplicate.sqlite"
    )
    try:
        persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
        with pytest.raises(DuplicateRunSourceError, match="Duplicate source"):
            persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
        summary = finalize_extraction_run(conn, run_id)
        assert (summary.succeeded_count, summary.failed_count, summary.skipped_count) == (1, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM extraction_source_outcomes").fetchone()[0] == 1
    finally:
        conn.close()


def test_unresolved_work_cannot_be_finalized_or_hide_counter_drift(tmp_path):
    conn, _runtime, _configuration, run_id, _provenance, _artifact = _prepared_core_run(
        tmp_path / "unfinished.sqlite"
    )
    try:
        with pytest.raises(ExtractionLifecycleError, match="unfinished selected sources"):
            finalize_extraction_run(conn, run_id)
        assert conn.execute(
            "SELECT status, completed_at FROM graph_extraction_runs WHERE run_id=?", (run_id,)
        ).fetchone() == ("running", None)

        conn.execute("UPDATE graph_extraction_runs SET succeeded_count=1 WHERE run_id=?", (run_id,))
        with pytest.raises(ProvenanceError, match="counters or terminal status"):
            validate_provenance_v4_schema(conn)
    finally:
        conn.close()


def test_canonical_writer_does_not_commit_caller_owned_transaction(tmp_path):
    conn, _runtime, _configuration, _run_id, provenance, artifact = _prepared_core_run(
        tmp_path / "caller-transaction.sqlite"
    )
    try:
        conn.execute("CREATE TABLE caller_marker(value TEXT)")
        conn.execute("BEGIN")
        conn.execute("INSERT INTO caller_marker VALUES ('uncommitted')")
        persist_graph_artifact(conn, provenance=provenance, artifact=artifact)
        assert conn.in_transaction
        conn.execute("ROLLBACK")

        assert conn.execute("SELECT COUNT(*) FROM caller_marker").fetchone()[0] == 0
        for table in (
            "evidence_snapshots",
            "graph_triplets",
            "extraction_source_outcomes",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        conn.close()


def test_skipped_outcome_is_terminal_success_and_legacy_run_apis_fail_closed(tmp_path):
    conn, _runtime, _configuration, run_id, _provenance, _artifact = _prepared_core_run(
        tmp_path / "skipped.sqlite"
    )
    try:
        record_source_outcome(
            conn,
            run_id=run_id,
            source_type="segment",
            source_id="seg-1",
            outcome="skipped",
            error_category="explicit_fixture_skip",
        )
        summary = finalize_extraction_run(conn, run_id)
        assert (summary.status, summary.skipped_count) == ("completed", 1)
        with pytest.raises(ProvenanceError, match="Legacy start_extraction_run"):
            start_extraction_run(
                conn,
                run_id="bypass",
                extraction_model="model",
                prompt_version="prompt",
                source_type="segment",
            )
        with pytest.raises(ProvenanceError, match="Legacy finish_extraction_run"):
            finish_extraction_run(conn, run_id, status="completed")
    finally:
        conn.close()


def test_canonical_graph_persistence_has_no_insert_or_ignore():
    assert "INSERT OR IGNORE" not in inspect.getsource(persist_graph_artifact).upper()
    assert "INSERT OR IGNORE" not in inspect.getsource(extract_graph_ollama.main).upper()


def test_stale_running_detection_and_resume_preserve_run_identity(tmp_path):
    conn, runtime, configuration, run_id, _provenance, _artifact = _prepared_core_run(
        tmp_path / "stale.sqlite"
    )
    try:
        old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        conn.execute(
            "UPDATE graph_extraction_runs SET heartbeat_at=? WHERE run_id=?", (old, run_id)
        )
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert is_stale_running_run(conn, run_id, stale_after=timedelta(hours=1), now=now)
        pending = resume_extraction_run(
            conn,
            run_id=run_id,
            configuration=configuration,
            database=runtime,
            stale_after=timedelta(hours=1),
            now=now,
        )
        assert pending == ["seg-1"]
        assert conn.execute("SELECT COUNT(*) FROM graph_extraction_runs").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("mismatch", ["parameters", "database", "snapshot"])
def test_resume_rejects_experiment_or_database_identity_mismatch(tmp_path, mismatch):
    conn, runtime, configuration, run_id, _provenance, _artifact = _prepared_core_run(
        tmp_path / "resume-mismatch.sqlite"
    )
    try:
        finalize_extraction_run(conn, run_id, forced_status="interrupted")
        candidate_configuration = configuration
        candidate_database = runtime
        if mismatch == "parameters":
            candidate_configuration = ExtractionConfiguration(
                provider=configuration.provider,
                model=configuration.model,
                prompt_version=configuration.prompt_version,
                parameters={"temperature": 1},
                seed=configuration.seed,
                source_type=configuration.source_type,
            )
        elif mismatch == "database":
            candidate_database = RuntimeDatabase(
                path=runtime.path,
                source="explicit",
                database_id="sqlite-path-sha256:different",
                snapshot_id=runtime.snapshot_id,
                live_state_id=runtime.live_state_id,
            )
        else:
            candidate_database = RuntimeDatabase(
                path=runtime.path,
                source="explicit",
                database_id=runtime.database_id,
                snapshot_id="snapshot-manifest-sha256:different",
            )

        with pytest.raises(RunNotResumableError, match="does not match"):
            resume_extraction_run(
                conn,
                run_id=run_id,
                configuration=candidate_configuration,
                database=candidate_database,
                stale_after=timedelta(hours=1),
            )
        assert conn.execute(
            "SELECT status FROM graph_extraction_runs WHERE run_id=?", (run_id,)
        ).fetchone() == ("interrupted",)
    finally:
        conn.close()


def test_manifest_snapshot_id_is_stored_without_fabricating_live_identity(tmp_path):
    path = tmp_path / "identity.sqlite"
    runtime = RuntimeDatabase(
        path=path,
        source="explicit",
        database_id=database_id_for_path(path),
        snapshot_id="snapshot-manifest-sha256:fixture",
    )
    configuration = ExtractionConfiguration(
        provider="fixture",
        model="model",
        prompt_version="prompt",
        parameters={},
        seed=None,
        source_type="segment",
    )
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        run_id = initialize_extraction_run(
            conn,
            configuration=configuration,
            database=runtime,
            selected_sources=[],
        )
        finalize_extraction_run(conn, run_id)
        assert conn.execute(
            "SELECT database_snapshot_id, database_live_state_id FROM graph_extraction_runs"
        ).fetchone() == ("snapshot-manifest-sha256:fixture", None)
    finally:
        conn.close()


def test_provenance_v4_run_survives_snapshot_manifest_validation(tmp_path):
    database = _database(tmp_path / "v4-source.sqlite")
    runtime = bind_live_database_state(resolve_runtime_database(database))
    configuration = ExtractionConfiguration(
        provider="fixture",
        model="model",
        prompt_version="prompt",
        parameters={},
        seed=None,
        source_type="segment",
    )
    with connect_existing_read_write(database) as conn:
        extract_graph_ollama.setup_db(conn.cursor())
        run_id = initialize_extraction_run(
            conn,
            configuration=configuration,
            database=runtime,
            selected_sources=[],
        )
        finalize_extraction_run(conn, run_id)

    binding = export_database_snapshot(database, tmp_path / "v4-snapshot.sqlite")
    validated = validate_snapshot_manifest(binding.manifest_path)

    assert validated.manifest["sqlite"]["provenance_schema_version"] == 4
    assert validated.database.snapshot_id == binding.database.snapshot_id
