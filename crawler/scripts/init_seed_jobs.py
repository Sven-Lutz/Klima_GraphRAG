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

    ap.add_argument(
        "--ags",
        default=None,
        help="Comma-separated AGS municipality IDs to initialize",
    )

    args = ap.parse_args(argv)

    seeds, _ = load_seeds_from_sqlite()

    if args.ags:
        requested_ags = {
            ags.strip()
            for ags in args.ags.split(",")
            if ags.strip()
        }

        seeds = [
            (municipality_id, seed_url)
            for municipality_id, seed_url in seeds
            if municipality_id in requested_ags
        ]

        found_ags = {municipality_id for municipality_id, _ in seeds}
        missing_ags = requested_ags - found_ags

        if missing_ags:
            ap.error(
                "AGS not found or missing homepage: "
                + ", ".join(sorted(missing_ags))
            )

        print(f"Selected {len(seeds)} municipalities via --ags")

    database = resolve_runtime_database(args.db)

    n = upsert_seed_jobs(
        seeds,
        crawl_db_path=database.path,
    )

    print(
        f"Upserted {n} seed_jobs into {database.path} "
        f"(database_id={database.database_id})"
    )


if __name__ == "__main__":
    main()