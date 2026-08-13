"""Runtime database identity, connection safety, and CLI propagation tests."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from crawler.core.database import (
    DatabaseConfigurationError,
    bind_live_database_state,
    connect_existing_read_only,
    connect_existing_read_write,
    create_sqlite_database,
    default_database_path,
    resolve_runtime_database,
)


def test_runtime_resolution_precedence(monkeypatch, tmp_path):
    environment = tmp_path / "environment.sqlite"
    explicit = tmp_path / "explicit.sqlite"
    monkeypatch.setenv("KLIMA_DB_PATH", str(environment))

    assert resolve_runtime_database(explicit).path == explicit.resolve()
    assert resolve_runtime_database(explicit).source == "explicit"
    assert resolve_runtime_database().path == environment.resolve()
    assert resolve_runtime_database().source == "environment"

    monkeypatch.delenv("KLIMA_DB_PATH")
    assert resolve_runtime_database().path == default_database_path()
    assert resolve_runtime_database().source == "default"


def test_environment_is_resolved_at_runtime_not_import_time(monkeypatch, tmp_path):
    import crawler.core.config as config

    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    monkeypatch.setenv("KLIMA_DB_PATH", str(first))
    assert config.resolve_db_path() == first.resolve()
    monkeypatch.setenv("KLIMA_DB_PATH", str(second))
    assert config.resolve_db_path() == second.resolve()
    assert not hasattr(config, "DB_PATH")


@pytest.mark.parametrize("factory", [connect_existing_read_only, connect_existing_read_write])
def test_missing_operational_database_is_never_created(factory, tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(DatabaseConfigurationError, match="does not exist"):
        factory(missing)
    assert not missing.exists()


def test_connection_factories_preserve_path_identity_and_safety(tmp_path):
    db_path = tmp_path / "fixture.sqlite"
    with create_sqlite_database(db_path, purpose="fixture", wal=True) as created:
        created.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        created.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")

    with connect_existing_read_write(db_path) as writable:
        database_file = writable.execute("PRAGMA database_list").fetchone()[2]
        assert Path(database_file).resolve() == db_path.resolve()
        assert writable.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert writable.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    with connect_existing_read_only(db_path) as readonly:
        assert readonly.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
            readonly.execute("INSERT INTO parent VALUES (1)")


def test_live_state_binding_is_frozen_for_one_invocation(tmp_path):
    db_path = tmp_path / "live.sqlite"
    with create_sqlite_database(db_path, purpose="fixture") as connection:
        connection.execute("CREATE TABLE data (value TEXT)")
    binding = bind_live_database_state(resolve_runtime_database(db_path))
    namespace = binding.cache_namespace

    with connect_existing_read_write(db_path) as connection:
        connection.execute("INSERT INTO data VALUES ('changed')")

    assert binding.cache_namespace == namespace
    assert bind_live_database_state(resolve_runtime_database(db_path)).cache_namespace != namespace


@pytest.mark.parametrize(
    ("module_name", "entrypoint"),
    [
        ("crawler.scripts.chunk_documents", "cli"),
        ("crawler.scripts.generate_embeddings", "cli"),
        ("crawler.scripts.extract_graph_ollama", "cli"),
        ("crawler.scripts.resolve_entities", "cli"),
        ("crawler.scripts.detect_communities", "cli"),
        ("crawler.scripts.summarize_documents", "cli"),
        ("crawler.scripts.analyze_topics", "cli"),
    ],
)
def test_stage_clis_pass_explicit_database_to_stage(monkeypatch, tmp_path, module_name, entrypoint):
    module = importlib.import_module(module_name)
    assert not hasattr(module, "DB_PATH")
    explicit = tmp_path / f"{module_name.rsplit('.', 1)[-1]}.sqlite"
    captured = {}

    def fake_main(*args, **kwargs):
        captured["db_path"] = kwargs["db_path"]

    monkeypatch.setattr(module, "main", fake_main)
    getattr(module, entrypoint)(["--db", str(explicit)])

    assert Path(captured["db_path"]).resolve() == explicit.resolve()


def test_legacy_query_cli_delegates_to_canonical_entrypoint(monkeypatch, tmp_path):
    from crawler.scripts import query_agent

    explicit = tmp_path / "query.sqlite"
    captured = {}

    def fake_main(argv):
        captured["argv"] = argv

    monkeypatch.setattr(query_agent, "canonical_query_main", fake_main)
    argv = ["--db", str(explicit), "--manifest", "snapshot.json", "question"]
    query_agent.main(argv)

    assert captured == {"argv": argv}


def test_evaluation_cli_passes_explicit_database(monkeypatch, tmp_path):
    from crawler.scripts import evaluate

    explicit = tmp_path / "evaluation.sqlite"
    captured = {}

    def fake_run_evaluation(**kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(evaluate, "run_evaluation", fake_run_evaluation)
    evaluate.main(["--db", str(explicit), "--questions", "0"])

    assert captured["db_path"] == str(explicit)


def test_initialization_cli_passes_explicit_database(monkeypatch, tmp_path):
    from crawler.scripts import init_seed_jobs

    explicit = tmp_path / "initialized.sqlite"
    captured = {}
    monkeypatch.setattr(init_seed_jobs, "load_seeds_from_sqlite", lambda: ([], {}))

    def fake_upsert(seeds, crawl_db_path):
        captured["db_path"] = crawl_db_path
        return 0

    monkeypatch.setattr(init_seed_jobs, "upsert_seed_jobs", fake_upsert)
    init_seed_jobs.main(["--db", str(explicit)])

    assert captured["db_path"] == explicit.resolve()


def test_prune_cli_resolves_explicit_database(monkeypatch, tmp_path):
    from crawler.scripts import prune_db

    explicit = tmp_path / "prune.sqlite"
    with create_sqlite_database(explicit, purpose="fixture"):
        pass
    captured = {}

    class Connection:
        def close(self):
            captured["closed"] = True

    def fake_connect(path):
        captured["db_path"] = path
        return Connection()

    monkeypatch.setattr(prune_db, "connect_existing_read_write", fake_connect)
    monkeypatch.setattr(prune_db, "report", lambda connection, path: None)
    prune_db.main(["--db", str(explicit)])

    assert captured["db_path"] == explicit.resolve()
    assert captured["closed"] is True


def test_snapshot_cli_resolves_explicit_source_database(monkeypatch, tmp_path):
    from crawler.scripts import create_database_snapshot

    explicit = tmp_path / "source.sqlite"
    output = tmp_path / "snapshot.sqlite"
    captured = {}

    class Binding:
        database = type(
            "Database",
            (),
            {"path": output.resolve(), "snapshot_id": "snapshot-id"},
        )()
        manifest_path = tmp_path / "snapshot.manifest.json"

    def fake_export(source, target, *, manifest_path):
        captured.update(source=source, target=target, manifest_path=manifest_path)
        return Binding()

    monkeypatch.setattr(create_database_snapshot, "export_database_snapshot", fake_export)
    create_database_snapshot.main(["--db", str(explicit), "--output", str(output)])

    assert captured["source"] == explicit.resolve()
    assert captured["target"] == output
    assert captured["manifest_path"] is None
