"""Canonical, snapshot-bound retrieval over SQLite evidence and Neo4j projections.

SQLite is authoritative for source text and provenance. Neo4j is an optional,
bounded navigation index whose identities are resolved back to the manifested
SQLite snapshot before a result is returned.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import numpy as np

from crawler.core.database import connect_existing_read_only
from crawler.core.neo4j_projection import ProjectionScopeError, graph_artifact_id_for
from crawler.core.provenance import evidence_id_for
from crawler.core.snapshots import SnapshotBinding, validate_snapshot_manifest

EMBEDDING_SCHEMA_VERSION = 1
DEFAULT_GRAPH_TIMEOUT_SECONDS = 15.0
MAX_TOP_K = 100
MAX_GRAPH_CANDIDATES = 500
RRF_CONSTANT = 60
MIN_FUSION_CANDIDATES = 50
FUSION_CANDIDATE_MULTIPLIER = 8

_LEXICAL_STOPWORDS = frozenset(
    {
        "aber", "als", "am", "an", "auch", "auf", "aus", "bei", "bis", "das",
        "dem", "den", "der", "des", "die", "ein", "eine", "einer", "eines",
        "für", "hat", "im", "in", "ist", "mit", "nach", "oder", "sich", "sind",
        "und", "von", "vor", "wie", "wird", "zu", "zur", "zum",
        "aktuell", "stadt", "gemeinde", "kommune", "landkreis",
        "a", "an", "and", "are", "city", "does", "for", "has", "in", "is",
        "municipality", "of", "or", "the", "to", "with",
    }
)
_LEXICAL_SUFFIXES = (
    "ungen", "heiten", "keiten", "isch", "lich", "ung", "ern", "en", "er",
    "es", "e", "s", "t",
)


class RetrievalError(RuntimeError):
    """Base error for a retrieval request that cannot be trusted."""


class EmbeddingLineageError(RetrievalError):
    """Raised when vectors are legacy, incomplete, or lineage-incompatible."""


class ProjectionMismatchError(RetrievalError):
    """Raised when Neo4j does not exactly match the requested snapshot scope."""


class RetrievalMode(str, Enum):
    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    SEMANTIC_LEXICAL = "semantic_lexical"
    GRAPH = "graph"
    MENTION = "mention"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider: str
    model: str
    model_revision: str
    vector_dimension: int
    preprocessing_id: str

    def validate(self) -> None:
        values = (self.provider, self.model, self.model_revision, self.preprocessing_id)
        if any(not value.strip() for value in values) or self.vector_dimension < 1:
            raise EmbeddingLineageError("Embedding identity fields must be explicit and non-empty")


class QueryEmbedder(Protocol):
    identity: EmbeddingIdentity

    def embed_query(self, text: str) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class DocumentSource:
    document_id: str
    source_url: str
    heading_context: str | None
    municipality_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphAssertion:
    assertion_id: str
    graph_artifact_id: str
    extraction_run_id: str
    subject_id: str
    subject_name: str
    relation_type: str
    object_id: str
    object_name: str
    evidence_excerpt: str | None


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    method: str
    source_rank: int
    source_score: float
    fusion_score: float | None = None


@dataclass(frozen=True, slots=True)
class ReviewedPolicyFact:
    """Human-reviewed, programme-scoped SQLite edge, not an LLM extraction."""

    fact_id: str
    scheme_id: str
    scheme_name: str
    city_id: str
    predicate: str
    target: str
    source_quote: str
    quote_start: int
    quote_end: int
    effective_from: str | None
    approved_utc: str


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    database_snapshot_id: str
    evidence_id: str
    source_type: str
    source_id: str
    segment_id: str
    chunk_id: str | None
    document: DocumentSource
    evidence_text: str
    extraction_run_ids: tuple[str, ...]
    graph_artifact_ids: tuple[str, ...]
    assertions: tuple[GraphAssertion, ...]
    retrieval_traces: tuple[RetrievalTrace, ...]
    retrieval_score: float
    rank: int
    reviewed_policy_facts: tuple[ReviewedPolicyFact, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    manifest_path: str
    mode: RetrievalMode = RetrievalMode.SEMANTIC
    top_k: int = 8
    projection_id: str | None = None
    extraction_run_ids: tuple[str, ...] = ()
    graph_hops: int = 1
    municipality_ids: tuple[str, ...] = ()
    graph_source: str = "neo4j"  # Explicit: neo4j, reviewed_sqlite, or structural_sqlite.

    def validate(self) -> None:
        if not isinstance(self.mode, RetrievalMode):
            raise RetrievalError("mode must be a RetrievalMode value")
        if not self.query.strip():
            raise RetrievalError("Retrieval query must not be empty")
        if not 1 <= self.top_k <= MAX_TOP_K:
            raise RetrievalError(f"top_k must be between 1 and {MAX_TOP_K}")
        if self.graph_hops != 1:
            raise RetrievalError("Canonical graph retrieval currently supports exactly one hop")
        if any(not str(value).strip() for value in self.municipality_ids):
            raise RetrievalError("municipality_ids must contain non-empty identifiers")
        if self.graph_source not in {"neo4j", "reviewed_sqlite", "structural_sqlite"}:
            raise RetrievalError("Unsupported graph_source")
        if self.mode == RetrievalMode.MENTION and self.graph_source != "neo4j":
            raise RetrievalError("Mention retrieval currently uses the Neo4j projection only")
        if self.mode in {RetrievalMode.GRAPH, RetrievalMode.MENTION, RetrievalMode.HYBRID}:
            if self.graph_source == "neo4j" and not self.projection_id:
                raise RetrievalError("Neo4j graph, mention, and hybrid retrieval require projection_id")
            if self.graph_source in {"reviewed_sqlite", "structural_sqlite"} and (
                self.projection_id is not None or self.extraction_run_ids
            ):
                raise RetrievalError(
                    "SQLite graph retrieval does not accept Neo4j projection/run scopes"
                )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    mode: str
    database_snapshot_id: str
    projection_id: str | None
    items: tuple[RetrievedEvidence, ...]
    embedding_artifact_id: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def render_retrieval_json(result: RetrievalResult, *, indent: int | None = 2) -> str:
    """Render the canonical retrieval contract with its deterministic citations."""
    from crawler.core.citations import build_citation_registry

    payload = result.to_dict()
    payload["citation_registry"] = build_citation_registry(result.items).to_dict()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=indent)


def render_retrieval_markdown(result: RetrievalResult) -> str:
    """Render retrieval output without recreating citation identity downstream."""
    from crawler.core.citations import (
        build_citation_registry,
        render_citations_markdown,
    )

    registry = build_citation_registry(result.items)
    lines = [f"# Retrieval results ({result.mode})", ""]
    for item, citation in zip(result.items, registry.citations, strict=True):
        text = item.evidence_text.replace("\n", " ").strip()
        lines.extend(
            [
                f"## [{citation.label}] rank {item.rank}",
                "",
                text,
                "",
                f"Method score: `{item.retrieval_score:.8f}`; evidence: `{item.evidence_id}`",
                "",
            ]
        )
    lines.append(render_citations_markdown(registry).rstrip())
    return "\n".join(lines) + "\n"


class GraphReadBackend(Protocol):
    def read(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> list[Mapping[str, Any]]: ...

    def close(self) -> None: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_to_blob(vector: np.ndarray) -> bytes:
    values = np.asarray(vector, dtype="<f4")
    return values.tobytes(order="C")


def blob_to_vector(blob: bytes, expected_dimension: int) -> np.ndarray:
    if len(blob) != expected_dimension * 4:
        raise EmbeddingLineageError(
            f"Embedding blob has {len(blob) // 4} dimensions; expected {expected_dimension}"
        )
    return np.frombuffer(blob, dtype="<f4").astype(np.float32, copy=True)


def ensure_embedding_schema(connection: sqlite3.Connection) -> None:
    """Create the versioned vector-artifact contract on a writable live database."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS embedding_artifacts (
          artifact_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          model_revision TEXT NOT NULL,
          vector_dimension INTEGER NOT NULL CHECK(vector_dimension > 0),
          preprocessing_id TEXT NOT NULL,
          source_database_id TEXT NOT NULL,
          corpus_fingerprint TEXT NOT NULL,
          planned_count INTEGER NOT NULL,
          embedded_count INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
          created_at TEXT NOT NULL,
          completed_at TEXT,
          UNIQUE(provider, model, model_revision, vector_dimension, preprocessing_id,
                 source_database_id, corpus_fingerprint)
        );
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
          artifact_id TEXT NOT NULL,
          chunk_id TEXT NOT NULL,
          source_content_hash TEXT NOT NULL,
          vector_dimension INTEGER NOT NULL CHECK(vector_dimension > 0),
          embedding BLOB NOT NULL,
          embedded_at TEXT NOT NULL,
          PRIMARY KEY(artifact_id, chunk_id),
          FOREIGN KEY(artifact_id) REFERENCES embedding_artifacts(artifact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_chunk
          ON chunk_embeddings(chunk_id, artifact_id);
        """
    )


def chunk_corpus_rows(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return stable chunk ID, content hash, and text without consulting rowids."""
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(chunks)")}
    if not {"chunk_id", "text"} <= columns:
        raise EmbeddingLineageError("chunks lacks the canonical chunk_id/text contract")
    hash_expression = "chunk_hash" if "chunk_hash" in columns else "NULL"
    rows = connection.execute(
        f"SELECT chunk_id, {hash_expression}, text FROM chunks ORDER BY chunk_id"
    ).fetchall()
    result = []
    for chunk_id, stored_hash, text in rows:
        content_hash = _sha256(str(text))
        if stored_hash and str(stored_hash) != content_hash:
            raise EmbeddingLineageError(
                f"Stored content hash disagrees with chunk text for {chunk_id}"
            )
        result.append((str(chunk_id), content_hash, str(text)))
    return result


def corpus_fingerprint(rows: Sequence[tuple[str, str, str]]) -> str:
    return "chunk-corpus-sha256:" + _sha256(
        _canonical_json([[chunk_id, content_hash] for chunk_id, content_hash, _ in rows])
    )


def embedding_artifact_id(
    identity: EmbeddingIdentity,
    *,
    source_database_id: str,
    corpus_identity: str,
) -> str:
    identity.validate()
    return "embedding-artifact-sha256:" + _sha256(
        _canonical_json(
            {
                **asdict(identity),
                "schema_version": EMBEDDING_SCHEMA_VERSION,
                "source_database_id": source_database_id,
                "corpus_fingerprint": corpus_identity,
            }
        )
    )


def _manifest_source_database_id(binding: SnapshotBinding) -> str:
    source = binding.manifest.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("database_id"), str):
        raise RetrievalError("Snapshot manifest lacks its source database identity")
    return str(source["database_id"])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _select_embedding_artifact(
    connection: sqlite3.Connection,
    binding: SnapshotBinding,
    identity: EmbeddingIdentity,
) -> tuple[str, list[tuple[str, str, str]]]:
    identity.validate()
    if not _table_exists(connection, "embedding_artifacts") or not _table_exists(
        connection, "chunk_embeddings"
    ):
        raise EmbeddingLineageError(
            "Only legacy/unverifiable chunk embeddings are present; regenerate canonical vectors"
        )
    rows = chunk_corpus_rows(connection)
    fingerprint = corpus_fingerprint(rows)
    source_database_id = _manifest_source_database_id(binding)
    matches = connection.execute(
        """
        SELECT artifact_id, planned_count, embedded_count, status
        FROM embedding_artifacts
        WHERE schema_version=? AND provider=? AND model=? AND model_revision=?
          AND vector_dimension=? AND preprocessing_id=? AND source_database_id=?
          AND corpus_fingerprint=?
        ORDER BY artifact_id
        """,
        (
            EMBEDDING_SCHEMA_VERSION,
            identity.provider,
            identity.model,
            identity.model_revision,
            identity.vector_dimension,
            identity.preprocessing_id,
            source_database_id,
            fingerprint,
        ),
    ).fetchall()
    if len(matches) != 1:
        raise EmbeddingLineageError(
            "No unique completed embedding artifact matches this model, corpus, and snapshot source"
        )
    artifact_id, planned, embedded, status = matches[0]
    artifact_rows = connection.execute(
        """
        SELECT chunk_id, source_content_hash, vector_dimension, length(embedding)
        FROM chunk_embeddings WHERE artifact_id=? ORDER BY chunk_id
        """,
        (artifact_id,),
    ).fetchall()
    actual = len(artifact_rows)
    expected = len(rows)
    complete = (
        status == "completed"
        and int(planned) == expected
        and int(embedded) == expected
        and actual == expected
    )
    if not complete:
        raise EmbeddingLineageError(
            f"Embedding corpus is partial: expected {expected}, artifact reports "
            f"{embedded}, and stores {actual}"
        )
    expected_rows = [(chunk_id, content_hash) for chunk_id, content_hash, _ in rows]
    actual_rows = [(str(row[0]), str(row[1])) for row in artifact_rows]
    if actual_rows != expected_rows:
        raise EmbeddingLineageError(
            "Embedding corpus membership or source-content identity does not exactly match "
            "the canonical chunk corpus"
        )
    for chunk_id, _content_hash, dimension, byte_length in artifact_rows:
        if int(dimension) != identity.vector_dimension:
            raise EmbeddingLineageError("Embedding artifact mixes vector dimensions")
        if int(byte_length) != identity.vector_dimension * 4:
            raise EmbeddingLineageError(
                f"Embedding blob for chunk {chunk_id} disagrees with its vector dimension"
            )
    return str(artifact_id), rows


