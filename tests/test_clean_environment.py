"""Smoke tests for the lean, isolated CI environment."""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (
    PRODUCTION_DATABASE,
    REPOSITORY_ROOT,
    SENSITIVE_ENVIRONMENT_VARIABLES,
)


def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


def test_worker_and_query_cli_imports_are_isolated(tmp_path: Path):
    workspace_log = REPOSITORY_ROOT / "worker.log"
    original_log_state = _file_state(workspace_log)
    environment = os.environ.copy()
    for variable in SENSITIVE_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    environment.update(
        {
            "KLIMA_DB_PATH": str(tmp_path / "unused.sqlite"),
            "KLIMA_TEXT2CYPHER_ENABLED": "0",
            "KLIMA_TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(REPOSITORY_ROOT),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; "
            "blocked=lambda *_args, **_kwargs: (_ for _ in ()).throw("
            "RuntimeError('network disabled')); "
            "socket.create_connection=blocked; socket.socket.connect=blocked; "
            "import crawler.scripts.query_agent; import crawler.scripts.run_worker",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "unused.sqlite").exists()
    assert _file_state(workspace_log) == original_log_state


def test_application_credentials_are_absent():
    assert all(variable not in os.environ for variable in SENSITIVE_ENVIRONMENT_VARIABLES)


def test_unmarked_tests_cannot_open_live_network_connections():
    with pytest.raises(RuntimeError, match="Live network access is disabled"):
        socket.create_connection(("example.com", 443))


def test_production_database_is_blocked_before_sqlite_access():
    with pytest.raises(AssertionError, match="production database"):
        sqlite3.connect(PRODUCTION_DATABASE)
    with pytest.raises(AssertionError, match="production database"):
        PRODUCTION_DATABASE.open("rb")


def test_relative_runtime_artifacts_are_confined_to_test_tmp_path(tmp_path: Path):
    artifact = Path("runtime-smoke.log")
    artifact.write_text("isolated", encoding="utf-8")

    assert artifact.resolve().is_relative_to(tmp_path.resolve())
    assert not (REPOSITORY_ROOT / artifact.name).exists()
