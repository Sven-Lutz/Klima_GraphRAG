"""Deprecated command alias for the canonical Neo4j projection CLI.

The former rowid-based exporter was removed. This module intentionally owns no
projection semantics; it delegates all work to ``project_graph_to_neo4j``.
"""

from __future__ import annotations

import sys
import warnings
from collections.abc import Sequence

from crawler.scripts.project_graph_to_neo4j import cli as _canonical_cli


def cli(argv: Sequence[str] | None = None) -> None:
    warnings.warn(
        "crawler.scripts.export_graph_to_neo4j is deprecated; use "
        "crawler.scripts.project_graph_to_neo4j",
        DeprecationWarning,
        stacklevel=2,
    )
    _canonical_cli(argv)


def main(*_args, **_kwargs) -> None:
    raise RuntimeError(
        "The legacy Python exporter API was removed because it cannot express a manifested "
        "snapshot/run scope. Invoke crawler.scripts.project_graph_to_neo4j.cli with explicit "
        "projection arguments."
    )


if __name__ == "__main__":
    warnings.simplefilter("default", DeprecationWarning)
    cli(sys.argv[1:])
