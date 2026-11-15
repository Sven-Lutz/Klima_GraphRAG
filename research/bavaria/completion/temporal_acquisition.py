# ruff: noqa: E501
"""Three named municipal reports for a justified temporal gap; no frontier crawl."""

import hashlib
import json
import shutil
import time
from pathlib import Path

import requests

from research.bavaria import acquisition as a

URLS = [
    "https://amberg.de/fileadmin/Klimaschutz/Klimaschutzbericht_2010-2022.pdf",
    "https://amberg.de/fileadmin/Klimaschutz/Klimaschutzbericht_2021_fuer_die_Stadt_Amberg.pdf",
    "https://amberg.de/fileadmin/Klimaschutz/Klimaschutzbericht_2020_fuer_die_Stadt_Amberg.pdf",
]


def run(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 15 * 1024**3 + 64 * 1024**2:
        raise RuntimeError("Reserve plus bounded temporal job")
    db = root / "acquisition.sqlite"
    baseline = Path("generated/bavaria/acquisition.sqlite")
    with a.connect(baseline) as conn:
        row = next(
            r
            for r in conn.execute("SELECT * FROM municipalities")
            if json.loads(r["record"])["name"] == "Amberg"
        )
    ags = row["ags"]
    registry = root / "registry.jsonl"
    if not db.exists():
        registry.write_text(row["record"] + "\n")
        a.initialize(db, registry)
    plan = root / "FREEZE.json"
    if not plan.exists():
        plan.write_text(
            json.dumps(
                {
                    "status": "FROZEN_BEFORE_TARGETED_REQUESTS",
                    "urls": URLS,
                    "purpose": "Original temporal reports missing from existing catalogue; distinguish municipal-property energy from territorial emissions.",
                    "maximum_documents": 3,
                    "max_body_bytes": a.MAX_BODY,
                    "total_output_cap_bytes": 32 * 1024**2,
                    "reserve_bytes": 15 * 1024**3,
                    "baseline_database": str(baseline),
                    "broad_crawl": False,
                },
                indent=2,
            )
            + "\n"
        )
    a.MAX_BYTES = 32 * 1024**2
    session = requests.Session()
    session.headers["User-Agent"] = a.UA
    policy, receipt = a.robots(session, URLS[0])
    delay = policy.crawl_delay(a.UA)
    if delay > 15:
        raise RuntimeError("POLITENESS_DEFER_GREATER_THAN_15_SECONDS")
    a.HOST_DELAY["amberg.de"] = max(1.5, delay)
    (root / "robots.txt").write_bytes(receipt[3])
    results = []
    for url in URLS:
        with a.connect(db) as conn:
            if conn.execute("select 1 from associations where url=?", (url,)).fetchone():
                results.append({"url": url, "status": "REUSED_COMPLETED_CHECKPOINT"})
                continue
        if not policy.can_fetch(a.UA, url):
            a.record_request(db, ags, url, error="ROBOTS_DISALLOW")
            results.append({"url": url, "status": "ROBOTS_DISALLOW"})
            continue
        cached = None
        for existing in [baseline, Path("generated/bavaria/continuation/delta/acquisition.sqlite")]:
            with a.connect(existing) as conn:
                hit = conn.execute("select * from associations where url=?", (url,)).fetchone()
                if hit:
                    cached = (existing, dict(hit))
                    break
        if cached:
            # Do not redownload known originals; this path remains an explicit reuse receipt.
            results.append(
                {
                    "url": url,
                    "status": "EXISTING_ARCHIVE_REFERENCE",
                    "database": str(cached[0]),
                    "sha256": cached[1]["sha256"],
                }
            )
            continue
        started = time.monotonic()
        try:
            if shutil.disk_usage(root).free < 15 * 1024**3 + 32 * 1024**2:
                raise RuntimeError("RESERVE")
            status, final, headers, body = a.fetch(session, url, {"amberg.de", "www.amberg.de"})
            if status != 200 or not body.startswith(b"%PDF"):
                raise ValueError("NOT_SUCCESSFUL_ORIGINAL_PDF:" + str(status))
            sha = hashlib.sha256(body).hexdigest()
            shared = None
            for existing in [
                baseline,
                Path("generated/bavaria/continuation/delta/acquisition.sqlite"),
            ]:
                with a.connect(existing) as conn:
                    found = conn.execute(
                        "SELECT * FROM documents WHERE sha256=?", (sha,)
                    ).fetchone()
                    if found:
                        shared = (existing, dict(found))
                        break
            if shared:
                with a.connect(shared[0]) as old, a.connect(db) as new:
                    new.execute(
                        "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
                        tuple(
                            shared[1][k]
                            for k in [
                                "sha256",
                                "raw_path",
                                "raw_bytes",
                                "compressed_bytes",
                                "parse_status",
                            ]
                        ),
                    )
                    for unit in old.execute("SELECT * FROM units WHERE sha256=?", (sha,)):
                        new.execute(
                            "INSERT OR IGNORE INTO units VALUES (?,?,?,?,?,?,?)", tuple(unit)
                        )
                    new.execute(
                        "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                        (sha, ags, final, "OFFICIAL_HOST_REFERENCED_REPORT"),
                    )
            else:
                a.store_document(
                    db,
                    root,
                    ags,
                    final,
                    headers.get("Content-Type", "application/pdf"),
                    body,
                    "OFFICIAL_HOST_REFERENCED_REPORT",
                )
            a.record_request(
                db,
                ags,
                url,
                final,
                status,
                None,
                sha,
                len(body),
                headers.get("Content-Type"),
                started,
            )
            results.append(
                {
                    "url": url,
                    "status": "STORED_ORIGINAL",
                    "sha256": sha,
                    "raw_bytes": len(body),
                    "seconds": time.monotonic() - started,
                }
            )
        except Exception as exc:
            a.record_request(
                db, ags, url, error=type(exc).__name__ + ":" + str(exc), started=started
            )
            results.append(
                {"url": url, "status": "FAILED", "error": type(exc).__name__ + ":" + str(exc)}
            )
        (root / "checkpoint.json").write_text(json.dumps(results, indent=2) + "\n")
    with a.connect(db) as conn:
        conn.execute(
            "UPDATE municipalities SET status='TARGETED_REQUESTS_DISPOSED',checked_at=? WHERE ags=?",
            (a.now(), ags),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (root / "report.json").write_text(
        json.dumps(
            {
                "requests": results,
                "free_bytes": shutil.disk_usage(root).free,
                "no_population_status_changed": True,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run("generated/bavaria/completion/temporal")
