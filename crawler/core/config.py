"""
config.py
=========
Single source of truth for all runtime settings.

Every script imports from here. Override via environment variables or by
editing this file directly — no more hunting for hardcoded URLs.

Environment variable precedence:
    KLIMA_NEO4J_URI        overrides NEO4J_URI
    KLIMA_NEO4J_USER       overrides NEO4J_USER
    KLIMA_NEO4J_PASSWORD   overrides NEO4J_PASSWORD
    KLIMA_OLLAMA_URL       overrides OLLAMA_URL
    KLIMA_OLLAMA_MODEL     overrides OLLAMA_MODEL
    KLIMA_DB_PATH          overrides DB_PATH
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = Path("crawler/data/db/crawl.sqlite")
# Backwards-compatible import-time value for legacy callers. New entry points
# must call resolve_db_path() at runtime so CLI configuration stays authoritative.
DB_PATH: Path = Path(os.environ.get("KLIMA_DB_PATH", DEFAULT_DB_PATH))


def resolve_db_path(value: str | Path | None = None) -> Path:
    """Resolve a database path at runtime without creating a fallback database.

    An explicit *value* wins over ``KLIMA_DB_PATH``. When neither is supplied,
    the documented repository default is used. Existence checks belong to the
    invoking command because setup commands are allowed to create a database
    while workers must require one that already exists.
    """
    if value is not None:
        return Path(value).expanduser()
    return Path(os.environ.get("KLIMA_DB_PATH", DEFAULT_DB_PATH)).expanduser()


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------
NEO4J_URI: str = os.environ.get("KLIMA_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.environ.get("KLIMA_NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.environ.get("KLIMA_NEO4J_PASSWORD", "password")

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
OLLAMA_URL: str = os.environ.get("KLIMA_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("KLIMA_OLLAMA_MODEL", "llama3.1:8b")

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
CHUNK_SIZE: int = 600  # tokens per chunk (cl100k_base)
CHUNK_OVERLAP: int = 60  # token overlap between consecutive chunks

# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
EMBED_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_BATCH_SIZE: int = 64

# ---------------------------------------------------------------------------
# Graph extraction
# ---------------------------------------------------------------------------
EXTRACT_MIN_SCORE: float = 15.0
EXTRACT_CONTEXT_WINDOW: int = 1  # neighbour segments included in prompt

# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------
# TLS-Zertifikate beim Crawlen verifizieren. Default AUS, weil viele kommunale
# Server kaputte/unvollständige Zertifikatsketten haben und der Crawl sonst
# große Teile Bayerns verliert. Für andere Einsatzzwecke: KLIMA_TLS_VERIFY=1.
TLS_VERIFY: bool = os.environ.get("KLIMA_TLS_VERIFY", "0") == "1"

# ---------------------------------------------------------------------------
# Reproducibility (Benchmarking)
# ---------------------------------------------------------------------------
# Fester Seed + einheitliche Temperatur für alle Ollama-Calls, damit
# Benchmark-Läufe reproduzierbar und zitierfähig sind.
LLM_SEED: int = int(os.environ.get("KLIMA_LLM_SEED", "42"))
LLM_TEMPERATURE: float = float(os.environ.get("KLIMA_LLM_TEMPERATURE", "0.0"))
# Bei jeder inhaltlichen Prompt-Änderung erhöhen — wird in Eval-Reports mitgeschrieben
PROMPT_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Community detection
# ---------------------------------------------------------------------------
COMMUNITY_MIN_SIZE: int = 3
COMMUNITY_ALGORITHM: str = "leiden"  # or "louvain"
GDS_GRAPH_NAME: str = "klimaEntities"

# ---------------------------------------------------------------------------
# Query agent
# ---------------------------------------------------------------------------
MAX_CRITIC_LOOPS: int = 3
LOCAL_SEARCH_TOP_K: int = 8
GLOBAL_SEARCH_MAX_COMMUNITIES: int = 50

# ---------------------------------------------------------------------------
# Text2Cypher (experimental; disabled by default)
# ---------------------------------------------------------------------------
TEXT2CYPHER_ENABLED: bool = os.environ.get("KLIMA_TEXT2CYPHER_ENABLED", "0") == "1"
TEXT2CYPHER_READONLY_USER: str = os.environ.get("KLIMA_TEXT2CYPHER_READONLY_USER", "")
TEXT2CYPHER_READONLY_PASSWORD: str = os.environ.get("KLIMA_TEXT2CYPHER_READONLY_PASSWORD", "")
TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED: bool = (
    os.environ.get("KLIMA_TEXT2CYPHER_READONLY_PRINCIPAL_CONFIRMED", "0") == "1"
)
TEXT2CYPHER_TIMEOUT_SECONDS: float = float(os.environ.get("KLIMA_TEXT2CYPHER_TIMEOUT_SECONDS", "5"))
TEXT2CYPHER_MAX_RESULTS: int = int(os.environ.get("KLIMA_TEXT2CYPHER_MAX_RESULTS", "50"))