def _municipalities(connection: sqlite3.Connection, document_id: str) -> tuple[str, ...]:
    values: set[str] = set()
    if _table_exists(connection, "municipality_documents"):
        values.update(
            str(row[0])
            for row in connection.execute(
                "SELECT municipality_id FROM municipality_documents "
                "WHERE document_id=? ORDER BY municipality_id",
                (document_id,),
            )
        )
    row = connection.execute(
        "SELECT municipality_id FROM documents_raw WHERE document_id=?", (document_id,)
    ).fetchone()
    if row and row[0] is not None:
        values.add(str(row[0]))
    return tuple(sorted(values))


def _validated_source_url(value: Any, document_id: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RetrievalError(f"Document {document_id} lacks a canonical HTTP(S) source URL")
    return url


def _required_municipalities(connection: sqlite3.Connection, document_id: str) -> tuple[str, ...]:
    municipalities = _municipalities(connection, document_id)
    if not municipalities:
        raise RetrievalError(f"Document {document_id} lacks municipality provenance")
    return municipalities


def _lexical_stem(token: str) -> str:
    # Deterministic German-friendly stem used only to broaden FTS candidates.
    for suffix in _LEXICAL_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _lexical_match_query(text: str) -> str:
    tokens = [
        token.lower()
        for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE)
        if len(token) >= 3
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in _LEXICAL_STOPWORDS:
            continue
        for variant in (token, _lexical_stem(token)):
            if len(variant) < 3 or variant in seen:
                continue
            seen.add(variant)
            terms.append(f'"{variant}"*')
    if not terms:
        raise RetrievalError("Lexical retrieval query contains no searchable terms")
    from crawler.core.question_intent import SupportType, extract_question_intent
    from crawler.core.retrieval_query_expansion import _POLICY_TERMS
    intent = extract_question_intent(text)
    if intent.policy_family and intent.support_type == SupportType.FINANCIAL:
        alternatives = []
        for alias in _POLICY_TERMS.get(intent.policy_family, ()):
            tokens = re.findall(r'[^\W_]+', alias.casefold(), flags=re.UNICODE)
            if not tokens:
                continue
            phrase = chr(34) + ' '.join(tokens) + chr(34)
            alternatives.append(phrase + ('*' if len(tokens)==1 else ''))
        if alternatives:
            return '(' + ' OR '.join(dict.fromkeys(alternatives)) + ') AND (förder* OR foerder* OR zuschuss* OR bezuschuss*)'
    return " OR ".join(terms[:40])


def _open_lexical_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RetrievalError(f"Lexical index does not exist: {path}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _validate_lexical_index(
    connection: sqlite3.Connection,
    binding: SnapshotBinding,
) -> None:
    if not _table_exists(connection, "metadata") or not _table_exists(connection, "chunks_fts"):
        raise RetrievalError("Lexical index lacks the required metadata/chunks_fts contract")
    metadata = {
        str(key): str(value)
        for key, value in connection.execute("SELECT key, value FROM metadata").fetchall()
    }
    expected_manifest_id = str(binding.manifest.get("manifest_id") or "")
    if not expected_manifest_id:
        raise RetrievalError("Snapshot manifest lacks manifest_id")
    if metadata.get("source_snapshot_id") != expected_manifest_id:
        raise RetrievalError(
            "Lexical index source snapshot does not match the requested manifested snapshot"
        )
    if metadata.get("index_type") != "sqlite-fts5-unicode61":
        raise RetrievalError("Unsupported lexical index type")


# Search paths separate concept discovery from status/decision evidence.
# This is intentionally not a verdict engine: an agenda, withdrawn proposal, or
# utility product offer is never converted here into a Yes/No policy assertion.
_LEXICAL_FINANCIAL_WORDS = (
    "förder", "foerder", "zuschuss", "bezuschuss", "fördermittel", "foerdermittel",
    "subvention", "förderprogramm", "foerderprogramm", "förderrichtlin", "foerderrichtlin",
    "förderfähig", "foerderfaehig", "finanzierung", "finanziell",
)
_LEXICAL_DECISION_WORDS = (
    "beschluss", "beschlossen", "aufgehoben", "eingestellt", "gestrichen",
    "abgeschafft", "ausgelaufen", "beendet", "verlängert", "verlaengert",
    "nicht mehr", "nicht förderfähig", "nicht bezuschusst", "abgelehnt", "haushaltsmittel", "haushaltssperre",
    "förderrichtlinie", "foerderrichtlinie", "bewilligt",
)
_LEXICAL_QUESTION_WORDS = frozenset({
    "bietet", "bieten", "biete", "welche", "welcher", "welches", "was", "wie",
    "wer", "warum", "wann", "gibt", "gegeben", "werden", "wurde", "wurden",
    "einen", "einem", "einer", "eines", "einen", "eine", "angebot",
    "heute", "derzeit", "aktuell", "gültig", "gueltig", "vorhanden",
    "erhalten", "können", "koennen", "man", "noch", "stadt", "gemeinde",
    "kommunale", "kommunal", "maßnahmen", "massnahmen", "durch", "dort",
    "for", "from", "what", "which", "does", "offer", "offers", "have", "has",
    "current", "currently", "available", "there", "any", "give", "provide",
    "grants", "grant", "subsidy", "subsidies", "support", "funding",
}) | _LEXICAL_STOPWORDS


def _lexical_normalize(value: str) -> str:
    # Normalize matching across German compounds and ASCII transliterations.
    folded = unicodedata.normalize("NFKD", str(value).casefold().replace("ß", "ss"))
    ascii_like = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[^\W_]+", ascii_like, flags=re.UNICODE))


def _lexical_has_phrase(haystack: str, needle: str) -> bool:
    n = _lexical_normalize(needle)
    if not n:
        return False
    if " " in n:
        return f" {n} " in f" {haystack} "
    # Allow inflections, but do not match 'balkon' within 'balkonpflanzen'.
    if len(n) <= 6:
        return bool(re.search(r"(?:^| )" + re.escape(n) + r"(?: |$)", haystack))
    return bool(re.search(r"(?:^| )" + re.escape(_lexical_stem(n)) + r"\w*(?: |$)", haystack))


def _lexical_fts_term(phrase: str) -> str:
    tokens = re.findall(r"[^\W_]+", str(phrase).casefold(), flags=re.UNICODE)
    if not tokens:
        return ""
    if len(tokens) == 1:
        stem = _lexical_stem(tokens[0])
        return f'"{stem}"*'
    return '"' + " ".join(tokens) + '"'


def _lexical_query_plans(question: str) -> tuple[list[tuple[str, str]], tuple[str, ...], str, str]:
    """Bounded FTS5 plans. Never AND all query synonyms into one requirement."""
    from crawler.core.question_intent import SupportType, extract_question_intent
    from crawler.core.retrieval_query_expansion import _POLICY_TERMS, _SUPPORT_TERMS

    intent = extract_question_intent(question)
    family = str(intent.policy_family or "")
    normalized_question = _lexical_normalize(question)
    if not family:
        # Do not infer a policy category merely from generic terms such as 'PV'.
        for possible, aliases in _POLICY_TERMS.items():
            specific = [a for a in aliases if len(_lexical_normalize(a)) >= 11]
            if any(_lexical_has_phrase(normalized_question, alias) for alias in specific):
                family = possible
                break

    aliases = tuple(dict.fromkeys(_POLICY_TERMS.get(family, ())))
    financial = intent.support_type == SupportType.FINANCIAL
    if not family and financial:
        # 'Förderung des Radverkehrs' can mean measures, not financial grants.
        financial = bool(re.search(r"\b(?:zuschuss|bezuschuss|fördermittel|foerdermittel|finanziell|subvention|grant|subsid)\w*\b", question.casefold()))
    support_aliases = tuple(_SUPPORT_TERMS.get(intent.support_type, ()))
    if family and not support_aliases and family.endswith("_subsidy"):
        financial = True
    raw_tokens = re.findall(r"[^\W_]+", question.casefold(), flags=re.UNICODE)
    municipality_tokens = set(re.findall(r"[^\W_]+", str(intent.municipality_name or "").casefold()))
    topic_tokens = tuple(dict.fromkeys(
        _lexical_stem(t) for t in raw_tokens
        if len(t) >= 5 and t not in _LEXICAL_QUESTION_WORDS
        and t not in municipality_tokens and not t.isdecimal()
        and not any(t.startswith(w) for w in ("förder", "foerder", "zuschuss", "bezuschuss"))
    ))[:12]

    concept_query = " OR ".join(dict.fromkeys(filter(None, map(_lexical_fts_term, aliases))))
    topical_query = " OR ".join(dict.fromkeys(filter(None, map(_lexical_fts_term, topic_tokens))))
    financial_query = " OR ".join(dict.fromkeys(filter(None, map(_lexical_fts_term, _LEXICAL_FINANCIAL_WORDS))))
    decision_query = " OR ".join(dict.fromkeys(filter(None, map(_lexical_fts_term, _LEXICAL_DECISION_WORDS))))
    if not financial and support_aliases:
        support_query = " OR ".join(dict.fromkeys(filter(None, map(_lexical_fts_term, support_aliases))))
    else:
        support_query = financial_query

    plans: list[tuple[str, str]] = []
    if concept_query:
        if support_query:
            plans.append(("concept_support", f"({concept_query}) AND ({support_query})"))
        plans.append(("concept_decision", f"({concept_query}) AND ({decision_query})"))
        plans.append(("concept", f"({concept_query})"))
    if topical_query:
        if support_query and not concept_query:
            plans.append(("topic_support", f"({topical_query}) AND ({support_query})"))
        plans.append(("topic", f"({topical_query})"))
    if not plans:
        plans.append(("base", _lexical_match_query(question)))
    seen: set[str] = set()
    unique = []
    for name, sql in plans:
        if sql not in seen:
            seen.add(sql)
            unique.append((name, sql))
    return unique[:5], aliases, family, "financial" if financial else "other"


def _lexical_is_probable_pdf_url(source_url: str) -> bool:
    """Conservative URL cue; getfile.php downloads frequently contain council PDFs."""
    parsed = urlsplit(source_url)
    path = parsed.path.casefold()
    return (
        path.endswith(".pdf")
        or path.endswith("/getfile.php")
        or (path.endswith("/loaddocument.phtml") and "pdf" in parsed.query.casefold())
    )


def _lexical_is_damaged_excerpt(text: str) -> bool:
    """Reject clearly clipped PDF fragments, never repair or invent source text.

    Short but complete official statements are allowed if their subject and action
    are directly evident; that decision is taken in _lexical_rank_candidate.
    """
    raw = str(text).strip()
    words = re.findall(r"[^\W_]+", raw, flags=re.UNICODE)
    if not words:
        return True
    if len(raw) < 45 or len(words) < 7:
        return True
    # Broken PDF reading orders often join the end of one line to the next.
    glued = len(re.findall(r"(?<=[a-zäöüß])\.(?=[a-zäöüß])", raw))
    if len(raw) < 200 and glued:
        return True
    if len(raw) < 180 and (raw.endswith((",", ":", "-")) or raw.startswith(("  ", "…"))):
        return True
    if len(words) >= 15:
        tiny_ratio = sum(len(w) <= 2 for w in words) / len(words)
        if tiny_ratio > 0.52:
            return True
    # Near-short PDF chunks often terminate mid-word, or splice several columns
    # into one incomplete clause. This is a conservative *short-text* gate.
    if len(raw) < 170:
        if raw.endswith((',', ':', '-', '…')):
            return True
        if re.search(r"\b\d{1,2}\.\d{1,2}\.\d{4}\w+", raw):
            return True
        if re.search(r"\b(?:seite|page)\s+\d+\s+von\s+\d+[a-zäöü]", raw, flags=re.I):
            return True
    return False


def _lexical_rank_candidate(
    item_text: str,
    heading: str | None,
    source_url: str,
    *,
    query: str,
    aliases: tuple[str, ...],
    family: str,
    support_kind: str,
    channels: dict[str, tuple[int, float]],
    query_city: str,
    query_terms: frozenset[str],
) -> tuple[float, bool]:
    """Evidence-quality gate and ranking; **never** convert retrieval to a verdict."""
    body = _lexical_normalize(item_text)
    heading_body = _lexical_normalize(heading or "")
    passage = f"{heading_body} {body}".strip()
    topic = sum(1 for term in aliases if _lexical_has_phrase(passage, term))
    body_topic = any(_lexical_has_phrase(body, term) for term in aliases)
    funding = any(_lexical_has_phrase(body, term) for term in _LEXICAL_FINANCIAL_WORDS)
    decision = any(_lexical_has_phrase(body, term) for term in _LEXICAL_DECISION_WORDS)
    if family and not topic:
        return -1e9, False
    if family and support_kind == "financial" and not (funding or decision):
        return -1e9, False

    pdf_like = _lexical_is_probable_pdf_url(source_url)
    damaged = _lexical_is_damaged_excerpt(item_text) if pdf_like else False
    # Short PDFs without a complete subject+decision pair can easily misattribute
    # "Der Antrag wird abgelehnt" to a different agenda item. Do not pass those
    # fragments to an answer generator as though they were decisive evidence.
    # Evidence with broken word order must not be saved by a heading/context
    # match: broken snippets may refer to another agenda motion entirely.
    if pdf_like and damaged:
        return -1e9, False
    if family and pdf_like and len(item_text.strip()) < 170:
        # For brief PDF excerpts, require the policy concept and relevant action
        # in the *body*, not just a heading or a neighboring agenda label.
        if not (body_topic and (funding or decision)):
            return -1e9, False

    parsed = urlsplit(source_url)
    host = str(parsed.hostname or "").casefold().removeprefix("www.")
    city_host = bool(query_city and query_city.replace(" ", "") in host.replace("-", ""))
    council = any(s in (parsed.path or "").casefold()
                  for s in ("/sessionnet/", "/ratsinfo/", "/sitzung", "/beschluss"))
    marketing = bool(re.search(
        r"\b(?:jetzt kaufen|bestellen|shop|kooperationspartner|"
        r"mehrwertsteuer|wlan funktion|partnerangebot|"
        r"mini pv anlage aussuchen|rabatt|kostenlos bestellen)\b", body
    ))
    if family and support_kind == "financial" and marketing and not city_host:
        # Product pricing and national VAT rules do not show a city subsidy.
        return -1e9, False

    overlap = sum(bool(re.search(r"(?:^| )" + re.escape(w) + r"\w*(?: |$)", passage))
                  for w in query_terms)
    score = 2.0 * min(6, overlap)
    score += (20.0 if topic else 0.0) + 2.0 * min(3, topic)
    score += (15.0 if funding and support_kind == "financial" else 0.0)
    score += 9.0 if decision else 0.0
    score += 10.0 if city_host else 0.0
    score += 7.0 if council else 0.0
    score += 5.0 if body_topic else 0.0
    if pdf_like and len(item_text.strip()) < 140:
        score -= 8.0
    if family and support_kind == "financial" and not city_host:
        # Still keep relevant operator evidence for provider differentiation,
        # but it must not outrank official municipal decisions by default.
        score -= 13.0
    if re.search(r"(?:^| )(?:tagesordnung|beratung|antrag)(?: |$)", body) and not decision:
        score -= 4.0
    if "concept_support" in channels:
        score += 4.0
    if "topic_support" in channels:
        score += 2.0
    if "concept_decision" in channels:
        score += 2.0
    score += min(3.0, sum(12.0 / (12.0 + rank) for rank, _ in channels.values()))
    return score, True


def _lexical_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    lexical_database: Path,
) -> list[RetrievedEvidence]:
    """Multi-query, citation-preserving FTS retrieval with policy-aware reranking.

    All candidate IDs are resolved against the validated immutable snapshot.
    Retrieval scores rank *evidence*, never claim a policy is currently active.
    """
    plans, aliases, family, support_kind = _lexical_query_plans(request.query)
    lexical = _open_lexical_read_only(lexical_database)
    try:
        _validate_lexical_index(lexical, binding)
        municipality_clause = ""
        municipality_params: list[Any] = []
        if request.municipality_ids:
            placeholders = ",".join("?" for _ in request.municipality_ids)
            municipality_clause = f" AND municipality_id IN ({placeholders})"
            municipality_params = list(request.municipality_ids)
        per_plan = min(500, max(200, request.top_k * 12))
        pool: dict[str, dict[str, Any]] = {}
        for channel, match_query in plans:
            rows = lexical.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS score "
                "FROM chunks_fts WHERE chunks_fts MATCH ?"
                f"{municipality_clause} ORDER BY score, chunk_id LIMIT ?",
                (match_query, *municipality_params, per_plan),
            ).fetchall()
            for rank, (chunk_id, raw_score) in enumerate(rows, 1):
                cid = str(chunk_id)
                record = pool.setdefault(cid, {"channels": {}})
                record["channels"][channel] = (rank, float(raw_score))
    finally:
        lexical.close()
    if not pool:
        return []

    from crawler.core.question_intent import extract_question_intent
    intent = extract_question_intent(request.query)
    query_city = _lexical_normalize(str(intent.municipality_name or ""))
    city_tokens = set(query_city.split())
    query_terms = frozenset(
        _lexical_stem(t) for t in _lexical_normalize(request.query).split()
        if len(t) >= 5 and t not in _LEXICAL_QUESTION_WORDS and t not in city_tokens
    )
    with connect_existing_read_only(binding.database) as connection:
        candidates: list[tuple[float, str, RetrievedEvidence]] = []
        for chunk_id, record in pool.items():
            row = connection.execute(
                "SELECT c.chunk_id, c.segment_id, c.document_id, c.text, "
                "c.heading_context, c.chunk_hash, d.url_canonical "
                "FROM chunks c JOIN documents_raw d ON d.document_id=c.document_id "
                "WHERE c.chunk_id=?",
                (chunk_id,),
            ).fetchone()
            if row is None:
                raise RetrievalError(
                    f"Lexical index returned chunk {chunk_id} absent from the bound snapshot"
                )
            canonical_chunk_id, segment_id, document_id, text, heading, stored_hash, url = row
            if segment_id is None:
                raise RetrievalError(f"Chunk {canonical_chunk_id} lacks its required parent segment")
            content_hash = _sha256(str(text))
            if stored_hash and str(stored_hash) != content_hash:
                raise RetrievalError(
                    f"Stored content hash disagrees with chunk text for {canonical_chunk_id}"
                )
            municipalities = _required_municipalities(connection, str(document_id))
            if request.municipality_ids and not set(municipalities).intersection(request.municipality_ids):
                raise RetrievalError(
                    f"Lexical index returned out-of-scope municipality evidence {canonical_chunk_id}"
                )
            validated_url = _validated_source_url(url, str(document_id))
            score, acceptable = _lexical_rank_candidate(
                str(text), str(heading) if heading is not None else None,
                validated_url, query=request.query, aliases=aliases,
                family=family, support_kind=support_kind, channels=record["channels"],
                query_city=query_city, query_terms=query_terms,
            )
            if not acceptable:
                continue
            candidate = RetrievedEvidence(
                database_snapshot_id=str(binding.database.snapshot_id),
                evidence_id=evidence_id_for("chunk", str(canonical_chunk_id), content_hash),
                source_type="chunk",
                source_id=str(canonical_chunk_id),
                segment_id=str(segment_id),
                chunk_id=str(canonical_chunk_id),
                document=DocumentSource(
                    document_id=str(document_id), source_url=validated_url,
                    heading_context=str(heading) if heading is not None else None,
                    municipality_ids=municipalities,
                ),
                evidence_text=str(text), extraction_run_ids=(), graph_artifact_ids=(),
                assertions=(), retrieval_traces=(), retrieval_score=score, rank=0,
            )
            candidates.append((score, candidate.evidence_id, candidate))

    candidates.sort(key=lambda row: (-row[0], row[1]))
    selected: list[RetrievedEvidence] = []
    seen_text: set[str] = set()
    per_document: defaultdict[str, int] = defaultdict(int)
    for score, _evidence_id, item in candidates:
        normalized = _lexical_normalize(item.evidence_text)
        # Deduplicate *substantive* equivalent passages across different URLs.
        fingerprint = _sha256(normalized) if len(normalized) >= 80 else item.evidence_id
        if fingerprint in seen_text:
            continue
        if per_document[item.document.document_id] >= (3 if support_kind == "financial" else 2):
            continue
        seen_text.add(fingerprint)
        per_document[item.document.document_id] += 1
        rank = len(selected) + 1
        selected.append(replace(
            item, rank=rank, retrieval_score=score,
            retrieval_traces=(RetrievalTrace("lexical", rank, score),),
        ))
        if len(selected) >= request.top_k:
            break
    return selected


