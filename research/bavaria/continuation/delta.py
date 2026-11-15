"""Resume deferred frames in a bounded delta using the existing crawler schema.

The base ledger is read-only. Existing original bytes and cached requests are
referenced, never copied or overwritten. A delta is an explicit source snapshot,
not an implicit replacement for the base database.
"""

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from crawler.core.models import ParseResult
from research.bavaria import acquisition as crawl

MAX_DELTA = 96 * 1024**2
DRIFT_GUARD = 256 * 1024**2
NEXT_DOCUMENT = 12 * 1024**2


def read_base(path):
    conn = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run(base, db, root):
    base, db, root = Path(base).resolve(strict=True), Path(db).resolve(), Path(root).resolve()
    if base == db:
        raise ValueError("Delta must differ from immutable base")
    root.mkdir(parents=True, exist_ok=True)

    def gate(_):
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if shutil.disk_usage(root).free < crawl.RESERVE + DRIFT_GUARD + NEXT_DOCUMENT:
            raise RuntimeError("STORAGE_RESERVE_STOP")
        if used + NEXT_DOCUMENT > MAX_DELTA:
            raise RuntimeError("STORAGE_CAP_STOP")

    gate(root)
    with read_base(base) as old:
        rows = old.execute(
            "SELECT * FROM municipalities WHERE status='RESOURCE_DEFERRED'"
        ).fetchall()
    if not db.exists():
        registry = root / "registry.jsonl"
        registry.write_text("".join(r["record"] + "\n" for r in rows))
        crawl.initialize(db, registry)
        with crawl.connect(db) as new:
            new.execute(
                "INSERT INTO metadata VALUES (?,?)",
                ("base_database_sha256", hashlib.sha256(base.read_bytes()).hexdigest()),
            )
            new.execute("INSERT INTO metadata VALUES (?,?)", ("base_database_path", str(base)))
    original_store = crawl.store_document

    def inherit(sha):
        with read_base(base) as old:
            doc = old.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()
            if not doc:
                return False
            path = Path(doc["raw_path"])
            raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                raise RuntimeError("SOURCE_INTEGRITY_STOP")
            units = old.execute("SELECT * FROM units WHERE sha256=?", (sha,)).fetchall()
        with crawl.connect(db) as new:
            new.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)", tuple(doc))
            new.executemany(
                "INSERT OR IGNORE INTO units VALUES (?,?,?,?,?,?,?)",
                [tuple(unit) for unit in units],
            )
        return True

    # Resume interrupted pages from their original acquisition, not a new download.
    with read_base(base) as old:
        cached = []
        for row in rows:
            cached.extend(
                old.execute(
                    "SELECT * FROM requests WHERE ags=? AND sha256 IS NOT NULL", (row["ags"],)
                ).fetchall()
            )
    with crawl.connect(db) as new:
        already = new.execute(
            "SELECT value FROM metadata WHERE key=?", ("inherited_request_rows",)
        ).fetchone()
    if already is None:
        for request in cached:
            inherit(request["sha256"])
            with crawl.connect(db) as new:
                new.execute(
                    "INSERT INTO requests VALUES (NULL,?,?,?,?,?,?,?,?,?,?)", tuple(request)[1:]
                )
                new.execute(
                    "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                    (
                        request["sha256"],
                        request["ags"],
                        request["final_url"],
                        "INHERITED_BASE_CANDIDATE",
                    ),
                )
        with crawl.connect(db) as new:
            new.execute(
                "INSERT INTO metadata VALUES (?,?)", ("inherited_request_rows", str(len(cached)))
            )

    def store(db_path, target, ags, url, media_type, body, authority):
        gate(target)
        sha = hashlib.sha256(body).hexdigest()
        if inherit(sha):
            with crawl.connect(db_path) as new:
                new.execute(
                    "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                    (sha, ags, url, "INHERITED_BASE_BYTE_IDENTITY"),
                )
            return sha
        return original_store(db_path, target, ags, url, media_type, body, authority)

    def bounded(parser):
        def parse(*args):
            parsed = parser(*args)
            if len(parsed.text) > 3 * 1024**2 or len(parsed.segments) > 10000:
                with (root / "parser-deferrals.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps({"url": args[1], "reason": "TEXT_OUTPUT_BUDGET"}) + "\n"
                    )
                return ParseResult("", [], [], {"deferred": "TEXT_OUTPUT_BUDGET"})
            return parsed

        return parse

    crawl.resource_gate = gate
    crawl.store_document = store
    crawl.parse_html = bounded(crawl.parse_html)
    crawl.parse_pdf = bounded(crawl.parse_pdf)
    crawl.MAX_BODY = 2 * 1024**2
    crawl.wave(db, root, max_docs=4, workers=1)
    with crawl.connect(db) as new:
        status = dict(new.execute("SELECT status,count(*) FROM municipalities GROUP BY status"))
        report = {
            "base_database": str(base),
            "delta_database": str(db),
            "statuses": status,
            "inherited_request_rows": len(cached),
            "raw_max_bytes": crawl.MAX_BODY,
            "generated_delta_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
            "free_bytes": shutil.disk_usage(root).free,
            "reserve_bytes": crawl.RESERVE,
            "base_unchanged": hashlib.sha256(base.read_bytes()).hexdigest()
            == new.execute(
                "SELECT value FROM metadata WHERE key=?", ("base_database_sha256",)
            ).fetchone()[0],
        }
    (root / "report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-db", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.base_db, args.db, args.root), indent=2))
