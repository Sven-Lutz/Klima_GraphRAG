"""Regression tests for runtime crawl database selection in worker processes."""

from __future__ import annotations

import importlib
import sqlite3
import sys
import threading

import pytest

from crawler.core.config import resolve_db_path
from crawler.core.seed_jobs import SeedJob
from crawler.core.storage import Storage


def _create_seed_jobs_database(db_path):
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE seed_jobs (municipality_id TEXT PRIMARY KEY, seed_url TEXT, "
        "status TEXT, claimed_by TEXT, claimed_at TEXT, done_at TEXT, "
        "heartbeat_at TEXT, last_error TEXT, attempt_count INTEGER)"
    )
    con.commit()
    con.close()


def test_worker_module_imports():
    module = importlib.import_module("crawler.scripts.run_worker")
    assert module.resolve_worker_db_path


def test_runtime_default_uses_current_environment(monkeypatch, tmp_path):
    configured = tmp_path / "configured.sqlite"
    monkeypatch.setenv("KLIMA_DB_PATH", str(configured))

    assert resolve_db_path() == configured
    storage = Storage(raw_dir=tmp_path / "raw")
    try:
        assert storage.db_path == configured
    finally:
        storage.close()


def test_explicit_database_wins_over_environment(monkeypatch, tmp_path):
    configured = tmp_path / "configured.sqlite"
    explicit = tmp_path / "explicit.sqlite"
    explicit.touch()
    monkeypatch.setenv("KLIMA_DB_PATH", str(configured))

    from crawler.scripts.run_worker import resolve_worker_db_path

    assert resolve_worker_db_path(explicit) == explicit


def test_worker_rejects_missing_selected_database(tmp_path):
    from crawler.scripts.run_worker import resolve_worker_db_path

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError, match="Selected crawl database"):
        resolve_worker_db_path(missing)
    assert not missing.exists()


def test_cli_rejects_missing_explicit_database(monkeypatch, tmp_path):
    from crawler.scripts import run_worker

    monkeypatch.setattr(sys, "argv", ["run_worker", "--db", str(tmp_path / "missing.sqlite")])
    with pytest.raises(FileNotFoundError, match="--db"):
        run_worker.main()


def test_cli_passes_explicit_database_to_workers(monkeypatch, tmp_path):
    from crawler.scripts import run_worker

    db_path = tmp_path / "crawl.sqlite"
    _create_seed_jobs_database(db_path)
    captured = {}

    def worker_loop(db_path, *args, **kwargs):
        captured["db_path"] = db_path
        return 0

    monkeypatch.setattr(run_worker, "load_seeds_from_sqlite", lambda: ([], {}))
    monkeypatch.setattr(run_worker, "_worker_loop", worker_loop)
    monkeypatch.setattr(sys, "argv", ["run_worker", "--db", str(db_path), "--limit", "0"])

    run_worker.main()
    assert captured["db_path"] == db_path


def test_worker_passes_one_database_to_engine_and_job_claiming(monkeypatch, tmp_path):
    from crawler.scripts import run_worker

    db_path = tmp_path / "crawl.sqlite"
    _create_seed_jobs_database(db_path)

    captured = {}

    class FakeStorage:
        def resolve_document_links(self):
            return 0

    class FakeEngine:
        def __init__(self, **kwargs):
            captured["engine_db_path"] = kwargs["db_path"]
            self.storage = FakeStorage()

        def run(self, seeds):
            captured["seeds"] = list(seeds)

    jobs = iter([SeedJob("09161000", "https://example.test"), None])

    def claim(connection, worker_id, stale_after_seconds):
        captured["claim_db_path"] = db_path
        return next(jobs)

    monkeypatch.setattr(run_worker, "Engine", FakeEngine)
    monkeypatch.setattr(run_worker, "claim_next_job", claim)

    processed = run_worker._worker_loop(
        db_path=db_path,
        worker_id="test-worker",
        allowed={},
        stale_after=60,
        limit_counter={"limit": None, "claimed": 0},
        limit_lock=threading.Lock(),
        shutdown=threading.Event(),
    )

    assert processed == 1
    assert captured["claim_db_path"] == db_path
    assert captured["engine_db_path"] == db_path
    assert captured["seeds"] == [("09161000", "https://example.test")]