def _semantic_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    embedder: QueryEmbedder,
) -> tuple[list[RetrievedEvidence], str]:
    from crawler.core.question_intent import extract_question_intent, FORMAL_POLICY_PATTERNS, formal_policy_text_score
    family = extract_question_intent(request.query).policy_family
    formal = family in FORMAL_POLICY_PATTERNS
    with connect_existing_read_only(binding.database) as connection:
        artifact_id, corpus = _select_embedding_artifact(connection, binding, embedder.identity)
        query_vector = np.asarray(embedder.embed_query(request.query), dtype=np.float32)
        if query_vector.ndim != 1 or len(query_vector) != embedder.identity.vector_dimension:
            raise EmbeddingLineageError("Query embedding dimension disagrees with its identity")
        norm = float(np.linalg.norm(query_vector))
        if not math.isfinite(norm) or norm == 0:
            raise EmbeddingLineageError("Query embedding must contain a finite non-zero vector")
        query_vector = query_vector / norm

        content_hashes = {chunk_id: content_hash for chunk_id, content_hash, _ in corpus}
        municipality_clause = ""
        parameters: list[Any] = [artifact_id]
        if request.municipality_ids:
            placeholders = ",".join("?" for _ in request.municipality_ids)
            municipality_clause = (
                " AND EXISTS (SELECT 1 FROM municipality_documents md "
                "WHERE md.document_id=c.document_id "
                f"AND md.municipality_id IN ({placeholders}))"
            )
            parameters.extend(request.municipality_ids)
        rows = connection.execute(
            f"""
            SELECT c.chunk_id, c.segment_id, c.document_id, c.text, c.heading_context,
                   d.url_canonical, ce.source_content_hash, ce.vector_dimension, ce.embedding
            FROM chunk_embeddings ce
            JOIN chunks c ON c.chunk_id=ce.chunk_id
            JOIN documents_raw d ON d.document_id=c.document_id
            WHERE ce.artifact_id=?{municipality_clause}
            ORDER BY c.chunk_id
            """,
            tuple(parameters),
        ).fetchall()
        scored: list[tuple[float, tuple[Any, ...]]] = []
        for row in rows:
            chunk_id = str(row[0])
            if str(row[6]) != content_hashes.get(chunk_id):
                raise EmbeddingLineageError(f"Embedding content hash mismatch for chunk {chunk_id}")
            if int(row[7]) != embedder.identity.vector_dimension:
                raise EmbeddingLineageError("Embedding artifact mixes vector dimensions")
            vector = blob_to_vector(row[8], embedder.identity.vector_dimension)
            vector_norm = float(np.linalg.norm(vector))
            if not math.isfinite(vector_norm) or vector_norm == 0:
                raise EmbeddingLineageError(f"Chunk {chunk_id} has an invalid embedding")
            score = float((vector / vector_norm) @ query_vector)
            if formal:
                # Same immutable embedding corpus, with a transparent bounded
                # policy-relevance rerank. No lexical or graph retrieval here.
                score += 0.08 * formal_policy_text_score(str(row[3]), family)
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1][0])))

        if formal:
            # Prevent duplicated excerpts from consuming the evidence budget.
            unique, seen = [], set()
            for entry in scored:
                key = _lexical_normalize(str(entry[1][3]))
                if key not in seen:
                    unique.append(entry)
                    seen.add(key)
            scored = unique

        result: list[RetrievedEvidence] = []
        for rank, (score, row) in enumerate(scored[: request.top_k], start=1):
            chunk_id, segment_id, document_id, text, heading, url = row[:6]
            if segment_id is None:
                raise RetrievalError(f"Chunk {chunk_id} lacks its required parent segment")
            content_hash = content_hashes[str(chunk_id)]
            result.append(
                RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id_for("chunk", str(chunk_id), content_hash),
                    source_type="chunk",
                    source_id=str(chunk_id),
                    segment_id=str(segment_id),
                    chunk_id=str(chunk_id),
                    document=DocumentSource(
                        document_id=str(document_id),
                        source_url=_validated_source_url(url, str(document_id)),
                        heading_context=str(heading) if heading is not None else None,
                        municipality_ids=_required_municipalities(connection, str(document_id)),
                    ),
                    evidence_text=str(text),
                    extraction_run_ids=(),
                    graph_artifact_ids=(),
                    assertions=(),
                    retrieval_traces=(RetrievalTrace("semantic", rank, score),),
                    retrieval_score=score,
                    rank=rank,
                )
            )
    return result, artifact_id


