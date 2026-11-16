"""Persistent normalized-term FTS5 BM25 for local discovery, bound to a real snapshot.

No raw text copy: search terms and stable unit references only. Frozen streaming BM25
and scientific evaluations retain their original implementation unless explicitly configured.
"""

import hashlib
import inspect
import json
import re
import shutil
import sqlite3
import time
from collections import Counter
from contextlib import closing, contextmanager
from pathlib import Path

from research.bavaria.federation import source_connection
from research.bavaria.workspace.core import norm, roots
from research.experiments.e2_offline_20261024.kit.vendor import lexical

# Explicit exploratory query rules, never part of the frozen streaming baseline.
LOCAL_EXPANSION = {
    "hitzevorsorge": ("hitzeschutz", "hitzeaktionsplan", "hitzewellen", "hitze"),
    "hitzeschutz": ("hitzevorsorge", "hitzeaktionsplan", "hitze"),
    "hitzeaktionsplan": ("hitzeschutz", "hitzevorsorge", "hitze"),
    "solarenergie": ("photovoltaik", "solar", "pv"),
    "klimaziele": ("klimaschutz", "klimaneutral", "treibhausgas"),
    "energieprojekte": ("energie", "erneuerbare", "energieversorgung"),
    "energieeinspar": ("energieeffizienz", "energiesparen", "energieeinsparung"),
    "energieversorgung": ("energieerzeugung", "erneuerbare", "fernwärme", "stromversorgung"),
    "klimaschutzmaßnahmen": ("klimaschutz", "emissionsminderung", "co2"),
    "wärmeversorgung": ("wärmeplanung", "nahwärme", "fernwärme"),
    "verkehrswende": ("radverkehr", "radweg", "öpnv", "mobilität"),
    "hitzeanpassung": ("hitze", "hitzeschutz", "hitzeaktionsplan"),
}

# Query-only processing; the stored tokenizer/index and frozen baselines stay unchanged.
QUERY_CONTEXT = set(
    roots(
        "nennt nennen beschreibt beschreiben Quellen dokumentiert dokumentieren "
        "kommunale kommunalen Maßnahmen Projekte"
    )
)


def navigation_boilerplate(text):
    normalized = norm(text).strip()
    return normalized.startswith("seite ubersetzen mit google translate") or normalized in {
        "drucken",
        "seite drucken",
        "zum inhalt",
        "nach oben",
        "datenschutz",
        "impressum",
        "barrierefreiheit",
        "alle cookies akzeptieren",
    }


SCHEMA = "klima-local-term-index-v1"
RESERVE = 15 * 1024**3
MAX_BYTES = 768 * 1024**2


def binding(w):
    return {
        "schema": SCHEMA,
        "snapshot": w.snapshot_id,
        "tokenizer": hashlib.sha256(
            inspect.getsource(roots).encode() + Path(lexical.__file__).read_bytes()
        ).hexdigest(),
    }


