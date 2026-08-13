"""Repository-wide safety boundaries for the isolated test environment."""

from __future__ import annotations

import builtins
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DATABASE = (REPOSITORY_ROOT / "crawler/data/db/crawl.sqlite").resolve()
SENSITIVE_ENVIRONMENT_VARIABLES = (
    "OPENAI_API_KEY",
    "KLIMA_NEO4J_PASSWORD",
    "KLIMA_TEXT2CYPHER_READONLY_PASSWORD",
)


def _reject_live_network(*_args, **_kwargs):
    raise RuntimeError(
        "Live network access is disabled in tests; use @pytest.mark.network "
        "only for an intentional network test"
    )


_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect

# Test modules import runtime configuration during collection. Give collection a
# harmless explicit database identity before any application module can resolve
# the production default, without creating the file.
sys.dont_write_bytecode = True
for variable in SENSITIVE_ENVIRONMENT_VARIABLES:
    os.environ.pop(variable, None)
os.environ["KLIMA_DB_PATH"] = str(
    Path(tempfile.gettempdir()) / "graphrag-al-collection-do-not-create.sqlite"
)
os.environ["KLIMA_TEXT2CYPHER_ENABLED"] = "0"
os.environ["KLIMA_TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED"] = "0"
socket.create_connection = _reject_live_network
socket.socket.connect = _reject_live_network


def _resolved_file_path(value: Any) -> Path | None:
    if isinstance(value, int):
        return None
    try:
        raw_path = os.fspath(value)
    except TypeError:
        return None
    if isinstance(raw_path, bytes):
        raw_path = os.fsdecode(raw_path)
    if raw_path == ":memory:":
        return None
    if raw_path.startswith("file:"):
        parsed = urlparse(raw_path)
        raw_path = unquote(parsed.path)
    return Path(raw_path).resolve()


def _reject_production_database(value: Any) -> None:
    path = _resolved_file_path(value)
    if path == PRODUCTION_DATABASE:
        raise AssertionError(f"Tests must not access the production database: {path}")


@pytest.fixture(autouse=True)
def isolated_test_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request):
    """Keep every test offline, credential-free, and outside workspace runtime paths."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KLIMA_DB_PATH", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("KLIMA_TEXT2CYPHER_ENABLED", "0")
    monkeypatch.setenv("KLIMA_TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED", "0")
    for variable in SENSITIVE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    original_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        _reject_production_database(database)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)

    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        _reject_production_database(file)
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    original_path_open = Path.open

    def guarded_path_open(path: Path, *args, **kwargs):
        _reject_production_database(path)
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_path_open)

    if request.node.get_closest_marker("network") is not None:
        monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
        monkeypatch.setattr(socket.socket, "connect", _ORIGINAL_SOCKET_CONNECT)
