"""Deprecated compatibility entry point for the former query-agent experiment.

Production retrieval is owned exclusively by :mod:`crawler.scripts.query` and
``crawler.core.retrieval``. The former LLM router, global-search experiment,
critic loop, and Text2Cypher executor are intentionally unavailable.
"""

from __future__ import annotations

from crawler.scripts.query import main as canonical_query_main

TEXT2CYPHER_DISABLED_MESSAGE = (
    "[Text2Cypher] Disabled. Use the bounded canonical semantic/graph/hybrid retrieval API."
)


class LegacyQueryAgentDisabledError(RuntimeError):
    """Raised when code attempts to use the removed competing retrieval path."""


def text2cypher(*_args, return_context: bool = False, **_kwargs):
    """Remain fail-closed for callers that still probe the experimental feature."""
    if return_context:
        return TEXT2CYPHER_DISABLED_MESSAGE, []
    return TEXT2CYPHER_DISABLED_MESSAGE


def answer(*_args, **_kwargs):
    raise LegacyQueryAgentDisabledError(
        "query_agent.answer was retired; use CanonicalRetriever with a manifested snapshot"
    )


def main(argv: list[str] | None = None) -> None:
    canonical_query_main(argv)


if __name__ == "__main__":
    main()