class LocalIndex:
    def __init__(self, workspace, path, *, build_if_missing=True):
        self.workspace, self.path = workspace, Path(path).resolve()
        if not self.path.exists():
            if not build_if_missing:
                raise FileNotFoundError("Existing term index required; build disabled")
            self.build()
        with self.conn() as c:
            metadata = json.loads(
                c.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()[0]
            )
            if metadata != binding(workspace):
                raise ValueError(
                    "Local index belongs to another snapshot/tokenizer; use a fresh index path"
                )
            if c.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()[0] != "true":
                raise ValueError("Incomplete local index")

    @contextmanager
    def conn(self):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as c:
            c.row_factory = sqlite3.Row
            yield c

    def build(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < RESERVE + MAX_BYTES:
            raise ValueError("Reserve plus bounded index budget unavailable")
        pending = self.path.with_suffix(".building")
        if pending.exists():
            raise ValueError("Interrupted index exists; inspect it before restarting")
        c = sqlite3.connect(pending)
        try:
            c.executescript("""
              PRAGMA journal_mode=DELETE;
              PRAGMA cache_size=-16384;
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
              CREATE VIRTUAL TABLE terms USING fts5(tokens,content='');
              CREATE TABLE units(id INTEGER PRIMARY KEY,unit_id TEXT UNIQUE,sha256 TEXT);
              CREATE TABLE frames(id INTEGER,ags TEXT,PRIMARY KEY(id,ags));
              CREATE INDEX frame_ags ON frames(ags,id);
              CREATE TABLE sources(sha256 TEXT PRIMARY KEY,url TEXT,raw_path TEXT,
                raw_bytes INTEGER,status TEXT,kind TEXT,unit_count INTEGER);
              CREATE TABLE source_frames(sha256 TEXT,ags TEXT,PRIMARY KEY(sha256,ags));
            """)
            w = self.workspace
            with closing(source_connection(w.db, w.source_databases)) as source:
                c.executemany(
                    "INSERT INTO sources VALUES (?,?,?,?,?,?,?)",
                    source.execute(
                        "SELECT d.sha256,min(a.url),d.raw_path,d.raw_bytes,d.parse_status,"
                        "coalesce(min(u.kind),'unknown'),count(DISTINCT u.unit_id) "
                        "FROM documents d LEFT JOIN associations a USING(sha256) "
                        "LEFT JOIN units u USING(sha256) GROUP BY d.sha256"
                    ),
                )
                c.executemany(
                    "INSERT INTO source_frames SELECT ?,?",
                    source.execute("SELECT DISTINCT sha256,ags FROM associations"),
                )
            for i, row in enumerate(w._unit_rows([]), 1):
                tokens = roots((row["heading"] or "") + " " + row["source_url"] + " " + row["text"])
                c.execute("INSERT INTO terms(rowid,tokens) VALUES (?,?)", (i, " ".join(tokens)))
                c.execute("INSERT INTO units VALUES (?,?,?)", (i, row["unit_id"], row["sha256"]))
                c.executemany(
                    "INSERT INTO frames VALUES (?,?)",
                    [(i, ags) for ags in row["frame_ags"].split(",")],
                )
                if i % 5000 == 0:
                    c.commit()
                    if (
                        pending.stat().st_size > MAX_BYTES
                        or shutil.disk_usage(pending.parent).free < RESERVE + 32 * 1024**2
                    ):
                        raise ValueError("Index resource budget reached; partial file preserved")
                    print(f"Local index: {i} real units", flush=True)
            c.execute(
                "INSERT INTO metadata VALUES ('binding',?)",
                (json.dumps(binding(w), sort_keys=True),),
            )
            c.execute("INSERT INTO metadata VALUES ('complete','true')")
            c.commit()
        finally:
            c.close()
        pending.rename(self.path)

    def expand_query(self, question):
        folded = norm(question)
        return tuple(
            dict.fromkeys(
                word
                for key, words in LOCAL_EXPANSION.items()
                if re.search(r"(?<!\w)" + re.escape(norm(key)), folded)
                for word in words
            )
        )

    def discover(self, question, ags=None, k=8):
        started = time.perf_counter()
        w = self.workspace
        if not question.strip() or not 1 <= k <= 20:
            raise ValueError("Supply a question and k in 1..20")
        selected = w.resolve_ags(ags, question)
        terms = Counter(roots(question))
        for target in selected:
            for term in roots(w.municipalities[target]["name"]):
                terms.pop(term, None)
        ignored = sorted(set(terms) & QUERY_CONTEXT) if set(terms) - QUERY_CONTEXT else []
        for term in ignored:
            terms.pop(term, None)
        query = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        ranked = []
        global_candidates = scoped_candidates = 0
        with self.conn() as c:
            scope_units = (
                c.execute(
                    "SELECT count(DISTINCT id) FROM frames WHERE ags IN ("
                    + ",".join("?" for _ in selected)
                    + ")",
                    selected,
                ).fetchone()[0]
                if selected
                else c.execute("SELECT count(*) FROM units").fetchone()[0]
            )
            scope_sources = (
                c.execute(
                    "SELECT count(DISTINCT sha256) FROM source_frames WHERE ags IN ("
                    + ",".join("?" for _ in selected)
                    + ")",
                    selected,
                ).fetchone()[0]
                if selected
                else c.execute("SELECT count(*) FROM sources").fetchone()[0]
            )
        if query:
            params = [query]
            where = ""
            if selected:
                where = (
                    " AND u.id IN (SELECT id FROM frames WHERE ags IN ("
                    + ",".join("?" for _ in selected)
                    + "))"
                )
                params.extend(selected)
            with self.conn() as c:
                global_candidates = c.execute(
                    "SELECT count(*) FROM terms WHERE terms MATCH ?", [query]
                ).fetchone()[0]
                scoped_candidates = c.execute(
                    "SELECT count(*) FROM terms JOIN units u ON u.id=terms.rowid "
                    "WHERE terms MATCH ?" + where,
                    params,
                ).fetchone()[0]
                ranked = list(
                    c.execute(
                        "SELECT u.unit_id,bm25(terms) score FROM terms "
                        "JOIN units u ON u.id=terms.rowid "
                        "WHERE terms MATCH ?" + where + " ORDER BY score,u.unit_id LIMIT ?",
                        [*params, min(80, k * 4)],
                    )
                )
        rows = {r["unit_id"]: r for r in w._unit_rows(selected, [r["unit_id"] for r in ranked])}
        results = []
        excluded = []
        for match in ranked:
            row = rows[match["unit_id"]]
            if navigation_boilerplate(row["text"]):
                excluded.append(row["unit_id"])
                continue
            if len(results) >= k:
                continue
            row.update(
                score=-match["score"],
                source_frame_ags=row.pop("frame_ags").split(","),
                claim_scope_ags=None,
                source_type="segment",
                source_id=row["unit_id"],
            )
            results.append(row)
        return dict(
            schema="bavaria-workspace-discovery-v1",
            question=question,
            status="EVIDENCE_DISCOVERY_NOT_A_COMPLETE_POLICY_ANSWER",
            snapshot_id=w.snapshot_id,
            scope_ags=selected,
            method="sqlite-fts5-context-bm25-query-v2",
            execution="persistent-term-index",
            candidate_units=None,
            results=results,
            warnings=[
                "Global index IDF; municipality filtering restricts candidates, not statistics.",
                "Rank is not entailment; no hit is not no policy.",
            ],
            diagnostics={
                "schema": "local-retrieval-diagnostics-v1",
                "index_binding": binding(w),
                "query": question,
                "normalized_terms": list(terms),
                "query_processing": "local-content-terms-v2",
                "ignored_context_terms": ignored,
                "fts_expression": query,
                "effective_ags": selected,
                "scope_sources": scope_sources,
                "scope_indexed_units": scope_units,
                "candidates_before_municipality": global_candidates,
                "candidates_after_municipality": scoped_candidates,
                "after_top_k": len(results),
                "candidate_window": len(ranked),
                "navigation_filter": "exact-known-navigation-text-v1",
                "excluded_navigation_unit_ids": excluded,
                "top_k": k,
                "minimum_score": None,
                "source_ids": [r["sha256"] for r in results],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "finding": "LEXICAL_CANDIDATES_REQUIRE_INSPECTION"
                if results
                else "NO_MATCH_IN_INDEXED_SCOPE"
                if scope_units
                else "NO_INDEXED_TEXT_IN_SCOPE",
            },
        )
