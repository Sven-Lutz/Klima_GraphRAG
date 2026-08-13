from __future__ import annotations

import argparse

from crawler.core.database import resolve_runtime_database
from crawler.core.seeds import load_seeds_from_sqlite, upsert_seed_jobs


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db",
        default=None,
        help="Crawl SQLite database; overrides KLIMA_DB_PATH for this invocation",
    )
    args = ap.parse_args(argv)

    seeds, _ = load_seeds_from_sqlite()
    database = resolve_runtime_database(args.db)
    n = upsert_seed_jobs(seeds, crawl_db_path=database.path)
    print(f"Upserted {n} seed_jobs into {database.path} (database_id={database.database_id})")


if __name__ == "__main__":
    main()
