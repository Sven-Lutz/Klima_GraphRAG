"""Small rights-approved portable subset; existing ledger/sidecar schemas are retained."""

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crawler.web.bavaria import create_bavaria_service
from research.bavaria.federation import source_connection
from research.bavaria.workspace.core import file_hash, identity, read_only


def valid_rights(rights, *, technical=False):
    sources = rights.get("sources", [])
    presentation = rights.get("source_presentation", "archived")
    if presentation == "minimal_external_preview":
        from crawler.web.minimal_policy import validate_minimal_policy

        return validate_minimal_policy(rights)
    if presentation not in {"archived", "linked_originals"}:
        raise ValueError("Unknown source presentation")
    if (
        rights.get("schema") != "klima-pilot-rights-v1"
        or rights.get("release_status") != "CLOSED_PILOT_APPROVED"
        or not rights.get("decision_by")
        or not rights.get("basis")
        or rights.get("technical_test_only", False)
        and not technical
        or datetime.fromisoformat(rights["expires_at"]) <= datetime.now(timezone.utc)
        or not 1 <= len(sources) <= 32
    ):
        raise ValueError("Explicit, unexpired rights approval for 1..32 sources required")
    selected = {}
    for row in sources:
        sha = row["sha256"]
        if (
            len(sha) != 64
            or any(c not in "0123456789abcdef" for c in sha)
            or sha in selected
            or row.get("source_metadata_display") != "APPROVED"
            or row.get("original_url_display") != "APPROVED"
            or row.get("excerpt_display") != "APPROVED"
            or (presentation == "archived" and row.get("archive_distribution") != "APPROVED")
            or (presentation == "linked_originals" and row.get("internal_processing") != "APPROVED")
            or not row.get("basis")
        ):
            raise ValueError("Duplicate, malformed or unapproved source")
        selected[sha] = row
    return selected


