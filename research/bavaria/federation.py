# ruff: noqa: E501
"""Read-only federation of explicit immutable acquisition ledgers, without copying originals."""

import json
import sqlite3
from pathlib import Path

TABLE_KEYS = {"documents": "sha256", "units": "unit_id"}


def source_connection(primary, additional=()):
    paths = [
        Path(primary).resolve(strict=True),
        *[Path(p).resolve(strict=True) for p in additional],
    ]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate explicit ledger path")
    for path in paths:
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("Close/checkpoint source writer before federation")
    conn = sqlite3.connect(":memory:", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for i, path in enumerate(paths):
            conn.execute(f"ATTACH DATABASE ? AS s{i}", (path.as_uri() + "?mode=ro",))
            schema = conn.execute(f"SELECT value FROM s{i}.metadata WHERE key='schema'").fetchone()
            if not schema or schema[0] != "bavaria-acquisition-v1":
                raise ValueError("Unsupported source ledger schema")
            if i:
                # Official identity must agree; acquisition status/check time may differ.
                for r in conn.execute(f"SELECT ags,record FROM s{i}.municipalities"):
                    base = conn.execute(
                        "SELECT record FROM s0.municipalities WHERE ags=?", (r["ags"],)
                    ).fetchone()
                    if not base:
                        raise ValueError("Extra ledger AGS outside authoritative frame")
                    a, b = json.loads(base[0]), json.loads(r["record"])
                    if a["name"] != b["name"] or a.get("official_name") != b.get("official_name"):
                        raise ValueError("Inconsistent official municipality identity")
                # Compare every prior ledger, not only the authoritative first one.
                for previous in range(i):
                    if conn.execute(
                        f"SELECT 1 FROM s{i}.units n JOIN s{previous}.units o USING(unit_id) "
                        "WHERE n.sha256!=o.sha256 OR n.text!=o.text LIMIT 1"
                    ).fetchone():
                        raise ValueError("Conflicting stable evidence identity")
        for table in ["metadata", "municipalities"]:
            conn.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM s0.{table}")
        for table, key in TABLE_KEYS.items():
            fragments = []
            for i in range(len(paths)):
                condition = " AND ".join(
                    f"NOT EXISTS (SELECT 1 FROM s{j}.{table} old WHERE old.{key}=n.{key})"
                    for j in range(i)
                )
                fragments.append(
                    f"SELECT n.* FROM s{i}.{table} n" + (" WHERE " + condition if condition else "")
                )
            conn.execute(f"CREATE TEMP VIEW {table} AS " + " UNION ALL ".join(fragments))
        conn.execute(
            "CREATE TEMP VIEW associations AS "
            + " UNION ".join(f"SELECT * FROM s{i}.associations" for i in range(len(paths)))
        )
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception:
        conn.close()
        raise
