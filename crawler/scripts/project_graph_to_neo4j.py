"""CLI for the canonical, manifested-snapshot Neo4j projection."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from crawler.core.extraction import safe_error_summary
from crawler.core.neo4j_projection import (
    Neo4jProjectionWriter,
    Neo4jTarget,
    ProjectionConfiguration,
    ProjectionLedger,
    build_projection_plan,
    execute_projection,
)
from crawler.core.snapshots import validate_snapshot_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Project verified extraction artifacts from one manifested SQLite snapshot "
            "into an explicit Neo4j projection namespace"
        )
    )
    parser.add_argument(
        "--snapshot-manifest",
        required=True,
        help="Validated .manifest.json beside the immutable SQLite snapshot",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        required=True,
        help="Canonical completed extraction run to include; repeat for multiple runs",
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="Local SQLite projection lifecycle ledger (created if absent)",
    )
    parser.add_argument("--neo4j-uri", required=True, help="Explicit Neo4j bolt/neo4j URI")
    parser.add_argument("--neo4j-database", required=True, help="Explicit Neo4j database name")
    parser.add_argument("--neo4j-user", required=True, help="Explicit write principal")
    parser.add_argument(
        "--neo4j-password-env",
        required=True,
        help="Name of the environment variable containing the projection principal password",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--query-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--omit-evidence-excerpts",
        action="store_true",
        help="Do not project relationship-specific derived evidence excerpts",
    )
    return parser


def cli(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    password = os.environ.get(args.neo4j_password_env)
    if not password:
        raise SystemExit(
            f"Neo4j password environment variable {args.neo4j_password_env!r} is unset or empty"
        )
    binding = validate_snapshot_manifest(args.snapshot_manifest)
    configuration = ProjectionConfiguration(
        target=Neo4jTarget(
            uri=args.neo4j_uri,
            database=args.neo4j_database,
            username=args.neo4j_user,
        ),
        batch_size=args.batch_size,
        query_timeout_seconds=args.query_timeout_seconds,
        project_evidence_excerpt=not args.omit_evidence_excerpts,
    )
    plan = build_projection_plan(binding, args.run_id, configuration)
    ledger = ProjectionLedger(args.ledger)
    writer = None
    try:
        ledger.prepare(plan, plan.batches(configuration.batch_size))
        try:
            writer = Neo4jProjectionWriter(configuration, password=password)
        except Exception as exc:
            ledger.fail_projection(plan.projection_id, exc)
            raise SystemExit(
                f"Neo4j projection failed before writes: {safe_error_summary(exc)}"
            ) from None
        record = execute_projection(plan, configuration, ledger, writer)
    finally:
        if writer is not None:
            writer.close()
        ledger.close()
    print(
        f"projection_id={plan.projection_id} snapshot_id={plan.snapshot_id} "
        f"status={record['status']} projected_count={record['projected_count']}"
    )


if __name__ == "__main__":
    cli()
