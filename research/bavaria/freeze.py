"""Freeze small research receipts after acquisition stops; never copy originals."""

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from .acquisition import connect, now
from .analysis import coverage


def file_sha(path, compressed=False):
    hasher = hashlib.sha256()
    opener = gzip.open if compressed else open
    with opener(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            hasher.update(block)
    return hasher.hexdigest()


def freeze(db, root, output):
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError("Freeze output must not already exist")
    with connect(db) as conn:
        pending = conn.execute(
            "SELECT count(*) FROM municipalities WHERE status='NOT_CHECKED'"
        ).fetchone()[0]
        if pending:
            raise ValueError(
                f"Acquisition has {pending} undisposed municipalities; do not freeze yet"
            )
        originals = [dict(r) for r in conn.execute("SELECT * FROM documents")]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    if integrity != "ok" or foreign_keys:
        raise ValueError("Database integrity failure")
    for row in originals:
        path = Path(row["raw_path"])
        if file_sha(path, path.suffix == ".gz") != row["sha256"]:
            raise ValueError("Source integrity violation: " + row["sha256"])
    report = coverage(db, output)
    records = [json.loads(x) for x in (output / "coverage.jsonl").read_text().splitlines()]
    fields = [
        "ags",
        "official_name",
        "municipality_type",
        "district",
        "region",
        "population",
        "population_date",
        "registry_date",
        "homepage_candidate",
        "domain_status",
        "coverage_status",
        "checked_at",
        "error",
        "source_versions",
        "parsed_versions",
        "source_url",
        "source_sha256",
    ]
    with (output / "municipalities.csv").open("w") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(records)
    # The compact CSV is the versioned full denominator; bulky JSONL stays local.
    (output / "coverage.jsonl").unlink()
    selected = [
        "registry/report.json",
        "wave01-report.json",
        "local-inventory.json",
        "historical-integrity.json",
        "text-model/manifest.json",
        "projection01.report.json",
        "study03/report.json",
        "study03/results.jsonl",
        "study03/by-family.json",
        "study03/embedding-manifest.json",
        "ie4/report.json",
        "ie4/v2/report.json",
        "project-tests-final.txt",
        "research-tests.txt",
        "ruff-latest.txt",
        "mypy-latest.txt",
        "acquisition-tests.txt",
        "resource-blocker.json",
        "review-final/candidate-report.json",
        "reviewer-packet/report.json",
        "reviewer-packet/A/tasks.jsonl",
        "reviewer-packet/A/judgments.csv",
        "reviewer-packet/B/tasks.jsonl",
        "reviewer-packet/B/judgments.csv",
        "reviewer-packet/adjudication.csv",
    ]
    for name in selected:
        source = root / name
        if source.is_file():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if target.suffix == ".csv":
                target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
    (output / "dependencies.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    )
    # No full database duplicate: local ledger is frozen in place and content-bound.
    with connect(db) as conn:
        checkpoint = list(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"))
    disk = shutil.disk_usage(root)
    receipt = {
        "at": now(),
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], text=True).strip(),
        "implementation_sha256": {
            str(p): file_sha(p) for p in sorted(Path("research/bavaria").glob("*.py"))
        },
        "database": str(Path(db).resolve()),
        "database_sha256": file_sha(db),
        "sqlite_version": sqlite3.sqlite_version,
        "original_versions_verified": len(originals),
        "foreign_key_violations": foreign_keys,
        "sqlite_integrity": integrity,
        "wal_checkpoint": [list(r) for r in checkpoint],
        "free_disk_bytes": disk.free,
        "generated_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "direct_paid_inference_calls": 0,
        "direct_paid_inference_eur": 0,
        "internal_agent_usage": "NOT_MEASURED_NOT_CLAIMED_FREE",
        "backup": "LOCAL_ONLY_NO_OFF_DEVICE_BACKUP",
        "coverage": report,
        "population_disposition_is_not_policy_coverage": True,
        "network_bytes": "RECORDED_RESPONSE_BODIES_ONLY_NOT_COMPLETE_WIRE_TRAFFIC",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
    artifact_paths = [
        Path(db),
        root / "projection01.sqlite",
        root / "study03/embeddings.npy",
        root / "study03/query_embeddings.npy",
        root / "text-model/manifest.json",
        root / "review-final/candidates.jsonl",
    ]
    with (output / "ARTIFACTS.csv").open("w") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["local_path", "bytes", "sha256", "retention"])
        for path in artifact_paths:
            writer.writerow(
                [
                    str(path),
                    path.stat().st_size,
                    file_sha(path),
                    "local_only_no_source_redistribution",
                ]
            )
    manifest = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest.append(f"{file_sha(path)}  {path.relative_to(output)}")
    (output / "SHA256SUMS").write_text("\n".join(manifest) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.db, args.root, args.output), indent=2))
