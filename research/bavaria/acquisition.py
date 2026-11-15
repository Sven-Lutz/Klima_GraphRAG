"""Bounded, checkpointed acquisition using existing parsers and relevance scoring.

Separate version ledger because Storage.store_raw upserts by canonical URL and
deletes old segments on changed bytes. Existing SHA-256 storage is acknowledged;
this extension preserves version-specific evidence plus population coverage.
"""

import concurrent.futures
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from crawler.core.engine import Engine
from crawler.core.models import FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf

from .robots import Policy

UA = "KlimaResearch/1.0"
TOPIC = re.compile(
    r"klima|energie|wärme|waerme|solar|photovolta|hochwasser|hitz|"
    r"entsiegel|begrün|radverkehr|mobilität|sanierung|förderprogramm",
    re.I,
)
LOCK = threading.Lock()
HOST_LOCKS: dict[str, threading.Lock] = {}
HOST_LAST: dict[str, float] = {}
HOST_DELAY: dict[str, float] = {}
INTEGRITY_STOP = threading.Event()
RESERVE = 15 * 1024**3
MAX_BYTES = 1024**3
MAX_BODY = 6 * 1024**2


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(path, *, create=False):
    path = Path(path).resolve()
    if not create and not path.is_file():
        raise FileNotFoundError(f"Explicit acquisition database missing: {path}")
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize(db, registry):
    if Path(db).exists():
        raise ValueError("Refuse to overwrite an existing acquisition database")
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    with connect(db, create=True) as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE municipalities(ags TEXT PRIMARY KEY,record TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'NOT_CHECKED',checked_at TEXT,error TEXT);
        CREATE TABLE requests(id INTEGER PRIMARY KEY,ags TEXT REFERENCES municipalities(ags),
          url TEXT NOT NULL,final_url TEXT,status INTEGER,error TEXT,sha256 TEXT,
          bytes INTEGER,media_type TEXT,at TEXT NOT NULL,seconds REAL);
        CREATE INDEX requests_url ON requests(url);
        CREATE TABLE documents(sha256 TEXT PRIMARY KEY,raw_path TEXT NOT NULL,
          raw_bytes INTEGER NOT NULL,compressed_bytes INTEGER NOT NULL,parse_status TEXT);
        CREATE TABLE associations(sha256 TEXT REFERENCES documents(sha256),
          ags TEXT REFERENCES municipalities(ags),url TEXT,authority TEXT,
          PRIMARY KEY(sha256,ags,url));
        CREATE TABLE units(unit_id TEXT PRIMARY KEY,sha256 TEXT REFERENCES documents(sha256),
          ordinal INTEGER,text TEXT NOT NULL,heading TEXT,page_ref TEXT,kind TEXT);
        CREATE INDEX units_source ON units(sha256);
        CREATE TABLE links(ags TEXT,url TEXT,anchor TEXT,score INTEGER,
          PRIMARY KEY(ags,url));
        """)
        conn.execute("INSERT INTO metadata VALUES (?,?)", ("schema", "bavaria-acquisition-v1"))
        conn.execute(
            "INSERT INTO metadata VALUES (?,?)",
            ("registry_sha256", hashlib.sha256(Path(registry).read_bytes()).hexdigest()),
        )
        for line in Path(registry).read_text().splitlines():
            record = json.loads(line)
            conn.execute(
                "INSERT INTO municipalities(ags,record) VALUES (?,?)", (record["ags"], line)
            )


def pace(url):
    host = urlsplit(url).netloc.lower()
    with LOCK:
        lock = HOST_LOCKS.setdefault(host, threading.Lock())
    with lock:
        delay = HOST_DELAY.get(host, 1.5) - (time.monotonic() - HOST_LAST.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        HOST_LAST[host] = time.monotonic()


def resource_gate(root):
    # Bound all generated levels, not just payloads. Preserve originals on stop.
    if shutil.disk_usage(root).free < RESERVE:
        raise RuntimeError("STORAGE_RESERVE_STOP")
    total = sum(p.stat().st_size for p in Path(root).rglob("*") if p.is_file())
    if total >= MAX_BYTES:
        raise RuntimeError("STORAGE_CAP_STOP")


def fetch(session, url, allowed_hosts=None):
    """Stream a bounded body; never auto-follow unverified cross-host redirects."""
    for _ in range(6):
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("UNSUPPORTED_SCHEME")
        pace(url)
        with session.get(url, timeout=(5, 12), stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                target = urljoin(url, response.headers.get("Location", ""))
                if allowed_hosts is not None and urlsplit(target).hostname not in allowed_hosts:
                    raise ValueError("UNVERIFIED_REDIRECT_DOMAIN")
                if allowed_hosts is not None and urlsplit(target).netloc != urlsplit(url).netloc:
                    policy, _ = robots(session, target)
                    if not policy.can_fetch(UA, target):
                        raise ValueError("ROBOTS_DISALLOW_REDIRECT")
                url = target
                continue
            if response.status_code in (429, 503):
                # No eager retry: retain Retry-After and defer this municipality.
                raise ValueError(
                    "DEFER_RETRY_AFTER:" + response.headers.get("Retry-After", "unknown")
                )
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BODY:
                    raise ValueError("BODY_LIMIT_DEFERRED")
                chunks.append(chunk)
            return response.status_code, response.url, dict(response.headers), b"".join(chunks)
    raise ValueError("REDIRECT_LIMIT")


def robots(session, url):
    base = urlsplit(url)
    target = f"{base.scheme}://{base.netloc}/robots.txt"
    status, final, headers, body = fetch(session, target)
    if 200 <= status < 300:
        parser = Policy(body.decode("utf-8", errors="replace"))
    elif status in (404, 410):
        parser = Policy("")
    else:
        # Conservative for 401/403 and server failures; do not claim policy disallow.
        raise ValueError("ROBOTS_UNAVAILABLE:" + str(status))
    return parser, (status, final, headers, body)


def store_document(db, root, ags, url, media_type, body, authority):
    digest = hashlib.sha256(body).hexdigest()
    with LOCK:
        with connect(db) as conn:
            exists = conn.execute("SELECT 1 FROM documents WHERE sha256=?", (digest,)).fetchone()
        if not exists:
            resource_gate(root)
            path = Path(root) / "raw" / digest[:2] / (digest + ".gz")
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = gzip.compress(body, mtime=0)
            path.write_bytes(payload)
            fr = FetchResult(url, 200, media_type, body, {})
            parsed = parse_pdf(fr, url) if body.startswith(b"%PDF") else parse_html(fr, url)
            with connect(db) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
                    (
                        digest,
                        str(path.resolve()),
                        len(body),
                        len(payload),
                        "TEXT_EXTRACTED" if parsed.segments else "EMPTY_OR_PARSE_FAILED",
                    ),
                )
                for seg in parsed.segments:
                    uid = hashlib.sha256(
                        f"{digest}:{seg.order_index}:{seg.text}".encode()
                    ).hexdigest()
                    conn.execute(
                        "INSERT OR IGNORE INTO units VALUES (?,?,?,?,?,?,?)",
                        (
                            uid,
                            digest,
                            seg.order_index,
                            seg.text,
                            seg.heading_context,
                            seg.page_ref,
                            seg.segment_type,
                        ),
                    )
        with connect(db) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)", (digest, ags, url, authority)
            )
    return digest


def record_request(
    db, ags, url, final=None, status=None, error=None, digest=None, size=0, media=None, started=None
):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO requests VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
            (
                ags,
                url,
                final,
                status,
                error,
                digest,
                size,
                media,
                now(),
                time.monotonic() - started if started else None,
            ),
        )


def crawl_one(db, root, row, max_docs):
    record = json.loads(row["record"])
    ags = record["ags"]
    homepage = record["homepage_candidate"]
    status, error = "NOT_UNAMBIGUOUSLY_ASSIGNED", None
    if not homepage:
        error = "MISSING_DOMAIN_CANDIDATE"
    else:
        session = requests.Session()
        session.headers["User-Agent"] = UA
        host = urlsplit(homepage).hostname
        hosts = {host, host[4:] if host.startswith("www.") else "www." + host}
        policies = {}
        queue = [(homepage, "homepage")]
        url = homepage
        relevant = 0
        try:
            for index in range(max_docs):
                if INTEGRITY_STOP.is_set():
                    raise RuntimeError("SOURCE_INTEGRITY_STOP")
                if not queue:
                    break
                resource_gate(root)
                url, anchor = queue.pop(0)
                started = time.monotonic()
                with connect(db) as conn:
                    cached = conn.execute(
                        "SELECT * FROM requests WHERE url=? AND sha256 IS NOT NULL "
                        "ORDER BY id DESC LIMIT 1",
                        (url,),
                    ).fetchone()
                if cached:
                    digest = cached["sha256"]
                    with connect(db) as conn:
                        original = conn.execute(
                            "SELECT raw_path FROM documents WHERE sha256=?", (digest,)
                        ).fetchone()
                        conn.execute(
                            "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
                            (digest, ags, cached["final_url"], "CANDIDATE_HOST_ONLY"),
                        )
                    path = Path(original["raw_path"])
                    body = (
                        gzip.decompress(path.read_bytes())
                        if path.suffix == ".gz"
                        else path.read_bytes()
                    )
                    if hashlib.sha256(body).hexdigest() != digest:
                        raise RuntimeError("SOURCE_INTEGRITY_STOP")
                    if body.startswith(b"%PDF"):
                        relevant += bool(TOPIC.search(url))
                    else:
                        parsed = parse_html(
                            FetchResult(cached["final_url"], 200, cached["media_type"], body, {}),
                            cached["final_url"],
                        )
                        relevant += bool(TOPIC.search(parsed.text))
                        queued = {u for u, _ in queue} | {url}
                        links = [
                            (u, a or "")
                            for u, a in parsed.out_links
                            if urlsplit(u).hostname in hosts
                            and u not in queued
                            and TOPIC.search(u + " " + (a or ""))
                        ]
                        links.sort(key=lambda x: (-Engine.score(None, x[0], x[1]), x[0]))
                        queue.extend(links)
                    status = (
                        "RELEVANT_CANDIDATES_FOUND" if relevant else "CHECKED_NO_RELEVANT_FOUND"
                    )
                    continue
                origin = (urlsplit(url).scheme, urlsplit(url).netloc)
                if origin not in policies:
                    parser, response = robots(session, url)
                    policies[origin] = parser
                    code, final, headers, body = response
                    record_request(
                        db,
                        ags,
                        f"{origin[0]}://{origin[1]}/robots.txt",
                        final,
                        code,
                        size=len(body),
                        media="text/plain",
                        started=started,
                    )
                if not policies[origin].can_fetch(UA, url):
                    record_request(db, ags, url, error="ROBOTS_DISALLOW", started=started)
                    status = "ACCESS_RULES_BLOCKED"
                    continue
                delay = policies[origin].crawl_delay(UA)
                if delay and delay > 30:
                    raise ValueError("CRAWL_DELAY_DEFERRED")
                with LOCK:
                    HOST_DELAY[origin[1].lower()] = max(
                        HOST_DELAY.get(origin[1].lower(), 1.5), delay or 1.5
                    )
                code, final, headers, body = fetch(session, url, hosts)
                media = headers.get("Content-Type", "").lower()
                digest = None
                if code == 200 and ("html" in media or "pdf" in media or body.startswith(b"%PDF")):
                    digest = store_document(
                        db, root, ags, final, media, body, "CANDIDATE_HOST_ONLY"
                    )
                    if "html" in media:
                        parsed = parse_html(FetchResult(final, code, media, body, headers), final)
                        relevant += bool(TOPIC.search(parsed.text))
                        links = [
                            (u, a or "")
                            for u, a in parsed.out_links
                            if urlsplit(u).hostname in hosts and TOPIC.search(u + " " + (a or ""))
                        ]
                        # Reuse the existing crawler's priority function, no new relevance model.
                        scored = [(u, a, Engine.score(None, u, a)) for u, a in links]
                        scored.sort(key=lambda x: (-x[2], x[0]))
                        with connect(db) as conn:
                            conn.executemany(
                                "INSERT OR IGNORE INTO links VALUES (?,?,?,?)",
                                [(ags, u, a, s) for u, a, s in scored],
                            )
                        queued = {u for u, _ in queue} | {url}
                        queue.extend((u, a) for u, a, _ in scored if u not in queued)
                    else:
                        relevant += 1
                    status = (
                        "RELEVANT_CANDIDATES_FOUND" if relevant else "CHECKED_NO_RELEVANT_FOUND"
                    )
                elif code in (401, 403):
                    status = "ACCESS_RULES_BLOCKED"
                else:
                    status = "TECHNICALLY_UNREACHABLE" if index == 0 else status
                record_request(
                    db,
                    ags,
                    url,
                    final,
                    code,
                    digest=digest,
                    size=len(body),
                    media=media,
                    started=started,
                )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            error = str(exc)[:180]
            if error.startswith("SOURCE_INTEGRITY_STOP"):
                INTEGRITY_STOP.set()
                record_request(db, ags, url, error="SOURCE_INTEGRITY_STOP")
                raise
            # Exception strings may include URLs, but never credentials: seed URLs are public.
            status = (
                "RESOURCE_DEFERRED"
                if error.startswith("STORAGE_")
                else (
                    "ROBOTS_UNAVAILABLE"
                    if error.startswith("ROBOTS_")
                    else "TECHNICALLY_UNREACHABLE"
                )
            )
            record_request(db, ags, url, error=error)
        finally:
            session.close()
    with connect(db) as conn:
        conn.execute(
            "UPDATE municipalities SET status=?,checked_at=?,error=? WHERE ags=?",
            (status, now(), error, ags),
        )
    return ags, status


def wave(db, root, limit=0, max_docs=4, workers=8):
    resource_gate(root)
    with connect(db) as conn:
        rows = conn.execute("SELECT * FROM municipalities WHERE status='NOT_CHECKED'").fetchall()
    # Deterministic round-robin over region × size strata, no convenience-city first wave.
    groups = {}
    for row in rows:
        r = json.loads(row["record"])
        size = 0 if r["population"] < 5000 else 1 if r["population"] < 20000 else 2
        groups.setdefault((r["region"], size), []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: hashlib.sha256(r["ags"].encode()).hexdigest())
    ordered = []
    while any(groups.values()):
        for key in sorted(groups):
            if groups[key]:
                ordered.append(groups[key].pop())
    if limit:
        ordered = ordered[:limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(crawl_one, db, root, row, max_docs) for row in ordered]
        for index, job in enumerate(concurrent.futures.as_completed(jobs), 1):
            print(
                json.dumps({"completed": index, "total": len(jobs), "result": job.result()}),
                flush=True,
            )
