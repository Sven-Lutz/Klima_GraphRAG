"""Text2Cypher remains unavailable while its standalone validator stays fail-closed."""

from __future__ import annotations

import pytest

from crawler.core.cypher_safety import (
    CypherValidationError,
    append_platform_limit,
    validate_read_only_cypher,
)
from crawler.scripts import query_agent

VALID_QUERY = "MATCH (e:Entity) RETURN e.name ORDER BY e.name ASC"


def test_text2cypher_cannot_be_enabled_through_legacy_entrypoint():
    result = query_agent.text2cypher("Wie viele?", "ollama")
    context_result = query_agent.text2cypher("Wie viele?", "ollama", return_context=True)

    assert "Disabled" in result
    assert context_result == (result, [])


def test_legacy_answer_path_is_explicitly_retired():
    with pytest.raises(query_agent.LegacyQueryAgentDisabledError, match="CanonicalRetriever"):
        query_agent.answer("question")


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
