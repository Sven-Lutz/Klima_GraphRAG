"""Thin CLI over canonical retrieval and grounded answer generation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from crawler.core.answer_context import EvidenceBudget
from crawler.core.answer_generation import (
    AnswerGenerationError,
    AnswerRequest,
    CanonicalAnswerGenerator,
    GenerationParameters,
    JsonlAnswerRunRecorder,
    OllamaGenerationProvider,
    render_answer_json,
    render_answer_markdown,
)
from crawler.core.citations import (
    build_citation_registry,
    render_citations_json,
    render_citations_markdown,
)
from crawler.core.config import LLM_SEED, LLM_TEMPERATURE, OLLAMA_MODEL, OLLAMA_URL
from crawler.core.retrieval import (
    CanonicalRetriever,
    EmbeddingIdentity,
    Neo4jReadBackend,
    RetrievalError,
    RetrievalMode,
    RetrievalRequest,
)
from crawler.core.snapshots import SnapshotManifestError, validate_snapshot_manifest
from crawler.scripts.generate_embeddings import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    DEFAULT_PREPROCESSING_ID,
    DEFAULT_PROVIDER,
)


class SentenceTransformerQueryEmbedder:
    """Lazy local query embedder with an explicit lineage identity."""

    def __init__(
        self,
        *,
        model: str,
        model_revision: str,
        vector_dimension: int,
        preprocessing_id: str,
    ):
        self.identity = EmbeddingIdentity(
            provider=DEFAULT_PROVIDER,
            model=model,
            model_revision=model_revision,
            vector_dimension=vector_dimension,
            preprocessing_id=preprocessing_id,
        )
        self._model = None

    def embed_query(self, text: str) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            revision = (
                {}
                if self.identity.model_revision == DEFAULT_MODEL_REVISION
                else {"revision": self.identity.model_revision}
            )
            self._model = SentenceTransformer(self.identity.model, **revision)
            self._model.max_seq_length = 256
        vector = self._model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
        return np.asarray(vector, dtype=np.float32)


def _graph_backend(args: argparse.Namespace) -> Neo4jReadBackend:
    if os.environ.get("KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED") != "1":
        raise RetrievalError("Graph retrieval requires KLIMA_RETRIEVAL_NEO4J_READONLY_CONFIRMED=1")
    password = os.environ.get("KLIMA_RETRIEVAL_NEO4J_PASSWORD")
    if not password:
        raise RetrievalError("Graph retrieval password must be supplied through the environment")
    from neo4j import GraphDatabase

    try:
        driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, password))
        driver.verify_connectivity()
    except Exception as exc:
        raise RetrievalError("Configured read-only Neo4j retrieval service is unavailable") from exc
    return Neo4jReadBackend(driver, database=args.neo4j_database)


def _render_markdown(result) -> str:
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Canonical snapshot-bound GraphRAG retrieval and grounded answers "
            "(Text2Cypher disabled)"
        )
    )
    parser.add_argument(
        "query", help="Natural-language retrieval query (never executed as SQL/Cypher)"
    )
    parser.add_argument("--db", required=True, help="Manifested SQLite snapshot file")
    parser.add_argument("--manifest", required=True, help="Validated snapshot manifest")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in RetrievalMode],
        default=RetrievalMode.SEMANTIC.value,
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--projection-id", default=None)
    parser.add_argument("--extraction-run-id", action="append", default=[])
    parser.add_argument("--output", choices=["markdown", "json"], default="markdown")
    parser.add_argument(
        "--answer",
        action="store_true",
        help="Generate through the canonical grounded answer layer",
    )
    parser.add_argument(
        "--generation-provider",
        choices=["ollama"],
        default="ollama",
    )
    parser.add_argument("--generation-model", default=OLLAMA_MODEL)
    parser.add_argument("--generation-temperature", type=float, default=LLM_TEMPERATURE)
    parser.add_argument("--generation-seed", type=int, default=LLM_SEED)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--generation-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-evidence-items", type=int, default=8)
    parser.add_argument("--max-evidence-characters", type=int, default=24_000)
    parser.add_argument(
        "--answer-run-log",
        help="Required append-only JSONL lifecycle log for answer generation",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("KLIMA_OLLAMA_URL", OLLAMA_URL),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--embedding-dimension", type=int, default=384)
    parser.add_argument("--preprocessing-id", default=DEFAULT_PREPROCESSING_ID)
    parser.add_argument(
        "--neo4j-uri",
        default=os.environ.get("KLIMA_RETRIEVAL_NEO4J_URI", "bolt://127.0.0.1:7687"),
    )
    parser.add_argument(
        "--neo4j-database",
        default=os.environ.get("KLIMA_RETRIEVAL_NEO4J_DATABASE", "neo4j"),
    )
    parser.add_argument("--neo4j-user", default=os.environ.get("KLIMA_RETRIEVAL_NEO4J_USER", ""))
    args = parser.parse_args(argv)

    if args.answer and not args.answer_run_log:
        parser.error("--answer requires an explicit --answer-run-log")

    try:
        binding = validate_snapshot_manifest(args.manifest)
    except SnapshotManifestError as exc:
        parser.error(str(exc))
    selected = Path(args.db).expanduser().resolve()
    if selected != binding.database.path:
        parser.error("--db must be the exact SQLite file bound by --manifest")
    mode = RetrievalMode(args.mode)
    embedder = (
        SentenceTransformerQueryEmbedder(
            model=args.model,
            model_revision=args.model_revision,
            vector_dimension=args.embedding_dimension,
            preprocessing_id=args.preprocessing_id,
        )
        if mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID}
        else None
    )
    backend = None
    answer_result = None
    result = None
    try:
        if mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID}:
            if not args.neo4j_user:
                parser.error("Graph retrieval requires an explicit --neo4j-user")
            backend = _graph_backend(args)
        retriever = CanonicalRetriever(embedder=embedder, graph_backend=backend)
        retrieval_request = RetrievalRequest(
            query=args.query,
            manifest_path=args.manifest,
            mode=mode,
            top_k=args.top_k,
            projection_id=args.projection_id,
            extraction_run_ids=tuple(args.extraction_run_id),
        )
        if args.answer:
            provider = OllamaGenerationProvider(args.ollama_url)
            generator = CanonicalAnswerGenerator(
                retriever=retriever,
                providers={provider.name: provider},
                run_recorder=JsonlAnswerRunRecorder(args.answer_run_log),
            )
            answer_result = generator.generate(
                AnswerRequest(
                    question=args.query,
                    retrieval_request=retrieval_request,
                    database_snapshot_id=str(binding.database.snapshot_id),
                    generation_provider=args.generation_provider,
                    generation_model=args.generation_model,
                    generation_parameters=GenerationParameters(
                        temperature=args.generation_temperature,
                        max_output_tokens=args.max_output_tokens,
                        seed=args.generation_seed,
                        timeout_seconds=args.generation_timeout_seconds,
                    ),
                    evidence_budget=EvidenceBudget(
                        max_items=args.max_evidence_items,
                        max_characters=args.max_evidence_characters,
                    ),
                )
            )
        else:
            result = retriever.retrieve(retrieval_request)
    except (RetrievalError, AnswerGenerationError) as exc:
        parser.error(str(exc))
    finally:
        if backend is not None:
            backend.close()

    if answer_result is not None:
        renderer = render_answer_json if args.output == "json" else render_answer_markdown
        print(renderer(answer_result), end="\n" if args.output == "json" else "")
    elif args.output == "json":
        assert result is not None
        registry = build_citation_registry(result.items)
        payload = result.to_dict()
        payload["citation_registry"] = json.loads(render_citations_json(registry))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        assert result is not None
        print(_render_markdown(result), end="")


if __name__ == "__main__":
    main()
