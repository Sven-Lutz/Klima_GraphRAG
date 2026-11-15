"""Restore one verified omitted original span into an explicit derived overlay only."""

import argparse
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup


def restore(baseline, overlay, receipt, ags):
    record = json.loads(Path(receipt).read_text())
    sha = record["source_sha256"]
    uid = record["unit_id"]
    base = sqlite3.connect(Path(baseline).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    base.row_factory = sqlite3.Row
    document = base.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()
    if not document:
        raise ValueError("Original version absent from explicit baseline")
    p = Path(document["raw_path"])
    body = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    if hashlib.sha256(body).hexdigest() != sha:
        raise ValueError("Original hash mismatch")
    soup = BeautifulSoup(body, "lxml")
    for n in soup(["script", "style", "noscript"]):
        n.decompose()
    visible = " ".join(soup.get_text(" ", strip=True).split())
    offset = record["original_visible_offset"]
    text = record["text"]
    if visible[offset : offset + len(text)] != text:
        raise ValueError("Original span offset/text mismatch")
    expected = hashlib.sha256(
        (sha + ":original-span-repair-v1:" + str(offset) + ":" + text).encode()
    ).hexdigest()
    if uid != expected:
        raise ValueError("Fragment identity mismatch")
    municipal = base.execute("SELECT * FROM municipalities WHERE ags=?", (ags,)).fetchone()
    if (
        not municipal
        or not base.execute(
            "SELECT 1 FROM associations WHERE ags=? AND sha256=? AND url=?",
            (ags, sha, record["source_url"]),
        ).fetchone()
    ):
        raise ValueError("Publishing frame not in original association")
    if not Path(overlay).is_file():
        raise FileNotFoundError("Restore/create explicit research overlay before adding a fragment")
    with sqlite3.connect(overlay) as target:
        hit = target.execute("SELECT text,sha256 FROM units WHERE unit_id=?", (uid,)).fetchone()
        if hit:
            if hit != (text, sha):
                raise ValueError("Existing fragment identity changed")
            base.close()
            return {"status": "REUSED_VERIFIED_FRAGMENT", "writes": 0}
        target.execute("PRAGMA foreign_keys=ON")
        target.execute(
            "INSERT OR IGNORE INTO municipalities VALUES (?,?,?,?,?)",
            (municipal["ags"], municipal["record"], "TARGETED_ORIGINAL_SPAN_REPAIR", None, None),
        )
        target.execute(
            "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
            tuple(
                document[k]
                for k in ["sha256", "raw_path", "raw_bytes", "compressed_bytes", "parse_status"]
            ),
        )
        target.execute(
            "INSERT OR IGNORE INTO associations VALUES (?,?,?,?)",
            (sha, ags, record["source_url"], "ORIGINAL_REVIEW_FRAGMENT_PUBLISHER_SCOPE_ONLY"),
        )
        target.execute(
            "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
            (
                uid,
                sha,
                1000000 + offset,
                text,
                "Versorgung Nahwärme (nicht städtisch!)",
                None,
                "original_review_fragment",
            ),
        )
        target.commit()
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    base.close()
    return {
        "status": "RESTORED_EXACT_ORIGINAL_FRAGMENT",
        "unit_id": uid,
        "original_bytes_changed": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for x in ["baseline", "overlay", "receipt", "ags"]:
        p.add_argument("--" + x, required=True)
    a = p.parse_args()
    print(json.dumps(restore(a.baseline, a.overlay, a.receipt, a.ags), indent=2))