_GRAPH_SCOPE_QUERY = """
MATCH (p:Projection {projection_id: $projection_id})
RETURN p.projection_id AS projection_id, p.snapshot_id AS snapshot_id,
       p.run_ids_json AS run_ids_json, p.schema_version AS schema_version
LIMIT 2
"""

_GRAPH_RETRIEVAL_QUERY = """
MATCH (projection:Projection {projection_id: $projection_id})-[:INCLUDES_RUN]->
      (run:ExtractionRun {projection_id: $projection_id})-[:PRODUCED]->
      (artifact:GraphArtifact {projection_id: $projection_id})
MATCH (artifact)-[:HAS_ASSERTION]->(assertion:Assertion {projection_id: $projection_id})
MATCH (artifact)-[:USES_EVIDENCE]->(evidence:Evidence {projection_id: $projection_id})
MATCH (assertion)-[:SUPPORTED_BY]->(evidence)
MATCH (assertion)-[:SUBJECT|OBJECT]->(entity:Entity {projection_id: $projection_id})
WHERE any(term IN $terms WHERE toLower(entity.name) CONTAINS term)
  AND assertion.extraction_run_id = run.run_id
  AND artifact.extraction_run_id = run.run_id
MATCH (assertion)-[:SUBJECT]->(subject:Entity {projection_id: $projection_id})
MATCH (assertion)-[:OBJECT]->(object:Entity {projection_id: $projection_id})
RETURN DISTINCT assertion.assertion_id AS assertion_id,
       run.run_id AS extraction_run_id,
       assertion.relation_type AS relation_type,
       assertion.evidence_excerpt AS evidence_excerpt,
       artifact.artifact_id AS graph_artifact_id,
       evidence.evidence_id AS evidence_id,
       evidence.source_type AS source_type, evidence.source_id AS source_id,
       evidence.segment_id AS segment_id, evidence.chunk_id AS chunk_id,
       evidence.document_id AS document_id, evidence.source_url AS source_url,
       subject.entity_id AS subject_id, subject.name AS subject_name,
       object.entity_id AS object_id, object.name AS object_name
ORDER BY assertion.assertion_id, evidence.evidence_id
LIMIT $candidate_limit
"""


_MENTION_RETRIEVAL_QUERY = """
MATCH (projection:Projection {projection_id: $projection_id})-[:INCLUDES_RUN]-      (run:ExtractionRun {projection_id: $projection_id})-[:PRODUCED]-      (artifact:GraphArtifact {projection_id: $projection_id})
MATCH (artifact)-[:HAS_MENTION]->(mention:EntityMention {projection_id: $projection_id})-[:REFERS_TO]->(entity:Entity {projection_id: $projection_id})
MATCH (mention)-[:SUPPORTED_BY]->(evidence:Evidence {projection_id: $projection_id})
WHERE any(term IN $terms WHERE toLower(entity.name) CONTAINS term OR toLower(mention.observed_name) CONTAINS term)
  AND mention.extraction_run_id = run.run_id
  AND artifact.extraction_run_id = run.run_id
RETURN DISTINCT mention.mention_id AS mention_id, run.run_id AS extraction_run_id,
       artifact.artifact_id AS graph_artifact_id, evidence.evidence_id AS evidence_id,
       evidence.source_type AS source_type, evidence.source_id AS source_id,
       evidence.segment_id AS segment_id, evidence.chunk_id AS chunk_id,
       evidence.document_id AS document_id, evidence.source_url AS source_url,
       entity.entity_id AS entity_id, entity.name AS entity_name
ORDER BY mention.mention_id, evidence.evidence_id
LIMIT $candidate_limit
"""


def _query_terms(query: str) -> list[str]:
    return sorted({term.lower() for term in re.findall(r"[\wäöüß-]+", query) if len(term) >= 3})


def _validate_graph_scope(
    backend: GraphReadBackend,
    binding: SnapshotBinding,
    request: RetrievalRequest,
    timeout_seconds: float,
) -> tuple[str, ...]:
    rows = backend.read(
        _GRAPH_SCOPE_QUERY,
        {"projection_id": request.projection_id},
        timeout_seconds=timeout_seconds,
    )
    if len(rows) != 1:
        raise ProjectionMismatchError("Selected Neo4j projection is missing or ambiguous")
    row = rows[0]
    if row.get("projection_id") != request.projection_id:
        raise ProjectionMismatchError("Neo4j returned a different projection identity")
    if row.get("snapshot_id") != binding.database.snapshot_id:
        raise ProjectionMismatchError("Neo4j projection belongs to a different snapshot")
    try:
        run_ids = tuple(sorted(str(value) for value in json.loads(str(row["run_ids_json"]))))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProjectionMismatchError("Neo4j projection run scope is malformed") from exc
    if not run_ids:
        raise ProjectionMismatchError("Neo4j projection has an empty extraction-run scope")
    if row.get("schema_version") != 1:
        raise ProjectionMismatchError("Neo4j projection schema version is unsupported")
    expected = tuple(sorted(request.extraction_run_ids))
    if expected and run_ids != expected:
        raise ProjectionMismatchError("Neo4j projection extraction-run scope does not match")
    return run_ids


