"""Explicit production composition root for the interactive application."""

from __future__ import annotations

import os
from pathlib import Path

from crawler.core.answer_generation import (
    CanonicalAnswerGenerator,
    JsonlAnswerRunRecorder,
    OllamaGenerationProvider,
)
from crawler.core.config import OLLAMA_MODEL, OLLAMA_URL
from crawler.core.query_embedding import SentenceTransformerQueryEmbedder
from crawler.core.retrieval import CanonicalRetriever, Neo4jReadBackend, RetrievalError
from crawler.core.snapshots import validate_snapshot_manifest
from crawler.scripts.generate_embeddings import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    DEFAULT_PREPROCESSING_ID,
    DEFAULT_PROVIDER,
)
from crawler.web.service import (
    LIVE_PROVIDER_ACCEPTANCE_EVIDENCE,
    ProviderAcceptance,
    ProviderAvailability,
    ProviderGate,
    ResearchApplicationService,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required application setting: {name}")
    return value


class LazyNeo4jReadBackend:
    """Connect only for graph requests so semantic retrieval stays available."""

    def __init__(self, uri: str, user: str, password: str, database: str):
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._backend: Neo4jReadBackend | None = None

    def _connect(self) -> Neo4jReadBackend:
        if self._backend is None:
            from neo4j import GraphDatabase

            driver = None
            try:
                driver = GraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                )
                driver.verify_connectivity()
            except Exception as exc:
                if driver is not None:
                    driver.close()
                raise RetrievalError("Configured read-only Neo4j service is unavailable") from exc
            self._backend = Neo4jReadBackend(driver, database=self._database)
        return self._backend

    def read(self, cypher, parameters, *, timeout_seconds):
        return self._connect().read(cypher, parameters, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()


def _graph_backend():
    if os.environ.get("KLIMA_UI_NEO4J_ENABLED") != "1":
        return None
    if os.environ.get("KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED") != "1":
        raise RetrievalError("Graph retrieval requires read-only principal confirmation")
    uri = _required("KLIMA_RETRIEVAL_NEO4J_URI")
    user = _required("KLIMA_RETRIEVAL_NEO4J_USER")
    password = _required("KLIMA_RETRIEVAL_NEO4J_PASSWORD")
    database = _required("KLIMA_RETRIEVAL_NEO4J_DATABASE")
    return LazyNeo4jReadBackend(uri, user, password, database)


def create_production_service() -> ResearchApplicationService:
    manifest = _required("KLIMA_UI_SNAPSHOT_MANIFEST")
    selected_database = Path(_required("KLIMA_UI_DB_PATH")).expanduser().resolve()
    binding = validate_snapshot_manifest(manifest)
    if selected_database != binding.database.path:
        raise RuntimeError("KLIMA_UI_DB_PATH must exactly match the manifested snapshot")

    embedder = SentenceTransformerQueryEmbedder(
        provider=DEFAULT_PROVIDER,
        model=os.environ.get("KLIMA_UI_EMBEDDING_MODEL", DEFAULT_MODEL),
        model_revision=os.environ.get("KLIMA_UI_EMBEDDING_MODEL_REVISION", DEFAULT_MODEL_REVISION),
        default_model_revision=DEFAULT_MODEL_REVISION,
        vector_dimension=int(os.environ.get("KLIMA_UI_EMBEDDING_DIMENSION", "384")),
        preprocessing_id=os.environ.get(
            "KLIMA_UI_EMBEDDING_PREPROCESSING_ID", DEFAULT_PREPROCESSING_ID
        ),
    )
    backend = _graph_backend()
    retriever = CanonicalRetriever(embedder=embedder, graph_backend=backend)
    generation_enabled = os.environ.get("KLIMA_UI_GENERATION_ENABLED") == "1"
    provider = OllamaGenerationProvider(os.environ.get("KLIMA_OLLAMA_URL", OLLAMA_URL))
    providers = {provider.name: provider} if generation_enabled else {}
    run_log = Path(_required("KLIMA_UI_ANSWER_RUN_LOG"))
    generator = CanonicalAnswerGenerator(
        retriever=retriever,
        providers=providers,
        run_recorder=JsonlAnswerRunRecorder(run_log),
    )
    return ResearchApplicationService(
        retriever=retriever,
        generator=generator,
        manifest_path=str(binding.manifest_path),
        database_snapshot_id=str(binding.database.snapshot_id),
        projection_id=os.environ.get("KLIMA_UI_PROJECTION_ID") or None,
        extraction_run_ids=tuple(
            value.strip()
            for value in os.environ.get("KLIMA_UI_EXTRACTION_RUN_IDS", "").split(",")
            if value.strip()
        ),
        provider_gate=ProviderGate(
            availability=(
                ProviderAvailability.ANSWER_GENERATION_READY
                if generation_enabled
                else ProviderAvailability.RETRIEVAL_ONLY_READY
            ),
            acceptance=ProviderAcceptance.LIVE_PROVIDER_VALIDATED,
            acceptance_evidence=LIVE_PROVIDER_ACCEPTANCE_EVIDENCE,
            provider="ollama",
            model=os.environ.get("KLIMA_OLLAMA_MODEL", OLLAMA_MODEL),
            message=(
                "Ollama answer generation is configured. Provider acceptance is closed; "
                "runtime availability is reported separately per session."
                if generation_enabled
                else "Retrieval is ready; answer generation is not enabled."
            ),
        ),
    )
