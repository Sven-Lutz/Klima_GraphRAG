"""Fail-closed placeholder for community detection on the canonical projection.

The former implementation mutated a legacy Neo4j graph whose Entity/Segment
shape is incompatible with the snapshot-scoped projection. Community reports
will return as a separate lifecycle only after they can bind a projection,
snapshot, extraction runs, and their own LLM run metadata.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


class CommunityProjectionCompatibilityError(RuntimeError):
    """Raised until community detection owns canonical projection semantics."""


_DISABLED_MESSAGE = (
    "Community detection is disabled: the legacy Neo4j writer is incompatible with the "
    "canonical snapshot-scoped projection. Implement a projection-bound community-report "
    "lifecycle before enabling this stage."
)


def main(*_args, **_kwargs) -> None:
    """Fail closed without opening SQLite, Neo4j, or an LLM service."""
    raise CommunityProjectionCompatibilityError(_DISABLED_MESSAGE)


def cli(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Community detection (disabled for the canonical Neo4j projection)"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Explicit SQLite database (retained for CLI compatibility; stage is disabled)",
    )
    args = parser.parse_args(argv)
    main(db_path=args.db)


if __name__ == "__main__":
    cli()