def _authoritative_evidence(
    connection: sqlite3.Connection,
    evidence_id: str,
    selected_runs: tuple[str, ...],
) -> tuple[Any, ...]:
    placeholders = ",".join("?" for _ in selected_runs)
    rows = connection.execute(
        f"""
        SELECT e.source_type, e.source_id, e.segment_id, e.chunk_id, e.document_id,
               e.source_url, e.evidence_text, g.extraction_run_id
        FROM evidence_snapshots e
        JOIN graph_triplets g ON g.evidence_id=e.evidence_id
        WHERE e.evidence_id=? AND g.provenance_status='verified'
          AND g.extraction_run_id IN ({placeholders})
        ORDER BY g.extraction_run_id
        """,
        (evidence_id, *selected_runs),
    ).fetchall()
    if not rows:
        raise ProjectionMismatchError(
            f"Neo4j evidence {evidence_id} cannot be resolved to verified SQLite evidence"
        )
    canonical = rows[0][:7]
    if any(row[:7] != canonical for row in rows[1:]):
        raise ProjectionMismatchError(f"SQLite evidence {evidence_id} has conflicting identities")
    return (*canonical, tuple(str(row[7]) for row in rows))


def _evidence_municipalities(connection: sqlite3.Connection, evidence_id: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT municipality_id FROM evidence_municipalities "
        "WHERE evidence_id=? ORDER BY municipality_id",
        (evidence_id,),
    ).fetchall()
    municipalities = tuple(str(row[0]) for row in rows)
    if not municipalities:
        raise ProjectionMismatchError(
            f"SQLite evidence {evidence_id} lacks immutable municipality provenance"
        )
    return municipalities


def _source_heading(
    connection: sqlite3.Connection, source_type: str, source_id: str, segment_id: str
) -> str | None:
    table, column, value = (
        ("chunks", "chunk_id", source_id)
        if source_type == "chunk"
        else ("segments", "segment_id", segment_id)
    )
    row = connection.execute(
        f"SELECT heading_context FROM {table} WHERE {column}=?", (value,)
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _mention_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    backend: GraphReadBackend,
    timeout_seconds: float,
) -> list[RetrievedEvidence]:
    selected_runs = _validate_graph_scope(backend, binding, request, timeout_seconds)
    terms = _query_terms(request.query)
    if not terms:
        return []

    candidate_limit = min(MAX_GRAPH_CANDIDATES, max(request.top_k * 20, request.top_k))
    rows = backend.read(
        _MENTION_RETRIEVAL_QUERY,
        {
            "projection_id": request.projection_id,
            "terms": terms,
            "candidate_limit": candidate_limit,
        },
        timeout_seconds=timeout_seconds,
    )
    if len(rows) > candidate_limit:
        raise RetrievalError("Neo4j backend exceeded the application-owned result cap")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id:
            raise ProjectionMismatchError("Neo4j mention result omitted evidence identity")
        grouped[evidence_id].append(row)

    ranked: list[tuple[int, RetrievedEvidence]] = []
    with connect_existing_read_only(binding.database) as connection:
        for evidence_id in sorted(grouped):
            source_type, source_id, segment_id, chunk_id, document_id, url, text, evidence_runs = (
                _authoritative_evidence(connection, evidence_id, selected_runs)
            )
            municipalities = _evidence_municipalities(connection, evidence_id)
            if request.municipality_ids and not set(request.municipality_ids).intersection(
                municipalities
            ):
                continue

            expected_identity = {
                "source_type": str(source_type),
                "source_id": str(source_id),
                "segment_id": str(segment_id),
                "chunk_id": str(chunk_id) if chunk_id is not None else None,
                "document_id": str(document_id),
                "source_url": str(url),
            }
            unique_mentions: dict[str, Mapping[str, Any]] = {}
            artifact_ids: set[str] = set()
            extraction_run_ids: set[str] = set()

            for row in grouped[evidence_id]:
                mention_id = str(row.get("mention_id") or "")
                if not mention_id:
                    raise ProjectionMismatchError("Neo4j mention omitted its stable identity")
                if mention_id in unique_mentions and row != unique_mentions[mention_id]:
                    raise ProjectionMismatchError(
                        f"Neo4j mention {mention_id} has conflicting result records"
                    )
                unique_mentions[mention_id] = row

                actual_identity = {key: row.get(key) for key in expected_identity}
                if actual_identity != expected_identity:
                    raise ProjectionMismatchError(
                        f"Neo4j evidence {evidence_id} disagrees with authoritative SQLite"
                    )

                run_id = str(row.get("extraction_run_id") or "")
                if run_id not in selected_runs or run_id not in evidence_runs:
                    raise ProjectionMismatchError(
                        "Neo4j mention falls outside verified projection scope"
                    )

                required_fields = (
                    "graph_artifact_id",
                    "entity_id",
                    "entity_name",
                )
                if any(not str(row.get(field) or "").strip() for field in required_fields):
                    raise ProjectionMismatchError(
                        f"Neo4j mention {mention_id} has incomplete identities"
                    )

                expected_artifact_id = graph_artifact_id_for(
                    run_id, str(source_type), str(source_id)
                )
                if row.get("graph_artifact_id") != expected_artifact_id:
                    raise ProjectionMismatchError(
                        "Neo4j graph artifact identity disagrees with authoritative SQLite scope"
                    )

                artifact_ids.add(expected_artifact_id)
                extraction_run_ids.add(run_id)

            item = RetrievedEvidence(
                database_snapshot_id=str(binding.database.snapshot_id),
                evidence_id=evidence_id,
                source_type=str(source_type),
                source_id=str(source_id),
                segment_id=str(segment_id),
                chunk_id=str(chunk_id) if chunk_id is not None else None,
                document=DocumentSource(
                    document_id=str(document_id),
                    source_url=_validated_source_url(url, str(document_id)),
                    heading_context=_source_heading(
                        connection, str(source_type), str(source_id), str(segment_id)
                    ),
                    municipality_ids=municipalities,
                ),
                evidence_text=str(text),
                extraction_run_ids=tuple(sorted(extraction_run_ids)),
                graph_artifact_ids=tuple(sorted(artifact_ids)),
                assertions=(),
                retrieval_traces=(),
                retrieval_score=0.0,
                rank=0,
            )
            ranked.append((len(unique_mentions), item))

    ranked.sort(key=lambda pair: (-pair[0], pair[1].evidence_id))
    return [
        replace(
            item,
            rank=rank,
            retrieval_score=1.0 / rank,
            retrieval_traces=(RetrievalTrace("mention", rank, 1.0 / rank),),
        )
        for rank, (_, item) in enumerate(ranked[: request.top_k], start=1)
    ]


def _graph_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    backend: GraphReadBackend,
    timeout_seconds: float,
) -> list[RetrievedEvidence]:
    selected_runs = _validate_graph_scope(backend, binding, request, timeout_seconds)
    terms = _query_terms(request.query)
    if not terms:
        return []
    candidate_limit = min(MAX_GRAPH_CANDIDATES, max(request.top_k * 20, request.top_k))
    rows = backend.read(
        _GRAPH_RETRIEVAL_QUERY,
        {
            "projection_id": request.projection_id,
            "terms": terms,
            "candidate_limit": candidate_limit,
        },
        timeout_seconds=timeout_seconds,
    )
    if len(rows) > candidate_limit:
        raise RetrievalError("Neo4j backend exceeded the application-owned result cap")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id:
            raise ProjectionMismatchError("Neo4j graph result omitted evidence identity")
        grouped[evidence_id].append(row)

    result: list[RetrievedEvidence] = []
    with connect_existing_read_only(binding.database) as connection:
        for evidence_id in sorted(grouped):
            source_type, source_id, segment_id, chunk_id, document_id, url, text, runs = (
                _authoritative_evidence(connection, evidence_id, selected_runs)
            )
            municipalities = _evidence_municipalities(connection, evidence_id)
            if request.municipality_ids and not set(request.municipality_ids).intersection(municipalities):
                continue
            unique_rows: dict[str, Mapping[str, Any]] = {}
            for row in grouped[evidence_id]:
                assertion_id = str(row.get("assertion_id") or "")
                if not assertion_id:
                    raise ProjectionMismatchError("Neo4j assertion omitted its stable identity")
                if assertion_id in unique_rows and row != unique_rows[assertion_id]:
                    raise ProjectionMismatchError(
                        f"Neo4j assertion {assertion_id} has conflicting result records"
                    )
                unique_rows[assertion_id] = row
            graph_rows = [unique_rows[key] for key in sorted(unique_rows)]
            expected_identity = {
                "source_type": str(source_type),
                "source_id": str(source_id),
                "segment_id": str(segment_id),
                "chunk_id": str(chunk_id) if chunk_id is not None else None,
                "document_id": str(document_id),
                "source_url": str(url),
            }
            for row in graph_rows:
                actual_identity = {key: row.get(key) for key in expected_identity}
                if actual_identity != expected_identity:
                    raise ProjectionMismatchError(
                        f"Neo4j evidence {evidence_id} disagrees with authoritative SQLite"
                    )
                if str(row.get("extraction_run_id")) not in selected_runs:
                    raise ProjectionMismatchError("Neo4j assertion falls outside projection scope")
                if str(row.get("extraction_run_id")) not in runs:
                    raise ProjectionMismatchError(
                        "Neo4j assertion run has no verified SQLite graph artifact"
                    )
                try:
                    expected_artifact_id = graph_artifact_id_for(
                        str(row.get("extraction_run_id")), str(source_type), str(source_id)
                    )
                except ProjectionScopeError as exc:
                    raise ProjectionMismatchError(
                        f"SQLite evidence {evidence_id} has an invalid graph artifact scope"
                    ) from exc
                if row.get("graph_artifact_id") != expected_artifact_id:
                    raise ProjectionMismatchError(
                        "Neo4j graph artifact identity disagrees with authoritative SQLite scope"
                    )
                required_graph_fields = (
                    "graph_artifact_id",
                    "subject_id",
                    "subject_name",
                    "relation_type",
                    "object_id",
                    "object_name",
                )
                if any(not str(row.get(field) or "").strip() for field in required_graph_fields):
                    raise ProjectionMismatchError(
                        f"Neo4j assertion {row['assertion_id']} has incomplete identities"
                    )
            assertions = tuple(
                GraphAssertion(
                    assertion_id=str(row["assertion_id"]),
                    graph_artifact_id=str(row["graph_artifact_id"]),
                    extraction_run_id=str(row["extraction_run_id"]),
                    subject_id=str(row["subject_id"]),
                    subject_name=str(row["subject_name"]),
                    relation_type=str(row["relation_type"]),
                    object_id=str(row["object_id"]),
                    object_name=str(row["object_name"]),
                    evidence_excerpt=(
                        str(row["evidence_excerpt"])
                        if row.get("evidence_excerpt") is not None
                        else None
                    ),
                )
                for row in graph_rows
            )
            artifact_ids = tuple(sorted({item.graph_artifact_id for item in assertions}))
            result.append(
                RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id,
                    source_type=str(source_type),
                    source_id=str(source_id),
                    segment_id=str(segment_id),
                    chunk_id=str(chunk_id) if chunk_id is not None else None,
                    document=DocumentSource(
                        document_id=str(document_id),
                        source_url=_validated_source_url(url, str(document_id)),
                        heading_context=_source_heading(
                            connection, str(source_type), str(source_id), str(segment_id)
                        ),
                        municipality_ids=municipalities,
                    ),
                    evidence_text=str(text),
                    extraction_run_ids=tuple(
                        sorted({item.extraction_run_id for item in assertions})
                    ),
                    graph_artifact_ids=artifact_ids,
                    assertions=assertions,
                    retrieval_traces=(),
                    retrieval_score=0.0,
                    rank=0,
                )
            )
    result.sort(
        key=lambda item: (
            -len(item.assertions),
            item.evidence_id,
        )
    )
    return [
        replace(
            item,
            rank=rank,
            retrieval_score=1.0 / rank,
            retrieval_traces=(RetrievalTrace("graph", rank, 1.0 / rank),),
        )
        for rank, item in enumerate(result[: request.top_k], start=1)
    ]


