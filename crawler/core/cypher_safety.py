"""Conservative validation for the experimental Text2Cypher feature.

The accepted language is intentionally much smaller than Cypher: one MATCH,
one RETURN, an optional WHERE, and an optional ORDER BY. The caller appends the
only permitted LIMIT parameter after validation. This validator complements,
but never replaces, database-level read-only authorization.
"""

from __future__ import annotations

import re


class CypherValidationError(ValueError):
    """Raised when a query is outside the deliberately small read-only subset."""


_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
    | (?P<string>'(?:[^'\\]|\\.|'')*')
    | (?P<backtick>`[^`\r\n]*`)
    | (?P<parameter>\$[^\W\d]\w*)
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<word>[^\W\d]\w*)
    | (?P<symbol><=|>=|<>|=~|<-|->|[()\[\]:,.=<>+\-*])
    """,
    re.VERBOSE,
)

_FORBIDDEN_KEYWORDS = frozenset(
    {
        "CALL",
        "CASE",
        "COMMIT",
        "CONSTRAINT",
        "CREATE",
        "CSV",
        "DATABASE",
        "DBMS",
        "DELETE",
        "DETACH",
        "DO",
        "DROP",
        "FOREACH",
        "GRANT",
        "INDEX",
        "LIMIT",
        "LOAD",
        "MERGE",
        "NODE",
        "OPTIONAL",
        "PROCEDURE",
        "REMOVE",
        "RENAME",
        "REVOKE",
        "ROLLBACK",
        "SET",
        "SHOW",
        "START",
        "TERMINATE",
        "TRANSACTION",
        "UNION",
        "UNWIND",
        "USE",
        "WITH",
        "YIELD",
    }
)
_ALLOWED_KEYWORDS = frozenset(
    {
        "AND",
        "AS",
        "ASC",
        "BY",
        "CONTAINS",
        "DESC",
        "DISTINCT",
        "ENDS",
        "IN",
        "IS",
        "MATCH",
        "NOT",
        "NULL",
        "OR",
        "ORDER",
        "RETURN",
        "STARTS",
        "WHERE",
    }
)
_ALLOWED_FUNCTIONS = frozenset({"AVG", "COLLECT", "COUNT", "MAX", "MIN", "SUM"})


def _tokenize(query: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(query):
        match = _TOKEN_RE.match(query, position)
        if match is None:
            raise CypherValidationError("unsupported token")
        position = match.end()
        kind = match.lastgroup
        value = match.group()
        if kind != "space":
            tokens.append((kind or "", value))
    return tokens


def validate_read_only_cypher(query: str) -> str:
    """Validate a minimal, bounded read-only Cypher query.

    The query must contain exactly one MATCH and one RETURN. LIMIT is rejected
    because the caller appends a parameterized platform limit after validation.
    """
    normalized = (query or "").strip()
    if not normalized:
        raise CypherValidationError("empty query")
    if ";" in normalized:
        raise CypherValidationError("statement separators are not allowed")
    if "//" in normalized or "/*" in normalized or "*/" in normalized:
        raise CypherValidationError("comments are not allowed")

    tokens = _tokenize(normalized)
    if not tokens:
        raise CypherValidationError("empty query")

    words = [value.upper() for kind, value in tokens if kind == "word"]
    forbidden = _FORBIDDEN_KEYWORDS.intersection(words)
    if forbidden:
        raise CypherValidationError("forbidden clause")

    for index, (kind, value) in enumerate(tokens):
        if kind != "word":
            continue
        upper = value.upper()
        if upper in _ALLOWED_KEYWORDS:
            continue
        next_kind, next_value = tokens[index + 1] if index + 1 < len(tokens) else (None, None)
        if next_kind == "symbol" and next_value == "(" and upper not in _ALLOWED_FUNCTIONS:
            raise CypherValidationError("function is not allowed")

    match_positions = [index for index, word in enumerate(words) if word == "MATCH"]
    return_positions = [index for index, word in enumerate(words) if word == "RETURN"]
    if len(match_positions) != 1 or len(return_positions) != 1:
        raise CypherValidationError("exactly one MATCH and one RETURN are required")
    if match_positions[0] != 0 or return_positions[0] <= match_positions[0]:
        raise CypherValidationError("invalid clause order")

    clause_words = [word for word in words if word in {"MATCH", "WHERE", "RETURN", "ORDER", "BY"}]
    expected_order = ["MATCH"]
    if "WHERE" in clause_words:
        expected_order.append("WHERE")
    expected_order.append("RETURN")
    if "ORDER" in clause_words:
        expected_order.extend(["ORDER", "BY"])
    if clause_words != expected_order:
        raise CypherValidationError("unsupported clause order")

    for index, (kind, value) in enumerate(tokens):
        if kind == "symbol" and value == "*":
            before = tokens[index - 1] if index else (None, None)
            after = tokens[index + 1] if index + 1 < len(tokens) else (None, None)
            if before != ("symbol", "(") or after != ("symbol", ")"):
                raise CypherValidationError("unbounded graph expansion is not allowed")

    return normalized


def append_platform_limit(query: str, max_results: int) -> tuple[str, dict[str, int]]:
    """Append the only permitted LIMIT as a parameterized platform control."""
    if not 1 <= int(max_results) <= 1000:
        raise ValueError("Text2Cypher result limit must be between 1 and 1000")
    return f"{query}\nLIMIT $text2cypher_limit", {"text2cypher_limit": int(max_results)}