def export_bundle(manifest, rights_path, output, *, deployment_root="/snapshot", technical=False):
    rights = json.loads(Path(rights_path).read_text())
    selected = valid_rights(rights, technical=technical)  # before opening corpus or writing
    output = Path(output).resolve()
    if output.exists() or not Path(deployment_root).is_absolute():
        raise ValueError("Fresh output and absolute deployment root required")
    if shutil.disk_usage(output.parent).free < 15 * 1024**3 + 1024**3:
        raise RuntimeError("Reserve plus bounded 1 GiB export allowance unavailable")
    presentation = rights.get("source_presentation", "archived")
    service = create_bavaria_service(manifest)
    w = service.workspace
    if rights.get("parent_snapshot_id") != w.snapshot_id:
        raise ValueError("Rights tied to another snapshot")
    conn = source_connection(w.db, w.source_databases)
    source_rows = {}
    total = 0
    for sha in selected:
        row = conn.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()
        if not row or not 0 < row["raw_bytes"] <= 16 * 1024**2:
            raise ValueError("Selected original unavailable or outside per-source budget")
        if Path(row["raw_path"]).stat().st_size > row["raw_bytes"] + 64 * 1024:
            raise ValueError("Unexpected compressed source size")
        total += row["raw_bytes"]
        source_rows[sha] = dict(row)
    text_size = sum(
        conn.execute(
            "SELECT coalesce(sum(length(cast(text as blob))),0) FROM units WHERE sha256=?", (sha,)
        ).fetchone()[0]
        for sha in selected
    )
    if total > 128 * 1024**2 or text_size > 64 * 1024**2:
        raise ValueError("Pilot subset exceeds 128 MiB original / 64 MiB text budget")
    # Validate all original hashes before output creation; no unverified copies.
    for sha, row in source_rows.items():
        opener = gzip.open if row["raw_path"].endswith(".gz") else open
        with opener(row["raw_path"], "rb") as stream:
            data = stream.read(16 * 1024**2 + 1)
        if len(data) != row["raw_bytes"] or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("Original integrity failure")
    output.mkdir()
    (output / "originals").mkdir()
    target = Path(deployment_root)
    ledger = sqlite3.connect(output / "ledger.sqlite")
    ledger.row_factory = sqlite3.Row
    # Reuse exactly the active acquisition schemas, no annotation tables or Gold data.
    with read_only(w.db) as primary:
        for table in [
            "metadata",
            "municipalities",
            "requests",
            "documents",
            "associations",
            "units",
            "links",
        ]:
            sql = primary.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            ledger.execute(sql)
    ledger.execute("INSERT INTO metadata VALUES (?,?)", ("schema", "bavaria-acquisition-v1"))
    ledger.execute("INSERT INTO metadata VALUES (?,?)", ("parent_snapshot_id", w.snapshot_id))

    def insert(db, table, row):
        values = tuple(row)
        db.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in values)})", values)

    for sha, row in source_rows.items():
        insert(ledger, "metadata", ("pilot_verified_original:" + sha, sha))
        # Preserve citation acquisition time without transporting request URLs/logs.
        if sha in service.retrieved_at:
            insert(ledger, "metadata", ("pilot_retrieved_at:" + sha, service.retrieved_at[sha]))
        if presentation == "archived":
            suffix = ".gz" if row["raw_path"].endswith(".gz") else ".bin"
            name = sha + suffix
            shutil.copyfile(row["raw_path"], output / "originals" / name)
            copied = output / "originals" / name
            opener = gzip.open if suffix == ".gz" else open
            with opener(copied, "rb") as stream:
                checked = stream.read(16 * 1024**2 + 1)
            if len(checked) != row["raw_bytes"] or hashlib.sha256(checked).hexdigest() != sha:
                raise ValueError("Copied original failed identity verification")
            row["raw_path"] = str(target / "originals" / name)
        else:
            row["raw_path"] = "NOT_DISTRIBUTED_LINKED_ORIGINAL:" + sha
        insert(ledger, "documents", row.values())
        for table in ["associations", "units"]:
            for item in conn.execute(f"SELECT * FROM {table} WHERE sha256=?", (sha,)):
                insert(ledger, table, item)
    # Crawl requests are not needed by the read-only preview. Retain only frames
    # actually associated with selected sources, with minimal registry fields.
    frame_ids = {
        r[0]
        for sha in selected
        for r in conn.execute("SELECT ags FROM associations WHERE sha256=?", (sha,))
    }
    for row in conn.execute("SELECT * FROM municipalities"):
        if row["ags"] not in frame_ids:
            continue
        v = dict(row)
        record = json.loads(v["record"])
        v["record"] = json.dumps(
            {
                k: record[k]
                for k in ["ags", "name", "official_name", "region", "district"]
                if k in record
            },
            ensure_ascii=False,
        )
        v["status"] = "PILOT_SUBSET"
        v["checked_at"] = None
        v["error"] = None
        insert(ledger, "municipalities", v.values())
    ledger.commit()
    ledger.close()
    conn.close()
    relpath = None
    if w.relations_db:
        graph_approved = {
            sha
            for sha, r in selected.items()
            if r.get("derived_graph_display") in {"APPROVED", "SCOPED_OWN_FACTS"}
        }
        retained = []
        needed = set()
        allowed_relations = (
            {f["relation_id"] for f in rights["minimal_facts"]["facts"]}
            if presentation == "minimal_external_preview"
            else None
        )
        for r in w.relation_rows:
            if allowed_relations is not None and r["relation_id"] not in allowed_relations:
                continue
            ids = {r["evidence_id"], *r["record"].get("context_evidence_ids", [])}
            if all(w.evidence_rows[eid]["source_sha256"] in graph_approved for eid in ids):
                retained.append(r)
                needed.update(ids)
        if retained:
            relpath = output / "relations.sqlite"
            dest = sqlite3.connect(relpath)
            with read_only(w.relations_db) as src:
                for table in ["metadata", "sources", "evidence", "entities", "relations"]:
                    dest.execute(
                        src.execute(
                            "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                        ).fetchone()[0]
                    )
            insert(dest, "metadata", ("schema", "bavaria-relational-v1"))
            insert(dest, "metadata", ("parent_snapshot_id", w.snapshot_id))
            shas = {w.evidence_rows[eid]["source_sha256"] for eid in needed}
            for sha in sorted(shas):
                row = dict(w.source_rows[sha])
                row["raw_path"] = source_rows[sha]["raw_path"]
                insert(dest, "sources", row.values())
            for eid in sorted(needed):
                insert(dest, "evidence", w.evidence_rows[eid].values())
            entity_ids = {eid for r in retained for eid in [r["subject_id"], r["object_id"]]}
            for eid in sorted(entity_ids):
                insert(dest, "entities", w.entities[eid].values())
            for r in retained:
                record = dict(r["record"])
                record["raw_path"] = source_rows[record["source_sha256"]]["raw_path"]

                # Local rendering paths are not portable evidence locators.
                def portable(v):
                    if isinstance(v, dict):
                        return {k: portable(x) for k, x in v.items()}
                    if isinstance(v, list):
                        return [portable(x) for x in v]
                    if isinstance(v, str) and v.startswith(("/Users/", "/home/")):
                        return "LOCAL_RENDER_PATH_NOT_EXPORTED"
                    return v

                insert(
                    dest,
                    "relations",
                    (
                        r["relation_id"],
                        r["subject_id"],
                        r["predicate"],
                        r["object_id"],
                        r["evidence_id"],
                        json.dumps(portable(record), ensure_ascii=False),
                    ),
                )
            dest.commit()
            dest.close()
    payload = {
        "schema": "bavaria-workspace-manifest-v1",
        "artifacts": {
            "ledger": {
                "path": str(target / "ledger.sqlite"),
                "sha256": file_hash(output / "ledger.sqlite"),
            }
        },
    }
    if relpath:
        payload["artifacts"]["relations"] = {
            "path": str(target / "relations.sqlite"),
            "sha256": file_hash(relpath),
        }
    payload["snapshot_id"] = "bavaria-workspace-sha256:" + identity(payload)
    (output / "workspace.json").write_text(json.dumps(payload, indent=2) + "\n")
    (output / "rights.json").write_text(json.dumps(rights, indent=2) + "\n")
    files = {
        str(p.relative_to(output)): file_hash(p) for p in sorted(output.rglob("*")) if p.is_file()
    }
    bundle = {
        "schema": "klima-pilot-bundle-v1",
        "technical_test_only": bool(technical),
        "source_presentation": presentation,
        "original_verification": "SHA256_AT_EXPORT",
        "parent_snapshot_id": w.snapshot_id,
        "snapshot_id": payload["snapshot_id"],
        "files": files,
        "rights_are_owner_decisions_not_hash_authentication": True,
    }
    (output / "BUNDLE.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return bundle


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--rights", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--deployment-root", default="/snapshot")
    a = p.parse_args()
    export_bundle(a.manifest, a.rights, a.output, deployment_root=a.deployment_root)