_REVIEWED_POLICY_TABLES = (
    "_graph_comparison_origin", "source_graph_build_metadata",
    "source_graph_nodes", "source_graph_edges", "source_policy_schemes",
    "source_policy_edges", "chunks", "documents_raw",
)


def _reviewed_policy_question_match(
    query: str, facts: Sequence[Mapping[str, Any]], scheme_name: str
) -> int:
    """Query-based evidence selection; never infer a YES/NO policy verdict.

    Match specific policy concepts against reviewed relationship targets, not
    generic words such as funding or an arbitrary document URL. Reuse the
    existing question-intent vocabulary for German/English concept aliases.
    """
    _plans, aliases, family, _support = _lexical_query_plans(query)
    normalized_query = _lexical_normalize(query)
    normalized_scheme = _lexical_normalize(scheme_name)
    targets = [_lexical_normalize(str(fact["target"])) for fact in facts
               if fact["predicate"] in {"FUNDS", "EXCLUDES", "CONDITION", "RATE",
                                        "IDENTIFIED_AS", "ADOPTED", "IMPLEMENTED",
                                        "PROPOSED", "TERMINATED"}]
    if not targets:
        return 0
    # Restrict alias matching to a known, question-derived policy family.
    if family and aliases:
        if any(_lexical_has_phrase(target, alias) for target in targets for alias in aliases):
            return 10
        if any(_lexical_has_phrase(normalized_scheme, alias) for alias in aliases):
            return 5
        return 0
    from crawler.core.question_intent import extract_question_intent
    intent = extract_question_intent(query)
    city_tokens = set(_lexical_normalize(str(intent.municipality_name or "")).split())
    generic_programme_tokens = {
        "programm", "programme", "program", "programs", "foerderprogramm",
        "foerderprogramme", "foerderung", "foerderungen", "funding",
        "subsidy", "subsidies", "policy", "policies", "massnahme",
        "massnahmen", "unterstuetzung", "unterstuetzungen",
    }
    terms = [token for token in normalized_query.split()
             if len(token) >= 4 and token not in _LEXICAL_QUESTION_WORDS
             and token not in city_tokens and token not in generic_programme_tokens
             and not token.isdigit()]
    # Generic programme questions can retrieve all reviewed schemes in scope.
    if not terms:
        return 1
    count = sum(1 for token in terms
                if any(_lexical_has_phrase(target, token) for target in targets))
    return 2 * count if count else 0


