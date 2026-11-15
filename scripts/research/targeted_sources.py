"""Append-only, explicitly scoped source follow-up; no recursive crawl or baseline writes."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import requests

from crawler.web.bavaria import create_bavaria_service
from research.bavaria.acquisition import (
    HOST_DELAY,
    UA,
    fetch,
    initialize,
    record_request,
    resource_gate,
    robots,
    store_document,
)
from research.bavaria.workspace.core import make_manifest, write_guard


def acquire(parent_manifest, config, output):
    """All URLs explicit and at most eight; retries require a fresh evidence directory."""
    parent_manifest, output = Path(parent_manifest).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Preserve previous acquisitions; select a fresh output directory")
    config = json.loads(Path(config).read_text())
    urls = config["sources"]
    if not 1 <= len(urls) <= 8:
        raise ValueError("Targeted acquisition requires1..8explicit URLs")
    hosts = set(config["allowed_hosts"])
    for row in urls:
        parsed = urlsplit(row["url"])
        if (
            parsed.scheme != "https"
            or parsed.hostname not in hosts
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Only explicit HTTPS host allowlist targets are permitted")
        if not row.get("official_source_basis"):
            raise ValueError("Record official-source selection basis")
    service = create_bavaria_service(parent_manifest)
    if any(row["ags"] not in service.workspace.municipalities for row in urls):
        raise ValueError("Unknown municipality assignment")
    write_guard(output, 64 * 1024**2)
    output.mkdir(parents=True)
    registry = output / "registry.jsonl"
    registry.write_text(
        "".join(
            json.dumps(dict(r, ags=a), ensure_ascii=False) + "\n"
            for a, r in sorted(service.workspace.municipalities.items())
        )
    )
    db = output / "acquisition.sqlite"
    initialize(db, registry)
    report = dict(
        schema="targeted-source-followup-v1",
        parent_snapshot=service.database_snapshot_id,
        human_gold=False,
        external_distribution="BLOCKED",
        sources=[],
        requests_limit=len(urls),
        recursive_crawl=False,
    )
    session = requests.Session()
    session.headers["User-Agent"] = UA
    policies = {}
    for row in urls:
        started = time.monotonic()
        url, ags = row["url"], row["ags"]
        item = dict(row, status="pending")
        try:
            resource_gate(output)
            host = urlsplit(url).hostname
            if host not in policies:
                policy, receipt = robots(session, url)
                policies[host] = policy
                HOST_DELAY[host] = max(1.5, policy.crawl_delay(UA))
                (output / ("robots-" + host + ".txt")).write_bytes(receipt[3])
            if not policies[host].can_fetch(UA, url):
                raise ValueError("ROBOTS_DISALLOW")
            status, final, headers, body = fetch(session, url, hosts)
            if status != 200:
                raise ValueError("HTTP_STATUS_" + str(status))
            media = headers.get("Content-Type", "").split(";")[0]
            if media not in {"text/html", "application/pdf"}:
                raise ValueError("Unsupported source media type")
            sha = store_document(
                db, output, ags, final, media, body, "OFFICIAL_DOMAIN_CANDIDATE_NOT_HUMAN_REVIEWED"
            )
            record_request(
                db,
                ags,
                url,
                final,
                status,
                digest=sha,
                size=len(body),
                media=media,
                started=started,
            )
            item.update(status="succeeded", source_sha256=sha, final_url=final, bytes=len(body))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            item.update(status="failed", error_class=type(exc).__name__, reason=str(exc)[:200])
            record_request(db, ags, url, error=item["reason"], started=started)
        report["sources"].append(item)
        (output / "ACQUISITION.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
    gc.collect()  # Legacy acquisition contexts commit but leave connection cycles.
    with closing(sqlite3.connect(db)) as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
        report["units"] = c.execute("SELECT count(*) FROM units").fetchone()[0]
    parent = json.loads(parent_manifest.read_text())
    if any(r["status"] == "succeeded" for r in report["sources"]):
        binding = make_manifest(
            parent["artifacts"]["ledger"]["path"],
            parent["artifacts"].get("relations", {}).get("path"),
            output / "workspace.json",
            source_databases=[*parent.get("source_ledger_order", []), str(db)],
            disposition_db=parent.get("disposition_ledger"),
        )
        report["snapshot_id"] = binding["snapshot_id"]
    report["ledger_sha256"] = hashlib.sha256(db.read_bytes()).hexdigest()
    (output / "ACQUISITION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    service.close()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent-manifest", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(acquire(a.parent_manifest, a.config, a.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
