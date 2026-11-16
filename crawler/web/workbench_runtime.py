"""Read-only reuse of explicit existing local corpus/index/model artifacts. No rebuild."""

import hashlib
import importlib.util
import json
from pathlib import Path


def create_local_workbench_service(config_path):
    from crawler.web.bavaria import create_bavaria_service
    from crawler.web.local_index import LocalIndex
    from crawler.web.product_retrieval import ProductRetriever
    from crawler.web.semantic_index import SemanticIndex

    config_path = Path(config_path).resolve(strict=True)
    config = json.loads(config_path.read_text())
    if config.get("schema") != "local-research-workbench-v1" or config.get("use") != (
        "LOCAL_RESEARCH_ONLY_NO_REDISTRIBUTION"
    ):
        raise ValueError("Explicit local-only corpus use contract required")
    required = ["workspace", "terms_index", "embedding_model", "semantic_index", "original_roots"]
    if any(not config.get(key) for key in required):
        raise ValueError("Explicit corpus, index, model and original roots required")
    paths = {key: Path(config[key]).resolve(strict=True) for key in required[:-1]}
    originals = [Path(p).resolve(strict=True) for p in config["original_roots"]]
    if not all(p.is_dir() for p in originals):
        raise ValueError("Existing explicit original directories required")
    # LocalIndex builds if absent: preflight prevents that side effect here.
    if not paths["terms_index"].is_file():
        raise ValueError("Existing complete term index required; no implicit build")
    for name in ["index.sqlite", "vectors.f32"]:
        if not (paths["semantic_index"] / name).is_file():
            raise ValueError("Existing complete semantic index required; no implicit build")
    service = create_bavaria_service(paths["workspace"], original_roots=originals)
    service.workspace.discovery_index = LocalIndex(
        service.workspace, paths["terms_index"], build_if_missing=False
    )
    semantic = None
    unavailable = {}
    if all(importlib.util.find_spec(m) for m in ["onnxruntime", "tokenizers"]):
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        semantic = SemanticIndex(
            service.workspace, paths["semantic_index"], paths["embedding_model"]
        )
        if not semantic.status().get("complete"):
            raise ValueError("Incomplete dense index; no rebuild permitted")
        # Probe the actual resident encoder, verify its file hashes; never downloads.
        vector = semantic._encoder().encode(["Kommunaler Klimaschutz"])
        if vector.shape != (1, 384):
            raise ValueError("Embedding qualification probe shape mismatch")
    else:
        unavailable = {
            key: "Lokale ONNX-/Tokenizer-Abhängigkeit fehlt; keine Downloads gestartet."
            for key in ["semantic", "hybrid"]
        }
    service.retriever = ProductRetriever(service.retriever, semantic)
    service.retriever.unavailable_reasons = unavailable
    model_manifest = json.loads((paths["embedding_model"] / "manifest.json").read_text())
    service.workbench_binding = {
        "corpus_class": "REAL_LOCAL_RESEARCH",
        "rights": "LOCAL_RESEARCH_ONLY; ORIGINAL_REDISTRIBUTION_NOT_CLEARED",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "snapshot": service.database_snapshot_id,
        "retriever": "evidence-retrieval-product-v4",
        "embedding_model": model_manifest,
        "dense_ready": semantic is not None,
        "embedding_index_binding": semantic.binding if semantic else None,
        "encoder_runtime": onnxruntime.__version__ if semantic else None,
        "encoder_parameters": {
            "max_sequence_length": 128,
            "threads": 2,
            "pooling": "masked-mean-l2",
            "device": "CPU",
        },
        "ie_status": "EXISTING_VERSIONED_PROVISIONAL_RELATIONS_NOT_REEXTRACTED",
        "human_gold": False,
    }
    service.demo_notice = (
        "REAL LOCAL CORPUS · vorhandene kommunale Quellen; lokale Forschungsnutzung. "
        "Keine pauschale Weitergabelizenz, Bayern-Vollständigkeit oder unabhängige Validierung."
    )
    return service


def original_for_anchor(service, anchor):
    """Resolve an already owner-authorized turn anchor to an archived original version."""
    from contextlib import closing

    from crawler.web.bavaria import read_original
    from research.bavaria.federation import source_connection

    if not getattr(service, "workbench_binding", None) or not anchor.get("source_sha256"):
        raise ValueError("No locally bound original available")
    baseline = getattr(service.retriever, "baseline", service.retriever)
    baseline.check_identity()
    workspace = service.workspace
    with closing(source_connection(workspace.db, workspace.source_databases)) as connection:
        record = connection.execute(
            "SELECT raw_path,raw_bytes FROM documents WHERE sha256=?",
            (anchor["source_sha256"],),
        ).fetchone()
    return read_original(record, anchor["source_sha256"], service.original_roots)
