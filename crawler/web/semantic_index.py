"""Resumable local dense index over immutable Bavaria source units.

Stores normalized vectors and source identities, never another copy of the original
text corpus. The existing experimental encoder/baselines are reused unchanged. A
partially built index is observable but cannot serve retrieval. No network/download
or background worker is started by importing or constructing this module.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import heapq
import json
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from crawler.web.local_index import navigation_boilerplate

SCHEMA = "klima-local-semantic-index-v1"
DIMENSIONS = 384
RESERVE = 15 * 1024**3
MAX_BYTES = 768 * 1024**2
BUILD_BATCH = 64
SEARCH_BATCH = 4096
PREPROCESSING = "heading-newline-text;prefix128;masked-mean;l2;float32-v1"
DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "generated/bavaria/text-model"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def _input(row):
    return (row.get("heading") or "") + "\n" + row["text"]


def _binding(workspace, model):
    manifest = json.loads((model / "manifest.json").read_text())
    encoder_path = Path(__file__).resolve().parents[2] / "research/bavaria/semantic.py"
    source = encoder_path.read_text()
    encoder = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))
    return {
        "schema": SCHEMA,
        "snapshot": workspace.snapshot_id,
        "dimensions": DIMENSIONS,
        "preprocessing": PREPROCESSING,
        "model_manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest(),
        "encoder_sha256": hashlib.sha256(
            ast.get_source_segment(source, encoder).encode()
        ).hexdigest(),
    }


class SemanticIndex:
    """One explicit workspace/model/index binding; call build() explicitly to encode."""

    def __init__(self, workspace, directory, model_path=DEFAULT_MODEL, *, encoder=None):
        self.workspace = workspace
        self.path = Path(directory).resolve()
        self.database = self.path / "index.sqlite"
        self.vector_path = self.path / "vectors.f32"
        self.model_path = Path(model_path).resolve()
        self.binding = _binding(workspace, self.model_path)
        self._encoder_instance = encoder
        self._vectors = None
        self._complete_identities = None
        self._identities = {
            Path(p): (Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
            for p in [workspace.db, *workspace.source_databases, workspace.manifest_path]
            if p is not None
        }
        if self.database.exists():
            self._validate_binding()
            if self.status()["complete"]:
                self._validate_complete()

    def _check_workspace(self):
        if self.workspace.snapshot_id != self.binding["snapshot"]:
            raise ValueError("Semantic index belongs to another snapshot")
        for path, identity in self._identities.items():
            if (path.stat().st_size, path.stat().st_mtime_ns) != identity:
                raise ValueError("Semantic source snapshot changed; use a fresh index")
            wal = Path(str(path) + "-wal")
            if wal.exists() and wal.stat().st_size:
                raise ValueError("Semantic source snapshot changed; nonempty WAL")

    @contextmanager
    def conn(self, *, write=False):
        if write:
            connection = sqlite3.connect(self.database)
        else:
            connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA cache_size=-16384")
            connection.execute("PRAGMA foreign_keys=ON")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _validate_binding(self):
        with self.conn() as c:
            stored = c.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()
        if stored is None or json.loads(stored[0]) != self.binding:
            raise ValueError("Semantic index belongs to another snapshot/model/preprocessing")

    def _validate_complete(self):
        state = self.status()
        expected = state["unique_vectors"] * DIMENSIONS * 4
        if not self.vector_path.exists() or self.vector_path.stat().st_size != expected:
            raise ValueError("Semantic vector file missing or wrong size")
        with self.conn() as c:
            checksum = c.execute("SELECT value FROM metadata WHERE key='vector_sha256'").fetchone()
            pending = c.execute("SELECT count(*) FROM vectors WHERE encoded=0").fetchone()[0]
        if pending or checksum is None or _sha(self.vector_path) != checksum[0]:
            raise ValueError("Semantic vector integrity/coverage failure")
        self._complete_identities = {
            p: (p.stat().st_size, p.stat().st_mtime_ns) for p in [self.database, self.vector_path]
        }

    def _check_complete_identity(self):
        if self._complete_identities is None:
            self._validate_complete()
        for path, identity in self._complete_identities.items():
            if (path.stat().st_size, path.stat().st_mtime_ns) != identity:
                raise ValueError("Completed semantic index changed; reopen and verify integrity")

    def status(self):
        if not self.database.exists():
            return {
                "schema": SCHEMA,
                "state": "NOT_BUILT",
                "complete": False,
                "binding": self.binding,
                "units": 0,
                "unique_vectors": 0,
                "encoded_vectors": 0,
            }
        self._validate_binding()
        with self.conn() as c:
            metadata = dict(c.execute("SELECT key,value FROM metadata"))
            units = c.execute("SELECT count(*) FROM units").fetchone()[0]
            unique = c.execute("SELECT count(*) FROM vectors").fetchone()[0]
            encoded = c.execute("SELECT count(*) FROM vectors WHERE encoded=1").fetchone()[0]
        return {
            "schema": SCHEMA,
            "state": metadata["state"],
            "complete": metadata["state"] == "COMPLETE",
            "binding": self.binding,
            "units": units,
            "unique_vectors": unique,
            "encoded_vectors": encoded,
            "deduplicated_units": units - unique,
            "seeded_vectors": int(metadata.get("seeded_vectors", "0")),
            "seed_parent": json.loads(metadata.get("seed_parent", "null")),
            "dimensions": DIMENSIONS,
            "vector_bytes": unique * DIMENSIONS * 4,
            "updated_at": metadata.get("updated_at"),
            "created_at": metadata.get("created_at"),
            "inventory_sha256": metadata.get("inventory_sha256"),
            "vector_sha256": metadata.get("vector_sha256"),
            "partial_queries_allowed": False,
            "model_status": "LOCAL_PINNED_NO_DOWNLOAD",
            "quality_status": "UNREVIEWED_SEMANTIC_SIMILARITY_NOT_ENTAILMENT",
        }

    def _budget(self):
        allocated = sum(p.stat().st_size for p in self.path.iterdir() if p.is_file())
        if allocated > MAX_BYTES:
            raise ValueError("Semantic index output budget reached; partial index preserved")
        if shutil.disk_usage(self.path).free < RESERVE + (MAX_BYTES - allocated):
            raise ValueError("Semantic reserve plus bounded output budget unavailable")

    def _initialize(self):
        if self.database.exists():
            return
        with self.conn(write=True) as c:
            c.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE vectors(id INTEGER PRIMARY KEY,text_sha TEXT UNIQUE NOT NULL,
                    unit_id TEXT NOT NULL,encoded INTEGER NOT NULL DEFAULT 0
                    CHECK(encoded IN(0,1)));
                CREATE TABLE units(unit_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,
                    vector_id INTEGER NOT NULL REFERENCES vectors(id));
                CREATE TABLE frames(unit_id TEXT NOT NULL,ags TEXT NOT NULL,
                    PRIMARY KEY(unit_id,ags));
                CREATE INDEX frames_ags ON frames(ags,unit_id);
                CREATE INDEX units_vector ON units(vector_id,unit_id);
            """)
            c.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [
                    ("binding", json.dumps(self.binding, sort_keys=True)),
                    ("state", "INVENTORY"),
                    ("created_at", _now()),
                    ("updated_at", _now()),
                ],
            )

    def _inventory(self):
        h = hashlib.sha256()
        with self.conn(write=True) as c:
            for i, row in enumerate(self.workspace._unit_rows([]), 1):
                text_sha = hashlib.sha256(_input(row).encode()).hexdigest()
                frames = sorted(set(row["frame_ags"].split(",")))
                h.update(json.dumps([row["unit_id"], row["sha256"], text_sha, frames]).encode())
                c.execute(
                    "INSERT OR IGNORE INTO vectors(text_sha,unit_id) VALUES (?,?)",
                    (text_sha, row["unit_id"]),
                )
                vid = c.execute("SELECT id FROM vectors WHERE text_sha=?", (text_sha,)).fetchone()[
                    0
                ]
                c.execute(
                    "INSERT OR IGNORE INTO units VALUES (?,?,?)",
                    (row["unit_id"], row["sha256"], vid),
                )
                c.executemany(
                    "INSERT OR IGNORE INTO frames VALUES (?,?)",
                    [(row["unit_id"], ags) for ags in frames],
                )
                if i % 2000 == 0:
                    c.commit()
                    self._budget()
            self._check_workspace()
            c.execute("UPDATE metadata SET value='ENCODING' WHERE key='state'")
            c.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('inventory_sha256',?)", (h.hexdigest(),)
            )
            c.execute("UPDATE metadata SET value=? WHERE key='updated_at'", (_now(),))

    def _encoder(self):
        current = json.loads((self.model_path / "manifest.json").read_text())
        digest = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
        if digest != self.binding["model_manifest_sha256"]:
            raise ValueError("Semantic model manifest changed; use a fresh index")
        if self._encoder_instance is None:
            from research.bavaria.semantic import TextEncoder

            self._encoder_instance = TextEncoder(self.model_path)
        return self._encoder_instance

    def seed_from(self, previous_directory):
        """Copy verified identical-input vectors into this separately bound snapshot.

        The parent index remains read-only. Its completion, encoder/preprocessing/model
        binding, vector checksum and stable files are required. Copied states commit
        only after verifying the source again; interruption safely repeats unflagged work.
        """
        previous = Path(previous_directory).resolve()
        if previous == self.path:
            raise ValueError("Semantic seeding requires a different output directory")
        previous_db = previous / "index.sqlite"
        previous_vectors = previous / "vectors.f32"
        with closing(sqlite3.connect(previous_db.as_uri() + "?mode=ro", uri=True)) as old:
            metadata = dict(old.execute("SELECT key,value FROM metadata"))
            parent_binding = json.loads(metadata["binding"])
            if metadata.get("state") != "COMPLETE":
                raise ValueError("Semantic seed parent is incomplete")
            if {k: v for k, v in parent_binding.items() if k != "snapshot"} != {
                k: v for k, v in self.binding.items() if k != "snapshot"
            }:
                raise ValueError("Semantic seed model/preprocessing binding mismatch")
            count, encoded = old.execute("SELECT count(*),sum(encoded) FROM vectors").fetchone()
            if (encoded or 0) != count:
                raise ValueError("Semantic seed parent has incomplete vector coverage")
        if (
            not previous_vectors.exists()
            or previous_vectors.stat().st_size != count * DIMENSIONS * 4
        ):
            raise ValueError("Semantic seed vector size mismatch")
        if _sha(previous_vectors) != metadata.get("vector_sha256"):
            raise ValueError("Semantic seed vector integrity failure")
        identities = {
            p: (p.stat().st_size, p.stat().st_mtime_ns) for p in [previous_db, previous_vectors]
        }
        self._check_workspace()
        self.path.mkdir(parents=True, exist_ok=True)
        self._budget()
        import fcntl

        with (self.path / "writer.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Semantic index already has an active writer") from exc
            self._initialize()
            self._validate_binding()
            state = self.status()
            if state["complete"]:
                raise ValueError("Completed semantic target is immutable; choose a new directory")
            if state["state"] == "INVENTORY":
                self._inventory()
                state = self.status()
            expected = state["vector_bytes"]
            if expected > MAX_BYTES:
                raise ValueError("Semantic vectors exceed output budget")
            if self.vector_path.exists():
                if self.vector_path.stat().st_size != expected:
                    raise ValueError("Partial semantic vector file has unexpected size")
            else:
                with self.vector_path.open("wb") as stream:
                    stream.truncate(expected)
            if not expected or not count:
                return self.status()
            parent = np.memmap(
                previous_vectors, mode="r", dtype="float32", shape=(count, DIMENSIONS)
            )
            target = np.memmap(
                self.vector_path,
                mode="r+",
                dtype="float32",
                shape=(state["unique_vectors"], DIMENSIONS),
            )
            copied = 0
            try:
                with self.conn(write=True) as c:
                    c.execute("ATTACH DATABASE ? AS seed", (previous_db.as_uri() + "?mode=ro",))
                    last = 0
                    while True:
                        batch = list(
                            c.execute(
                                "SELECT n.id new_id,p.id parent_id FROM vectors n "
                                "JOIN seed.vectors p USING(text_sha) "
                                "WHERE n.encoded=0 AND p.encoded=1 AND n.id>? "
                                "ORDER BY n.id LIMIT 1024",
                                (last,),
                            )
                        )
                        if not batch:
                            break
                        self._budget()
                        self._check_workspace()
                        new_ids = [r["new_id"] - 1 for r in batch]
                        old_ids = [r["parent_id"] - 1 for r in batch]
                        target[new_ids] = parent[old_ids]
                        target.flush()
                        c.executemany(
                            "UPDATE vectors SET encoded=1 WHERE id=?",
                            [(r["new_id"],) for r in batch],
                        )
                        last = batch[-1]["new_id"]
                        copied += len(batch)
                    for path, identity in identities.items():
                        if (path.stat().st_size, path.stat().st_mtime_ns) != identity:
                            raise ValueError("Semantic seed parent changed during copy")
                    if _sha(previous_vectors) != metadata["vector_sha256"]:
                        raise ValueError("Semantic seed parent changed during copy")
                    c.execute(
                        "INSERT OR REPLACE INTO metadata VALUES ('seeded_vectors',?)",
                        (str(state["seeded_vectors"] + copied),),
                    )
                    c.execute(
                        "INSERT OR REPLACE INTO metadata VALUES ('seed_parent',?)",
                        (
                            json.dumps(
                                {
                                    "binding": parent_binding,
                                    "vector_sha256": metadata["vector_sha256"],
                                }
                            ),
                        ),
                    )
                    c.execute("UPDATE metadata SET value=? WHERE key='updated_at'", (_now(),))
            finally:
                parent._mmap.close()
                target._mmap.close()
        return self.status()

    def build(self, *, max_batches=None, seconds=None):
        """Checkpoint each64vectors; bounded interruptions resume without re-encoding."""
        if max_batches is not None and (
            isinstance(max_batches, bool) or not isinstance(max_batches, int) or max_batches < 0
        ):
            raise ValueError("max_batches must be a nonnegative integer")
        if seconds is not None and (not np.isfinite(seconds) or seconds <= 0):
            raise ValueError("seconds must be finite and positive")
        self._check_workspace()
        self.path.mkdir(parents=True, exist_ok=True)
        self._budget()
        import fcntl

        started = time.monotonic()
        # Keep one stable lock inode; flock releases on process exit, including interruption.
        with (self.path / "writer.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Semantic index already has an active writer") from exc
            self._initialize()
            self._validate_binding()
            state = self.status()
            if state["complete"]:
                self._validate_complete()
                return state
            if state["state"] == "INVENTORY":
                self._inventory()
                state = self.status()
            expected = state["vector_bytes"]
            if expected > MAX_BYTES:
                raise ValueError("Semantic vectors exceed output budget; no file allocated")
            if self.vector_path.exists():
                if self.vector_path.stat().st_size != expected:
                    raise ValueError("Partial semantic vector file has unexpected size")
            else:
                with self.vector_path.open("wb") as stream:
                    stream.truncate(expected)
            if expected == 0:
                self._complete()
                return self.status()
            vectors = np.memmap(
                self.vector_path,
                mode="r+",
                dtype="float32",
                shape=(state["unique_vectors"], DIMENSIONS),
            )
            batches = 0
            try:
                while True:
                    if (max_batches is not None and batches >= max_batches) or (
                        seconds is not None and time.monotonic() - started >= seconds
                    ):
                        return self.status()
                    self._budget()
                    self._check_workspace()
                    with self.conn() as c:
                        batch = list(
                            c.execute(
                                "SELECT id,text_sha,unit_id FROM vectors WHERE encoded=0 "
                                "ORDER BY id LIMIT ?",
                                (BUILD_BATCH,),
                            )
                        )
                    if not batch:
                        break
                    source = {
                        r["unit_id"]: r
                        for r in self.workspace._unit_rows([], [r["unit_id"] for r in batch])
                    }
                    texts = []
                    for row in batch:
                        text = _input(source[row["unit_id"]])
                        if hashlib.sha256(text.encode()).hexdigest() != row["text_sha"]:
                            raise ValueError("Semantic source text changed during build")
                        texts.append(text)
                    encoded = np.asarray(self._encoder().encode(texts), dtype=np.float32)
                    if encoded.shape != (len(batch), DIMENSIONS) or not np.isfinite(encoded).all():
                        raise ValueError("Invalid semantic encoding shape or nonfinite output")
                    norms = np.linalg.norm(encoded, axis=1, keepdims=True)
                    if (norms <= 1e-12).any():
                        raise ValueError("Invalid zero semantic vector")
                    encoded /= norms
                    for row, vector in zip(batch, encoded, strict=True):
                        vectors[row["id"] - 1] = vector
                    # Flush before flags; interrupted work can safely repeat.
                    vectors.flush()
                    with self.conn(write=True) as c:
                        c.executemany(
                            "UPDATE vectors SET encoded=1 WHERE id=?", [(r["id"],) for r in batch]
                        )
                        c.execute("UPDATE metadata SET value=? WHERE key='updated_at'", (_now(),))
                    batches += 1
                    if batches % 10 == 0:
                        print(json.dumps(self.status()), flush=True)
                self._complete()
            finally:
                vectors._mmap.close()
        return self.status()

    def _complete(self):
        self._check_workspace()
        with self.conn(write=True) as c:
            if c.execute("SELECT count(*) FROM vectors WHERE encoded=0").fetchone()[0]:
                raise ValueError("Cannot complete a partial semantic index")
            c.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('vector_sha256',?)",
                (_sha(self.vector_path),),
            )
            c.execute("UPDATE metadata SET value='COMPLETE' WHERE key='state'")
            c.execute("UPDATE metadata SET value=? WHERE key='updated_at'", (_now(),))

    def discover(self, question, ags=None, k=8):
        started = time.perf_counter()
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("Supply a nonempty semantic question up to4000characters")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
            raise ValueError("Supply semantic k in1..20")
        self._check_workspace()
        state = self.status()
        if not state["complete"]:
            raise ValueError("Semantic index incomplete; run the resumable build")
        self._check_complete_identity()
        selected = self.workspace.resolve_ags(ags, question)
        with self.conn() as c:
            if selected:
                marks = ",".join("?" for _ in selected)
                vids = np.array(
                    [
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT u.vector_id FROM units u JOIN frames f USING(unit_id) "
                            f"WHERE f.ags IN ({marks}) ORDER BY u.vector_id",
                            selected,
                        )
                    ],
                    dtype=np.int64,
                )
                scope_units = c.execute(
                    f"SELECT count(DISTINCT unit_id) FROM frames WHERE ags IN ({marks})", selected
                ).fetchone()[0]
            else:
                vids = np.arange(1, state["unique_vectors"] + 1, dtype=np.int64)
                scope_units = state["units"]
        result = []
        excluded = []
        if len(vids):
            query = np.asarray(self._encoder().encode([question]), dtype=np.float32)
            if query.shape != (1, DIMENSIONS) or not np.isfinite(query).all():
                raise ValueError("Invalid semantic query vector")
            norm = np.linalg.norm(query)
            if norm <= 1e-12:
                raise ValueError("Invalid zero semantic query vector")
            query = query[0] / norm
            if self._vectors is None:
                self._vectors = np.memmap(
                    self.vector_path,
                    mode="r",
                    dtype="float32",
                    shape=(state["unique_vectors"], DIMENSIONS),
                )
            heap = []
            limit = min(80, max(k * 4, 32))
            for offset in range(0, len(vids), SEARCH_BATCH):
                chunk = vids[offset : offset + SEARCH_BATCH]
                scores = np.einsum("ij,j->i", self._vectors[chunk - 1], query)
                if not np.isfinite(scores).all():
                    raise ValueError("Nonfinite stored semantic vectors")
                for vid, score in zip(chunk, scores, strict=True):
                    item = (float(score), -int(vid))
                    if len(heap) < limit:
                        heapq.heappush(heap, item)
                    elif item > heap[0]:
                        heapq.heapreplace(heap, item)
            score_map = {-vid: score for score, vid in heap}
            ids = []
            with self.conn() as c:
                for vid in score_map:
                    scope = ""
                    params = [vid]
                    if selected:
                        scope = (
                            " AND unit_id IN (SELECT unit_id FROM frames WHERE ags IN ("
                            + ",".join("?" for _ in selected)
                            + "))"
                        )
                        params.extend(selected)
                    # At mostkunits per dedup vector can contribute to a top-kresult.
                    ids.extend(
                        (r[0], score_map[vid])
                        for r in c.execute(
                            "SELECT unit_id FROM units WHERE vector_id=?"
                            + scope
                            + " ORDER BY unit_id LIMIT ?",
                            [*params, k],
                        )
                    )
            ranked = sorted(ids, key=lambda x: (-x[1], x[0]))
            source = {
                r["unit_id"]: r
                for r in self.workspace._unit_rows(selected, [uid for uid, _ in ranked])
            }
            for uid, score in ranked:
                row = source[uid]
                if navigation_boilerplate(row["text"]):
                    excluded.append(uid)
                    continue
                if len(result) >= k:
                    continue
                row.update(
                    score=score,
                    source_frame_ags=row.pop("frame_ags").split(","),
                    claim_scope_ags=None,
                    source_type="segment",
                    source_id=uid,
                )
                result.append(row)
        return {
            "schema": "bavaria-workspace-discovery-v1",
            "question": question,
            "status": "EVIDENCE_DISCOVERY_NOT_A_COMPLETE_POLICY_ANSWER",
            "snapshot_id": self.workspace.snapshot_id,
            "scope_ags": selected,
            "method": "local-minilm-semantic-v1",
            "execution": "deduplicated-float32-memmap-exact-cosine",
            "candidate_units": scope_units,
            "results": result,
            "warnings": [
                "Semantic rank is similarity, not entailment or policy validation.",
                "Long units use the pinned128token prefix; full source remains accessible.",
                "Source frame is not individual claim jurisdiction.",
            ],
            "diagnostics": {
                "schema": "local-semantic-diagnostics-v1",
                "index_binding": self.binding,
                "effective_ags": selected,
                "scope_indexed_units": scope_units,
                "unique_scored_vectors": len(vids),
                "top_k": k,
                "excluded_navigation_unit_ids": excluded,
                "fallback": False,
                "partial_index": False,
                "minimum_score": None,
                "retrieval_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "final_source_ids": [r["sha256"] for r in result],
                "final_unit_ids": [r["unit_id"] for r in result],
            },
        }

    def close(self):
        if self._vectors is not None:
            self._vectors._mmap.close()
            self._vectors = None
        self._encoder_instance = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "query", "status"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--seed-from", type=Path)
    parser.add_argument("--question")
    parser.add_argument("--ags", action="append")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    from crawler.web.bavaria import create_bavaria_service

    service = create_bavaria_service(args.manifest)
    index = SemanticIndex(service.workspace, args.directory, args.model)
    try:
        if args.command == "build":
            if args.seed_from:
                index.seed_from(args.seed_from)
            result = index.build(seconds=args.seconds, max_batches=args.max_batches)
        elif args.command == "query":
            result = index.discover(args.question, args.ags, args.top_k)
        else:
            result = index.status()
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    finally:
        index.close()
        service.close()


if __name__ == "__main__":
    main()
