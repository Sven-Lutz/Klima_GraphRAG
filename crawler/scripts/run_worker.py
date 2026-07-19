# crawler/scripts/run_worker.py
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
import time
from datetime import timedelta

from crawler.core.engine import Engine, EngineLimits
from crawler.core.seed_jobs import (
    DEFAULT_CRAWL_DB_PATH,
    claim_next_job,
    default_worker_id,
    heartbeat_job,
    mark_done,
    mark_failed,
)
from crawler.core.seeds import load_seeds_from_sqlite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("worker.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_SECONDS = 30.0

KLIMA_KEYWORDS = {
    "positive": [
        "klima", "klimaschutz", "energie", "waermeplanung", "wärmeplanung",
        "co2", "thg", "solar", "photovoltaik", "pv", "wind",
        "förder", "foerder", "zuschuss", "mittel", "haushalt", "finanz",
        "investition", "nki", "kfw", "eu-mittel", "efre", "beschluss", "vorlage"
    ],
    "negative": [
        "impressum", "datenschutz", "barrierefreiheit", "leichte-sprache",
        "login", "karriere", "stellenangebot", "jubiläum", "veranstaltung",
        "tourismus", "museum", "sport"
    ]
}

KLIMA_LIMITS = EngineLimits(
    max_depth=12,
    max_pages_per_muni=25000,
    max_file_size_mb=200  # Auf 200 MB angehoben für große Pläne
)

# Additional seed paths — enqueued alongside each homepage to discover
# relevant climate/energy pages that may not be well-linked
SEED_ADDITIONAL_PATHS = [
    "/klima", "/klimaschutz", "/energie", "/waermeplanung",
    "/umwelt", "/mobilitaet", "/verkehr", "/bauen",
    "/foerderung", "/foerderprogramme", "/haushalt",
    "/ratsinfo", "/bekanntmachungen",
]

def _heartbeat_loop(
    db_path: str, municipality_id: str, worker_id: str, stop: threading.Event
) -> None:
    con = sqlite3.connect(db_path, timeout=60.0, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout=60000;")
        con.execute("PRAGMA journal_mode=WAL;")
        while not stop.wait(HEARTBEAT_EVERY_SECONDS):
            try:
                heartbeat_job(con, municipality_id, worker_id)
            except sqlite3.OperationalError:
                pass
            except Exception:
                pass
    finally:
        con.close()

def _worker_loop(
    db_path: str,
    worker_id: str,
    allowed: dict,
    stale_after: int,
    limit_counter: dict,
    limit_lock: threading.Lock,
    shutdown: threading.Event,
) -> int:
    """Ein Worker-Thread: claimt Jobs und crawlt Kommunen, bis keine mehr da sind.

    Jeder Thread hat seine eigene Engine (eigene Storage-/SQLite-Verbindung,
    eigener Scheduler). Parallelität entsteht ÜBER Kommunen hinweg — jede
    Kommune ist ein anderer Host, die Per-Host-Politeness bleibt gewahrt.
    """
    con = sqlite3.connect(db_path, timeout=60.0, isolation_level=None)
    con.execute("PRAGMA busy_timeout=60000;")
    con.execute("PRAGMA journal_mode=WAL;")

    engine = Engine(
        keywords=KLIMA_KEYWORDS,
        limits=KLIMA_LIMITS,
        allowed_domains_by_muni=allowed,
        worker_id=worker_id,
        seed_additional_paths=SEED_ADDITIONAL_PATHS,
    )

    processed = 0
    try:
        while not shutdown.is_set():
            with limit_lock:
                if (limit_counter["limit"] is not None
                        and limit_counter["claimed"] >= limit_counter["limit"]):
                    break
                job = claim_next_job(con, worker_id=worker_id, stale_after_seconds=stale_after)
                if job is not None:
                    limit_counter["claimed"] += 1

            if job is None:
                break

            stop = threading.Event()
            hb = threading.Thread(
                target=_heartbeat_loop,
                args=(db_path, job.municipality_id, worker_id, stop),
                daemon=True,
            )
            hb.start()

            muni_start_time = time.time()
            try:
                logger.info(f"▶️ Bearbeite Gemeinde {job.municipality_id} ({job.seed_url})")
                engine.run([(job.municipality_id, job.seed_url)])

                # Resolve target_document_id in document_links so Neo4j LINKS_TO
                # edges can be exported (otherwise target_document_id stays NULL)
                resolved = engine.storage.resolve_document_links()
                if resolved:
                    logger.info(f"🔗 {resolved} document links resolved for {job.municipality_id}")

                mark_done(con, job.municipality_id, worker_id)
                duration = timedelta(seconds=int(time.time() - muni_start_time))
                logger.info(f"✅ Gemeinde {job.municipality_id} fertiggestellt. Dauer: {duration}.")

            except Exception as e:
                mark_failed(con, job.municipality_id, worker_id, str(e))
                logger.error(f"❌ Fehler bei {job.municipality_id}: {e}", exc_info=True)

            finally:
                stop.set()
                hb.join(timeout=2.0)
                processed += 1
    finally:
        con.close()
    return processed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_CRAWL_DB_PATH))
    ap.add_argument("--stale-after", type=int, default=15 * 60)
    ap.add_argument("--limit", type=int, default=None, help="Anzahl der zu bearbeitenden Kommunen.")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallele Kommunen (je 1 Thread; Politeness pro Host bleibt erhalten)")
    args = ap.parse_args()

    _, allowed = load_seeds_from_sqlite()

    start_time = time.time()
    shutdown = threading.Event()
    limit_lock = threading.Lock()
    limit_counter = {"limit": args.limit, "claimed": 0}

    logger.info(f"🚀 Starte Industrial-Worker (High-Recall Mode, workers={args.workers})")
    if args.limit:
        logger.info(f"🎯 Ziel-Limit: {args.limit} Kommunen")

    jobs_processed = 0
    try:
        if args.workers <= 1:
            jobs_processed = _worker_loop(
                args.db, default_worker_id(), allowed,
                args.stale_after, limit_counter, limit_lock, shutdown,
            )
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [
                    pool.submit(
                        _worker_loop,
                        args.db, f"{default_worker_id()}#{i}", allowed,
                        args.stale_after, limit_counter, limit_lock, shutdown,
                    )
                    for i in range(args.workers)
                ]
                jobs_processed = sum(f.result() for f in futures)
    except KeyboardInterrupt:
        logger.warning("⚠️ Strg+C gedrückt! Worker werden gestoppt…")
        shutdown.set()
        sys.exit(0)
    finally:
        total_time = timedelta(seconds=int(time.time() - start_time))
        logger.info(f"🏁 Fertig: {jobs_processed} Kommunen in {total_time} verarbeitet.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
