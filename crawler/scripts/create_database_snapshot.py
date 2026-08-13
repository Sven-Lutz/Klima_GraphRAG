"""Export a consistent SQLite snapshot and its reproducibility manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from crawler.core.database import resolve_runtime_database
from crawler.core.snapshots import export_database_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=None,
        help="Source SQLite database; overrides KLIMA_DB_PATH for this invocation",
    )
    parser.add_argument("--output", required=True, help="New checkpointed SQLite snapshot")
    parser.add_argument("--manifest", default=None, help="Manifest path (default: beside snapshot)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    source_database = resolve_runtime_database(args.db)
    binding = export_database_snapshot(
        source_database.path,
        Path(args.output),
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    print(f"source_database_id={source_database.database_id}")
    print(f"snapshot_database={binding.database.path}")
    print(f"snapshot_manifest={binding.manifest_path}")
    print(f"snapshot_id={binding.database.snapshot_id}")


if __name__ == "__main__":
    main()
