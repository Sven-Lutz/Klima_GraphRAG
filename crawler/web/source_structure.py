"""Bounded read-only structure hints for product synthesis; never rewrites source units."""

from contextlib import closing
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

from crawler.web.bavaria import read_original
from research.bavaria.federation import source_connection


def normalized_text(value):
    return " ".join(value.split())


def structural_texts(raw):
    """Exact headings/link labels, excluding ambiguous identical prose occurrences."""
    soup = BeautifulSoup(raw, "html.parser")
    structural, prose = set(), set()
    tags = soup.find_all(
        ["a", "button", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p"], limit=10001
    )
    if len(tags) > 10000:
        return None
    for tag in tags:
        text = normalized_text(tag.get_text(" ", strip=True))
        if not text or len(text) > 1800:
            continue
        if tag.name in {"li", "p"}:
            links = normalized_text(
                " ".join(a.get_text(" ", strip=True) for a in tag.find_all(["a", "button"]))
            )
            (structural if links == text else prose).add(text)
        else:
            structural.add(text)
    return frozenset(structural - prose)


class SourceStructureInspector:
    def __init__(self, workspace, original_roots):
        self.workspace, self.original_roots = workspace, original_roots

    @lru_cache(maxsize=8)
    def _document(self, sha, path, size, _signature):
        if not 0 <= size <= 2 * 1024**2:
            return None
        raw, _, _ = read_original(
            dict(raw_path=path, raw_bytes=size), sha, self.original_roots, raw=True
        )
        if raw.startswith(b"%PDF"):
            return None
        return structural_texts(raw)

    def __call__(self, metadata, text):
        return self.inspect_many([dict(metadata=metadata, evidence_text=text)])[0]

    def inspect_many(self, blocks):
        if not blocks:
            return []
        ids = tuple(dict.fromkeys(block["metadata"].get("source_id") for block in blocks))
        with closing(source_connection(self.workspace.db, self.workspace.source_databases)) as conn:
            rows = {
                row["unit_id"]: dict(row)
                for row in conn.execute(
                    "SELECT u.unit_id,u.kind,u.text,u.sha256,d.raw_path,d.raw_bytes FROM units u "
                    "JOIN documents d ON d.sha256=u.sha256 WHERE u.unit_id IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                )
            }
        return [
            self._inspect(
                rows.get(block["metadata"].get("source_id")),
                block["metadata"],
                block["evidence_text"],
            )
            for block in blocks
        ]

    def _inspect(self, row, metadata, text):
        if row is None or normalized_text(row["text"]) != normalized_text(text):
            return dict(state="UNKNOWN_OR_PARTIAL_UNIT", structural_only=False)
        if metadata.get("document_id") != "source-version:" + row["sha256"]:
            raise ValueError("Source structure belongs to another original")
        result = dict(
            state="LEDGER_UNIT_KIND", kind=row["kind"], structural_only=row["kind"] == "heading"
        )
        # Paragraphs remain prose. Only short non-prose units need bounded DOM inspection.
        if row["kind"] not in {"heading", "list_item"} or len(text) > 1800:
            return result
        stat = Path(row["raw_path"]).stat()
        document = self._document(
            row["sha256"], row["raw_path"], row["raw_bytes"], (stat.st_mtime_ns, stat.st_size)
        )
        if document is not None:
            result.update(
                state="HASH_VERIFIED_ORIGINAL_STRUCTURE",
                structural_only=normalized_text(text) in document,
            )
        return result
