"""Tests for default-deny Text2Cypher containment."""

from __future__ import annotations

import sys

import pytest

from crawler.core.cypher_safety import (
    CypherValidationError,
    append_platform_limit,
    validate_read_only_cypher,
)
from crawler.scripts import query_agent

VALID_QUERY = "MATCH (e:Entity) RETURN e.name ORDER BY e.name ASC"


@pytest.fixture(autouse=True)
def safe_defaults(monkeypatch):
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_ENABLED", False)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_USER", "")
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PASSWORD", "")
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED", False)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_MAX_RESULTS", 50)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_TIMEOUT_SECONDS", 5.0)


def test_text2cypher_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(query_agent, "llm", lambda *args, **kwargs: pytest.fail("LLM called"))

    result = query_agent.text2cypher("Wie viele?", "ollama")

    assert "deaktiviert" in result


def test_explicit_enablement_requires_dedicated_readonly_identity(monkeypatch):
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_ENABLED", True)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED", True)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_USER", query_agent.NEO4J_USER)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PASSWORD", "secret")

    result = query_agent.text2cypher("Wie viele?", "ollama")

    assert "schreibgeschützte Neo4j-Identität" in result


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (e) RETURN e; MATCH (n) RETURN n",
        "MATCH (e) CREATE (n) RETURN e",
        "MATCH (e) RETURN e LIMIT 10",
        "CALL db.labels()",
        "MATCH (e) RETURN apoc.version()",
        "LOAD CSV FROM 'https://example.test/data.csv' AS row RETURN row",
        "MATCH p=(e)-[*]->(n) RETURN p",
    ],
)
def test_validator_rejects_unsafe_or_unbounded_queries(query):
    with pytest.raises(CypherValidationError):
        validate_read_only_cypher(query)


def test_validator_accepts_minimal_read_only_query():
    assert validate_read_only_cypher(VALID_QUERY) == VALID_QUERY


def test_platform_adds_parameterized_hard_limit():
    query, parameters = append_platform_limit(VALID_QUERY, 7)

    assert query.endswith("LIMIT $text2cypher_limit")
    assert parameters == {"text2cypher_limit": 7}


def test_invalid_result_limit_is_rejected():
    with pytest.raises(ValueError):
        append_platform_limit(VALID_QUERY, 0)


def test_execution_uses_read_access_timeout_and_limit(monkeypatch):
    captured = {}

    class Result:
        def data(self):
            return [{"name": "Augsburg"}]

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run(self, query, parameters, timeout):
            captured.update(query=query, parameters=parameters, timeout=timeout)
            return Result()

    class Driver:
        def session(self, **kwargs):
            captured["session_kwargs"] = kwargs
            return Session()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(query_agent, "_open_text2cypher_driver", lambda: Driver())
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_MAX_RESULTS", 3)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_TIMEOUT_SECONDS", 2.5)
    fake_neo4j = type("Neo4j", (), {"READ_ACCESS": "READ"})
    monkeypatch.setitem(sys.modules, "neo4j", fake_neo4j)

    rows = query_agent._execute_read_only_cypher(VALID_QUERY)

    assert rows == [{"name": "Augsburg"}]
    assert captured["parameters"] == {"text2cypher_limit": 3}
    assert captured["timeout"] == 2.5
    assert captured["session_kwargs"] == {"default_access_mode": "READ"}
    assert captured["closed"] is True


def test_execution_errors_do_not_expose_connection_details(monkeypatch):
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_ENABLED", True)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED", True)
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_USER", "graph_reader")
    monkeypatch.setattr(query_agent, "TEXT2CYPHER_READONLY_PASSWORD", "secret")
    monkeypatch.setattr(query_agent, "llm", lambda *args, **kwargs: VALID_QUERY)
    monkeypatch.setattr(
        query_agent,
        "_execute_read_only_cypher",
        lambda query: (_ for _ in ()).throw(RuntimeError("bolt://secret-host:7687 password")),
    )

    result = query_agent.text2cypher("Welche Namen?", "ollama")

    assert "konnte nicht ausgeführt" in result
    assert "secret-host" not in result
    assert "password" not in result