def _structural_sqlite_graph_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    structural_database: Path,
) -> list[RetrievedEvidence]:
    """Traverse existing source-provenance topic graph, not policy assertions.

    Score candidate *passages*, never interpret graph topic/action cues as
    proved YES/NO, municipal issuer, adoption, funding or programme status.
    Strictly read-only and bound to the already frozen source snapshot.
    """
    if not request.municipality_ids:
        raise RetrievalError("Structural graph retrieval requires municipality scope")
    if not structural_database.is_file():
        raise RetrievalError(f"Structural graph database missing: {structural_database}")
    manifest_snapshot = binding.manifest.get("snapshot")
    expected_sha = str((manifest_snapshot or {}).get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise RetrievalError("Frozen snapshot manifest is missing its SHA256")

    # Reuse the existing predeclared city/topic patterns and sentence-local cue
    # detector. Do not train new query rules from the frozen gold answers.
    from crawler.scripts import extract_graph_ollama as source_graph
    from crawler.core.question_intent import extract_question_intent, FORMAL_POLICY_PATTERNS, formal_policy_text_score
    family = extract_question_intent(request.query).policy_family
    formal = family in FORMAL_POLICY_PATTERNS
    topics = tuple(sorted(topic for topic, pattern in source_graph._SOURCE_GRAPH_PATTERNS.items()
                          if pattern.search(request.query)))
    if formal:
        # The frozen graph has topic/provenance edges, not verified plan
        # adoption relations. Search the existing municipality's topic paths
        # for the requested instrument, without pretending new edges exist.
        topics = tuple(sorted(source_graph._SOURCE_GRAPH_PATTERNS))
    if not topics:
        return []
    terms = tuple(word for word in _lexical_normalize(request.query).split()
                  if len(word) >= 4 and word not in _LEXICAL_QUESTION_WORDS)

    with connect_existing_read_only(binding.database) as source:
        graph = sqlite3.connect(structural_database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            graph.execute("PRAGMA query_only=ON")
            graph.execute("BEGIN")
            origin = graph.execute(
                "SELECT source_sha256, selection_version FROM _graph_comparison_origin"
            ).fetchall()
            metadata = graph.execute(
                "SELECT version, frozen_source_sha256, node_count, edge_count "
                "FROM source_graph_build_metadata"
            ).fetchall()
            if origin != [(expected_sha, "balanced-policy-v1")]:
                raise ProjectionMismatchError("Structural graph copy has different frozen origin")
            if (len(metadata) != 1 or metadata[0][:2] !=
                ("source-provenance-topic-v1", expected_sha) or
                min(int(metadata[0][2]), int(metadata[0][3])) < 1):
                raise ProjectionMismatchError("Source-provenance topic graph is absent or mismatched")

            best: dict[str, tuple[float, tuple[Any, ...], str]] = {}
            sql = """
                SELECT c.chunk_id,c.segment_id,d.document_id,d.url_canonical,
                       c.heading_context,c.text,COALESCE(c.impact_score,0),
                       topic_edge.proof_start,topic_edge.proof_end,
                       topic_edge.proof_text,topic_edge.proof_sha256
                FROM source_graph_edges AS topic_edge
                JOIN source_graph_edges AS parent_edge
                    ON parent_edge.src=topic_edge.dst
                   AND parent_edge.predicate='FROM_DOCUMENT'
                JOIN source_graph_edges AS owner_edge
                    ON owner_edge.src=parent_edge.dst
                   AND owner_edge.predicate='ASSIGNED_TO'
                JOIN chunks AS c ON topic_edge.dst='chunk:'||c.chunk_id
                JOIN documents_raw AS d
                    ON parent_edge.dst='document:'||d.document_id
                WHERE topic_edge.src=? AND topic_edge.predicate='MENTIONED_IN'
                  AND owner_edge.dst=? AND c.document_id=d.document_id
                ORDER BY c.chunk_id
            """
            for city in sorted(set(request.municipality_ids)):
                if city not in source_graph.COMPARISON_MUNICIPALITIES:
                    raise RetrievalError(f"Unrecognized city for structural graph: {city}")
                for topic in topics:
                    for row in graph.execute(sql, (f"topic:{topic}", f"municipality:{city}")):
                        (cid, sid, did, url, heading, body, impact,
                         start, end, proof, digest) = row
                        if not isinstance(body, str) or not isinstance(proof, str) or (
                            not isinstance(start, int) or not isinstance(end, int) or
                            not 0 <= start < end <= len(body) or body[start:end] != proof or
                            _sha256(body) != digest
                        ):
                            raise ProjectionMismatchError(f"Broken graph source proof: {cid}")
                        norm = _lexical_normalize(body + " " + str(heading or ""))
                        formal_score = formal_policy_text_score(body, family) if formal else 0
                        if formal and not formal_score:
                            continue
                        overlap = sum(_lexical_has_phrase(norm, term) for term in terms)
                        cue = source_graph._source_graph_policy_signal(body, topic)
                        # Provenance topic path is sufficient for retrieval, never for a verdict.
                        # Prefer a local action cue and question-term overlap; no gold labels.
                        score = float(2 * min(8, overlap) + (10 + cue[0] if cue else 0)
                                      + min(float(impact or 0), 50) / 100)
                        if formal:
                            # Prefer the requested policy and its action cue,
                            # rather than an unrelated funding cue on the path.
                            score = float(12 * formal_score + min(8, overlap)
                                          + min(float(impact or 0), 50) / 100)
                        key = str(cid)
                        previous = best.get(key)
                        if previous is None or score > previous[0]:
                            best[key] = (score, row, city)

            ordered = sorted(best.values(), key=lambda item: (-item[0], str(item[1][0])))
            if formal:
                # Identical excerpts on mirrored URLs must not occupy
                # separate slots. Keep the strongest path and its provenance.
                unique, seen_text = [], set()
                for entry in ordered:
                    key = ((urlsplit(str(entry[1][3])).hostname or '').lower().removeprefix('www.'),
                           _lexical_normalize(str(entry[1][5])))
                    if key not in seen_text:
                        unique.append(entry)
                        seen_text.add(key)
                ordered = unique
            # Prefer distinct documents; use at most top_k chunks at this stage.
            selected: list[tuple[float, tuple[Any, ...], str]] = []
            used_docs: set[str] = set()
            for entry in ordered:
                doc_id = str(entry[1][2])
                if doc_id in used_docs:
                    continue
                selected.append(entry)
                used_docs.add(doc_id)
                if len(selected) >= request.top_k:
                    break
            if len(selected) < request.top_k:
                used_ids = {str(item[1][0]) for item in selected}
                for entry in ordered:
                    if str(entry[1][0]) in used_ids:
                        continue
                    selected.append(entry)
                    if len(selected) >= request.top_k:
                        break

            output: list[RetrievedEvidence] = []
            for rank, (score, row, city) in enumerate(selected, 1):
                cid, sid, did, url, heading, body = row[:6]
                authoritative = source.execute(
                    "SELECT c.chunk_id,c.segment_id,c.document_id,c.text,c.heading_context,"
                    "d.url_canonical FROM chunks c JOIN documents_raw d "
                    "ON c.document_id=d.document_id WHERE c.chunk_id=?",
                    (str(cid),),
                ).fetchone()
                if (authoritative is None or tuple(map(str, authoritative[:4])) !=
                    (str(cid),str(sid),str(did),str(body)) or
                    authoritative[4] != heading or str(authoritative[5]) != str(url)):
                    raise ProjectionMismatchError(f"Graph passage differs from frozen snapshot: {cid}")
                municipalities = _required_municipalities(source, str(did))
                if city not in municipalities:
                    raise ProjectionMismatchError(f"Graph municipality mismatch: {cid}")
                output.append(RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id_for("chunk",str(cid),_sha256(body)),
                    source_type="chunk",source_id=str(cid),segment_id=str(sid),chunk_id=str(cid),
                    document=DocumentSource(
                        document_id=str(did),
                        source_url=_validated_source_url(url, str(did)),
                        heading_context=str(heading) if heading is not None else None,
                        municipality_ids=municipalities,
                    ),
                    evidence_text=body,
                    extraction_run_ids=(),graph_artifact_ids=(),assertions=(),
                    retrieval_traces=(RetrievalTrace("sqlite_structural_topic_graph", rank, score),),
                    retrieval_score=score,rank=rank,reviewed_policy_facts=(),
                ))
            return output
        finally:
            graph.close()


def _reviewed_sqlite_graph_retrieve(
    binding: SnapshotBinding,
    request: RetrievalRequest,
    reviewed_database: Path,
) -> list[RetrievedEvidence]:
    """Traverse reviewed scheme->fact->chunk->document->city in isolated SQLite.

    The graph database is a derived work copy, NEVER the authoritative corpus.
    Every selected chunk and document is independently resolved and matched
    against the validated, immutable source snapshot before returning evidence.
    """
    if not reviewed_database.is_file():
        raise RetrievalError(f"Reviewed graph database does not exist: {reviewed_database}")
    manifest_snapshot = binding.manifest.get("snapshot")
    expected_sha = (
        str(manifest_snapshot.get("sha256") or "").lower()
        if isinstance(manifest_snapshot, dict) else ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise RetrievalError("Snapshot manifest lacks the verified SHA256 origin")
    if not request.municipality_ids:
        raise RetrievalError(
            "Reviewed graph retrieval requires an explicit municipality scope"
        )
    with connect_existing_read_only(binding.database) as source:
        graph = sqlite3.connect(reviewed_database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            graph.execute("PRAGMA query_only=ON")
            graph.execute("BEGIN")  # Consistent read snapshot; no writes are permitted.
            missing = [name for name in _REVIEWED_POLICY_TABLES
                       if not _table_exists(graph, name)]
            if missing:
                raise RetrievalError(f"Reviewed graph tables missing: {missing}")
            origin = graph.execute(
                "SELECT source_sha256, selection_version FROM _graph_comparison_origin"
            ).fetchall()
            metadata = graph.execute(
                "SELECT version, frozen_source_sha256, node_count, edge_count "
                "FROM source_graph_build_metadata"
            ).fetchall()
            if origin != [(expected_sha, "balanced-policy-v1")]:
                raise ProjectionMismatchError("Reviewed graph copy has a different frozen origin")
            if len(metadata) != 1 or metadata[0][:2] != (
                "source-provenance-topic-v1", expected_sha
            ) or min(int(metadata[0][2]), int(metadata[0][3])) < 1:
                raise ProjectionMismatchError("Unrecognized source-provenance graph build")
            # Bounded to reviewed facts in exactly the municipality scope requested.
            placeholders = ",".join("?" for _ in request.municipality_ids)
            rows = graph.execute(
                "SELECT s.scheme_id, s.scheme_name, s.city_id, s.source_url, "
                "s.source_snapshot_sha256, s.review_method, s.approved_utc, "
                "s.document_node, s.city_node, e.fact_id, e.predicate, e.target, "
                "e.evidence_chunk_node, e.quote, e.quote_start, e.quote_end, "
                "e.source_text_sha256, c.segment_id, c.document_id, c.text "
                "FROM source_policy_schemes s "
                "JOIN source_policy_edges e ON e.scheme_id=s.scheme_id "
                "JOIN source_graph_edges ownership ON ownership.src=s.document_node "
                " AND ownership.predicate='ASSIGNED_TO' AND ownership.dst=s.city_node "
                "JOIN source_graph_edges parent ON parent.src=e.evidence_chunk_node "
                " AND parent.predicate='FROM_DOCUMENT' AND parent.dst=s.document_node "
                "JOIN chunks c ON e.evidence_chunk_node='chunk:'||c.chunk_id "
                f"WHERE s.city_id IN ({placeholders}) "
                "ORDER BY s.scheme_id, e.fact_id",
                request.municipality_ids,
            ).fetchall()
            # A broken review path must fail closed rather than be silently dropped.
            total_reviewed = graph.execute(
                "SELECT COUNT(*) FROM source_policy_edges e "
                "JOIN source_policy_schemes s ON s.scheme_id=e.scheme_id "
                f"WHERE s.city_id IN ({placeholders})",
                request.municipality_ids,
            ).fetchone()[0]
            if total_reviewed != len(rows):
                raise ProjectionMismatchError("Reviewed facts have missing or duplicate graph paths")
            general_ids = {str(row[0]) for row in rows if str(row[10]) == "IDENTIFIED_AS"}
            if _table_exists(graph, "source_policy_review_context"):
                registered = {str(x[0]) for x in graph.execute(
                    "SELECT r.scheme_id FROM source_policy_review_context r "
                    "JOIN source_policy_schemes s ON s.scheme_id=r.scheme_id "
                    f"WHERE s.city_id IN ({placeholders})", request.municipality_ids
                )}
                if registered != general_ids:
                    raise ProjectionMismatchError("General-policy identity and scope audit disagree")
            if general_ids:
                if not _table_exists(graph, "source_policy_review_context"):
                    raise ProjectionMismatchError("General-policy scope review metadata missing")
                for scheme_id in sorted(general_ids):
                    ctx = graph.execute(
                        "SELECT policy_model,policy_kind,scope_reason,temporal_state "
                        "FROM source_policy_review_context WHERE scheme_id=?", (scheme_id,)
                    ).fetchone()
                    if (ctx is None or ctx[0] != "general-policy-v1"
                        or not str(ctx[1] or "").strip()
                        or len(str(ctx[2] or "").strip()) < 45
                        or ctx[3] not in {"cited-effective-date", "effective-date-unknown"}):
                        raise ProjectionMismatchError("General-policy scope review is missing or invalid")
            groups: dict[str, dict[str, Any]] = {}
            for row in rows:
                (scheme_id, title, city, url, snapshot_sha, method, approved_at,
                 document_node, city_node, fact_id, predicate, target,
                 chunk_node, quote, quote_start, quote_end, text_sha,
                 work_segment_id, work_document_id, work_text) = row
                scheme_id, city, document_node = str(scheme_id), str(city), str(document_node)
                chunk_node = str(chunk_node)
                if (snapshot_sha != expected_sha or method != "operator-approved-evidence"
                    or city_node != f"municipality:{city}"
                    or document_node != f"document:{work_document_id}"
                    or not chunk_node.startswith("chunk:")
                    or not str(scheme_id).startswith(city+":")
                    or predicate not in {"ISSUED_BY", "EFFECTIVE_FROM", "FUNDS",
                                         "EXCLUDES", "CONDITION", "RATE", "IDENTIFIED_AS",
                                         "ADOPTED", "ADOPTED_ON", "IMPLEMENTED", "PROPOSED",
                                         "TERMINATED", "TERMINATED_ON"}):
                    raise ProjectionMismatchError("Invalid reviewed scheme provenance or predicate")
                chunk_id = chunk_node.removeprefix("chunk:")
                # The committed fact ID binds relationship, target and quote;
                # verify again at retrieval rather than trusting editable work rows.
                expected_fact_id = _sha256(_canonical_json([
                    scheme_id, str(predicate), str(target), chunk_id, str(quote)
                ]))
                if expected_fact_id != str(fact_id):
                    raise ProjectionMismatchError(f"Reviewed fact identity changed: {fact_id}")
                content = str(work_text)
                start, end = int(quote_start), int(quote_end)
                if (start < 0 or end <= start or content[start:end] != quote
                    or content.count(str(quote)) != 1
                    or _sha256(content) != text_sha):
                    raise ProjectionMismatchError(f"Reviewed quotation changed: {fact_id}")
                row_source = source.execute(
                    "SELECT c.chunk_id,c.segment_id,c.document_id,c.text,c.heading_context, "
                    "d.url_canonical FROM chunks c JOIN documents_raw d "
                    "ON d.document_id=c.document_id WHERE c.chunk_id=?",
                    (chunk_id,),
                ).fetchone()
                if row_source is None:
                    raise ProjectionMismatchError(f"Reviewed chunk absent from snapshot: {chunk_id}")
                cid, sid, did, text, heading, source_url = row_source
                if (str(sid) != str(work_segment_id) or str(did) != str(work_document_id)
                    or str(text) != content or str(source_url) != str(url)):
                    raise ProjectionMismatchError(f"Reviewed chunk disagrees with frozen snapshot: {cid}")
                municipalities = _required_municipalities(source, str(did))
                if city not in municipalities:
                    raise ProjectionMismatchError(f"Reviewed municipality mismatch: {city}")
                key = str(cid)
                group = groups.setdefault(key, {
                    "source_row": row_source, "city": city, "municipalities": municipalities,
                    "facts": [], "schemes": defaultdict(list),
                })
                if group["source_row"] != row_source:
                    raise ProjectionMismatchError(f"Reviewed evidence identity collision: {cid}")
                fact = ReviewedPolicyFact(
                    fact_id=str(fact_id), scheme_id=scheme_id, scheme_name=str(title),
                    city_id=city, predicate=str(predicate), target=str(target),
                    source_quote=str(quote), quote_start=start, quote_end=end,
                    effective_from=None, approved_utc=str(approved_at),
                )
                group["facts"].append(fact)
                group["schemes"][scheme_id].append(fact)
            # Effective dates are scheme-scoped even when their quote is in a
            # different chunk. Do not infer availability as of any date.
            scheme_dates = {
                fact.scheme_id: fact.target
                for group in groups.values() for fact in group["facts"]
                if fact.predicate == "EFFECTIVE_FROM"
            }
            scheme_facts: defaultdict[str, list[ReviewedPolicyFact]] = defaultdict(list)
            for group in groups.values():
                for fact in group["facts"]:
                    scheme_facts[fact.scheme_id].append(fact)
            scheme_relevance = {
                scheme_id: _reviewed_policy_question_match(
                    request.query,
                    [{"predicate": fact.predicate, "target": fact.target} for fact in facts],
                    facts[0].scheme_name,
                ) for scheme_id, facts in scheme_facts.items()
            }
            selected: list[tuple[int, RetrievedEvidence]] = []
            for cid, group in groups.items():
                # Query matching is scheme-wide, not restricted to a single
                # cited chunk: retain issuer/date/condition evidence as needed.
                # Determine relevance from all facts within each scheme.
                relevance = max(
                    (scheme_relevance[scheme_id] for scheme_id in group["schemes"]),
                    default=0,
                )
                if relevance <= 0:
                    continue
                cid, sid, did, text, heading, source_url = group["source_row"]
                selected_facts = tuple(sorted(
                    (replace(f, effective_from=scheme_dates.get(f.scheme_id))
                     for f in group["facts"]),
                    key=lambda f: f.fact_id,
                ))
                # Prefer the excerpt proving an eligible/excluded measure over
                # standalone issuer/date metadata. Never turn either into verdict.
                decision_edges = sum(
                    f.predicate in {"FUNDS", "EXCLUDES", "ADOPTED", "IMPLEMENTED",
                                    "PROPOSED", "TERMINATED"} for f in selected_facts
                )
                score = float(relevance * 10 + decision_edges * 3 + len(selected_facts))
                selected.append((decision_edges, RetrievedEvidence(
                    database_snapshot_id=str(binding.database.snapshot_id),
                    evidence_id=evidence_id_for("chunk", str(cid), _sha256(str(text))),
                    source_type="chunk", source_id=str(cid), segment_id=str(sid),
                    chunk_id=str(cid),
                    document=DocumentSource(
                        document_id=str(did),
                        source_url=_validated_source_url(source_url, str(did)),
                        heading_context=str(heading) if heading is not None else None,
                        municipality_ids=group["municipalities"],
                    ),
                    evidence_text=str(text), extraction_run_ids=(), graph_artifact_ids=(),
                    assertions=(), retrieval_traces=(), retrieval_score=score, rank=0,
                    reviewed_policy_facts=selected_facts,
                )))
            selected.sort(key=lambda pair: (-pair[1].retrieval_score, pair[1].evidence_id))
            # Graph path returns only reviewed policy evidence; no source-topic
            # keyword fallback, and no inheritance from unreviewed LLM triples.
            return [
                replace(item, rank=rank,
                        retrieval_traces=(RetrievalTrace("reviewed_sqlite_graph", rank, item.retrieval_score),))
                for rank, (_, item) in enumerate(selected[:request.top_k], 1)
            ]
        finally:
            graph.close()


def _merge_evidence(primary: RetrievedEvidence, secondary: RetrievedEvidence) -> RetrievedEvidence:
    if (
        primary.database_snapshot_id,
        primary.source_type,
        primary.source_id,
        primary.segment_id,
        primary.chunk_id,
        primary.document.document_id,
        primary.document.source_url,
        primary.document.heading_context,
        primary.document.municipality_ids,
        primary.evidence_text,
    ) != (
        secondary.database_snapshot_id,
        secondary.source_type,
        secondary.source_id,
        secondary.segment_id,
        secondary.chunk_id,
        secondary.document.document_id,
        secondary.document.source_url,
        secondary.document.heading_context,
        secondary.document.municipality_ids,
        secondary.evidence_text,
    ):
        raise RetrievalError(f"Evidence identity collision for {primary.evidence_id}")
    assertions = {item.assertion_id: item for item in (*primary.assertions, *secondary.assertions)}
    reviewed_facts: dict[str, ReviewedPolicyFact] = {}
    for fact in (*primary.reviewed_policy_facts, *secondary.reviewed_policy_facts):
        previous = reviewed_facts.get(fact.fact_id)
        if previous is not None and previous != fact:
            raise RetrievalError(f"Conflicting reviewed policy fact {fact.fact_id}")
        reviewed_facts[fact.fact_id] = fact
    return replace(
        primary,
        reviewed_policy_facts=tuple(reviewed_facts[key] for key in sorted(reviewed_facts)),
        extraction_run_ids=tuple(
            sorted(set(primary.extraction_run_ids) | set(secondary.extraction_run_ids))
        ),
        graph_artifact_ids=tuple(
            sorted(set(primary.graph_artifact_ids) | set(secondary.graph_artifact_ids))
        ),
        assertions=tuple(assertions[key] for key in sorted(assertions)),
        retrieval_traces=primary.retrieval_traces + secondary.retrieval_traces,
    )


def reciprocal_rank_fusion(
    semantic: Sequence[RetrievedEvidence],
    graph: Sequence[RetrievedEvidence],
    *,
    top_k: int,
    channel_names: tuple[str, str] = ("semantic", "graph"),
) -> list[RetrievedEvidence]:
    """Fuse incompatible score spaces deterministically using RRF and stable IDs."""
    if not 1 <= top_k <= MAX_TOP_K:
        raise RetrievalError(f"top_k must be between 1 and {MAX_TOP_K}")
    combined: dict[str, RetrievedEvidence] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for channel, source in zip(channel_names, (semantic, graph), strict=True):
        seen: set[str] = set()
        for source_rank, item in enumerate(source, start=1):
            if item.evidence_id in seen:
                raise RetrievalError(
                    f"{channel} retrieval returned duplicate evidence {item.evidence_id}"
                )
            seen.add(item.evidence_id)
            if item.rank != source_rank:
                raise RetrievalError(
                    f"{channel} retrieval ranks must be contiguous and match result order"
                )
            increment = 1.0 / (RRF_CONSTANT + source_rank)
            scores[item.evidence_id] += increment
            traced = replace(
                item,
                retrieval_traces=tuple(
                    replace(trace, fusion_score=increment) for trace in item.retrieval_traces
                ),
            )
            combined[item.evidence_id] = (
                _merge_evidence(combined[item.evidence_id], traced)
                if item.evidence_id in combined
                else traced
            )
    ordered = sorted(
        combined.values(), key=lambda item: (-scores[item.evidence_id], item.evidence_id)
    )
    return [
        replace(item, retrieval_score=scores[item.evidence_id], rank=rank)
        for rank, item in enumerate(ordered[:top_k], start=1)
    ]


class Neo4jReadBackend:
    """Read-only Neo4j adapter; queries are fixed above and never user-authored."""

    def __init__(self, driver: Any, *, database: str):
        if not database.strip():
            raise RetrievalError("Neo4j database must be explicit")
        self.driver = driver
        self.database = database

    def read(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> list[Mapping[str, Any]]:
        from neo4j import READ_ACCESS, Query

        with self.driver.session(
            database=self.database,
            default_access_mode=READ_ACCESS,
        ) as session:
            result = session.run(
                Query(cypher, timeout=timeout_seconds), parameters=dict(parameters)
            )
            return result.data()

    def close(self) -> None:
        self.driver.close()


class CanonicalRetriever:
    """Single production retrieval abstraction for semantic, lexical, graph, and fusion modes."""

    def __init__(
        self,
        *,
        embedder: QueryEmbedder | None = None,
        graph_backend: GraphReadBackend | None = None,
        lexical_database: str | Path | None = None,
        reviewed_graph_database: str | Path | None = None,
        structural_graph_database: str | Path | None = None,
        graph_timeout_seconds: float = DEFAULT_GRAPH_TIMEOUT_SECONDS,
    ):
        if graph_timeout_seconds <= 0:
            raise RetrievalError("Graph query timeout must be positive")
        self.embedder = embedder
        self.graph_backend = graph_backend
        self.lexical_database = (
            Path(lexical_database).expanduser().resolve()
            if lexical_database is not None
            else None
        )
        self.graph_timeout_seconds = graph_timeout_seconds
        self.reviewed_graph_database = (
            Path(reviewed_graph_database).expanduser().resolve()
            if reviewed_graph_database is not None else None
        )
        self.structural_graph_database = (
            Path(structural_graph_database).expanduser().resolve()
            if structural_graph_database is not None else None
        )

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        from crawler.core.question_intent import extract_question_intent, FORMAL_POLICY_PATTERNS
        request.validate()
        binding = validate_snapshot_manifest(request.manifest_path)
        semantic: list[RetrievedEvidence] = []
        lexical: list[RetrievedEvidence] = []
        graph: list[RetrievedEvidence] = []
        mention: list[RetrievedEvidence] = []
        artifact_id = None
        candidate_request = request
        if request.mode == RetrievalMode.SEMANTIC_LEXICAL:
            candidate_request = replace(
                request,
                top_k=min(
                    MAX_TOP_K,
                    max(
                        MIN_FUSION_CANDIDATES,
                        request.top_k * FUSION_CANDIDATE_MULTIPLIER,
                    ),
                ),
            )
        if request.mode in {
            RetrievalMode.SEMANTIC,
            RetrievalMode.SEMANTIC_LEXICAL,
            RetrievalMode.HYBRID,
        }:
            if self.embedder is None:
                raise RetrievalError("Semantic retrieval requires an explicit query embedder")
            semantic, artifact_id = _semantic_retrieve(
                binding, candidate_request, self.embedder
            )
        if request.mode in {RetrievalMode.LEXICAL, RetrievalMode.SEMANTIC_LEXICAL}:
            if self.lexical_database is None:
                raise RetrievalError(
                    "Lexical retrieval requires an explicit lexical_database"
                )
            lexical = _lexical_retrieve(
                binding, candidate_request, self.lexical_database
            )
        if request.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}:
            if request.graph_source == "reviewed_sqlite":
                if self.reviewed_graph_database is None:
                    raise RetrievalError("Reviewed graph retrieval requires reviewed_graph_database")
                graph = _reviewed_sqlite_graph_retrieve(
                    binding, request, self.reviewed_graph_database
                )
            elif request.graph_source == "structural_sqlite":
                if self.structural_graph_database is None:
                    raise RetrievalError("Structural graph retrieval requires structural_graph_database")
                graph = _structural_sqlite_graph_retrieve(
                    binding, request, self.structural_graph_database
                )
            else:
                if self.graph_backend is None:
                    raise RetrievalError("Graph retrieval requires an explicit read-only backend")
                graph = _graph_retrieve(
                    binding, request, self.graph_backend, self.graph_timeout_seconds
                )
        if request.mode == RetrievalMode.MENTION:
            if self.graph_backend is None:
                raise RetrievalError("Mention retrieval requires an explicit read-only backend")
            mention = _mention_retrieve(
                binding, request, self.graph_backend, self.graph_timeout_seconds
            )
        items = (
            reciprocal_rank_fusion(semantic, graph, top_k=request.top_k)
            if request.mode == RetrievalMode.HYBRID
            else reciprocal_rank_fusion(
                semantic,
                lexical,
                top_k=request.top_k,
                channel_names=("semantic", "lexical"),
            )
            if request.mode == RetrievalMode.SEMANTIC_LEXICAL
            else mention
            if request.mode == RetrievalMode.MENTION
            else lexical
            if request.mode == RetrievalMode.LEXICAL
            else semantic or graph
        )
        return RetrievalResult(
            query=request.query,
            mode=request.mode.value,
            database_snapshot_id=str(binding.database.snapshot_id),
            projection_id=request.projection_id,
            items=tuple(items),
            embedding_artifact_id=artifact_id,
            warnings=(
                ("Reviewed policy graph is incomplete: unreviewed schemes are not evidence of absence.",
                 "Reviewed facts are source claims, not a current-availability or YES/NO verdict.")
                if request.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}
                and request.graph_source == "reviewed_sqlite" else
                ("Structural topic graph retrieves source passages, NOT verified policy actions or YES/NO facts.",)
                if request.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}
                and request.graph_source == "structural_sqlite" else ()
            ) + (("Formal-policy retrieval uses a bounded decision-relevance rerank; graph paths remain structural evidence only.",)
                 if extract_question_intent(request.query).policy_family in FORMAL_POLICY_PATTERNS else ()),
        )
