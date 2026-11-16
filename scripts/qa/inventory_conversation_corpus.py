"""Read-only union inventory of an explicit owner-local workspace manifest.

Does not import E1, retrieve passages, qualify coverage or export original text.
Missing/mismatched artifacts fail; never creates an implicit source database.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(manifest):
    manifest = manifest.resolve(strict=True)
    binding = json.loads(manifest.read_text())
    if binding["schema"] != "bavaria-workspace-manifest-v1":
        raise ValueError("Unsupported manifest schema")
    paths = {}
    receipts = {}
    for key, artifact in binding["artifacts"].items():
        path = Path(artifact["path"]).resolve(strict=True)
        if sha(path) != artifact["sha256"]:
            raise ValueError("Artifact hash mismatch: " + key)
        paths[key] = path
        receipts[key] = {"sha256": artifact["sha256"], "bytes": path.stat().st_size}
    result = {
        "manifest_sha256": sha(manifest),
        "snapshot_id": binding["snapshot_id"],
        "artifacts": receipts,
        "inventory_semantics": "DISTINCT_ID_UNION_NO_DISPOSITIONS",
    }
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA temp_store=MEMORY")
        names = []
        for key, path in paths.items():
            name = "db" + str(len(names))
            conn.execute("ATTACH DATABASE ? AS " + name, (path.as_uri() + "?mode=ro",))
            receipts[key]["quick_check"] = conn.execute(
                "PRAGMA " + name + ".quick_check"
            ).fetchone()[0]
            if key == "relations":
                conn.execute("DETACH DATABASE " + name)
                continue
            for table, columns in {
                "documents": {"sha256"},
                "units": {"unit_id", "sha256"},
                "associations": {"ags", "sha256"},
                "requests": {"ags"},
            }.items():
                present = {
                    r[1] for r in conn.execute("PRAGMA " + name + ".table_info(" + table + ")")
                }
                if not columns <= present:
                    raise ValueError("Source schema mismatch")
            names.append(name)
        if not names:
            raise ValueError("No explicit source ledgers")
        result["municipality_frame"] = conn.execute(
            "SELECT count(*) FROM db0.municipalities"
        ).fetchone()[0]
        for table, key in [("documents", "sha256"), ("units", "unit_id")]:
            query = " UNION ".join("SELECT " + key + " FROM " + n + "." + table for n in names)
            result[table + "_distinct"] = conn.execute(
                "SELECT count(*) FROM (" + query + ")"
            ).fetchone()[0]
        for dimension, query in [
            (
                "municipalities_with_text",
                "SELECT DISTINCT a.ags FROM {n}.associations a JOIN {n}.units u USING(sha256)",
            ),
            ("municipalities_with_request", "SELECT DISTINCT ags FROM {n}.requests"),
        ]:
            union = " UNION ".join(query.format(n=n) for n in names)
            result[dimension] = conn.execute("SELECT count(*) FROM (" + union + ")").fetchone()[0]
    if any(sha(paths[key]) != receipt["sha256"] for key, receipt in receipts.items()):
        raise ValueError("Source changed during inventory")
    result.update(
        human_reviewed="NOT_ESTABLISHED",
        claim_scope_qualified="NOT_ESTABLISHED",
        answerability_audited="NOT_EVALUATED",
        rights="NOT_CLEARED_BY_INVENTORY",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.manifest), indent=2))


if __name__ == "__main__":
    main()
