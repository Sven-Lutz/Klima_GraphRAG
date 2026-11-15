"""Bounded local ONNX text embeddings, pinned to checksummed model files."""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class TextEncoder:
    def __init__(self, model):
        model = Path(model)
        self.manifest = json.loads((model / "manifest.json").read_bytes())
        for name, record in self.manifest["files"].items():
            if hashlib.sha256((model / name).read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError("Model checksum mismatch: " + name)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model / "onnx/model_quantized.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(model / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.input_names = {item.name for item in self.session.get_inputs()}

    def encode(self, texts):
        output = []
        for offset in range(0, len(texts), 8):
            batch = self.tokenizer.encode_batch(texts[offset : offset + 8])
            inputs = {
                "input_ids": np.array([x.ids for x in batch], dtype=np.int64),
                "attention_mask": np.array([x.attention_mask for x in batch], dtype=np.int64),
                "token_type_ids": np.array([x.type_ids for x in batch], dtype=np.int64),
            }
            tokens = self.session.run(
                None, {k: v for k, v in inputs.items() if k in self.input_names}
            )[0]
            if tokens.ndim == 3:
                mask = inputs["attention_mask"][..., None]
                vectors = (tokens * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            else:
                vectors = tokens
            if vectors.shape[1] != 384 or not np.isfinite(vectors).all():
                raise ValueError("Invalid embedding dimensions or nonfinite outputs")
            output.append(
                vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            )
        return np.concatenate(output).astype(np.float32)


def rankings(model, units, questions, output):
    started = time.perf_counter()
    encoder = TextEncoder(model)
    texts = [(u["heading_context"] or "") + "\n" + u["text"] for u in units]
    vectors = encoder.encode(texts)
    queries = encoder.encode(questions)
    scores = queries @ vectors.T
    np.save(Path(output) / "embeddings.npy", vectors, allow_pickle=False)
    np.save(Path(output) / "query_embeddings.npy", queries, allow_pickle=False)
    report = {
        "model": encoder.manifest,
        "units": len(units),
        "queries": len(questions),
        "dimensions": 384,
        "max_sequence_length": 128,
        "pooling": "masked_mean_l2",
        "runtime": ort.__version__,
        "provider": "CPUExecutionProvider",
        "threads": 2,
        "batch": 8,
        "seconds": time.perf_counter() - started,
        "input_ids": [u["unit_id"] for u in units],
        "embedding_bytes": vectors.nbytes,
        "query_bytes": queries.nbytes,
        "texts_sha256": hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest(),
    }
    (Path(output) / "embedding-manifest.json").write_text(json.dumps(report, indent=2))
    return [[units[i]["unit_id"] for i in np.argsort(-row, kind="stable")] for row in scores]


def window_rankings(model, units, questions, output):
    """Max-pool overlapping token windows within the same original evidence unit.

    Fixes the information loss of a single 128-token prefix for long PDF pages.
    This is a declared preprocessing ablation, not a different text model.
    """
    started = time.perf_counter()
    encoder = TextEncoder(model)
    segmenter = Tokenizer.from_file(str(Path(model) / "tokenizer.json"))
    segmenter.no_truncation()
    segmenter.no_padding()
    windows, owners, lengths = [], [], []
    for i, u in enumerate(units):
        text = (u["heading_context"] or "") + "\n" + u["source_url"] + "\n" + u["text"]
        offsets = [(a, b) for a, b in segmenter.encode(text).offsets if b > a]
        lengths.append(len(offsets))
        if not offsets:
            windows.append(text)
            owners.append(i)
            continue
        for start in range(0, len(offsets), 84):
            end = min(start + 112, len(offsets))
            windows.append(text[offsets[start][0] : offsets[end - 1][1]])
            owners.append(i)
            if end == len(offsets):
                break
    estimated_bytes = len(windows) * 384 * 4
    if estimated_bytes > 32 * 1024**2:
        raise RuntimeError("Window embedding allowance exceeds 32 MiB")
    vectors = encoder.encode(windows)
    queries = encoder.encode(questions)
    window_scores = queries @ vectors.T
    unit_scores = np.full((len(questions), len(units)), -np.inf, dtype=np.float32)
    for j, owner in enumerate(owners):
        unit_scores[:, owner] = np.maximum(unit_scores[:, owner], window_scores[:, j])
    if not np.isfinite(unit_scores).all():
        raise ValueError("Missing window coverage")
    output = Path(output)
    np.save(output / "window_embeddings.npy", vectors, allow_pickle=False)
    report = {
        "method": "max-pooled-overlapping-token-windows",
        "model_manifest": encoder.manifest,
        "units": len(units),
        "windows": len(windows),
        "window_tokens": 112,
        "stride_tokens": 84,
        "units_over_128_tokens": sum(n > 128 for n in lengths),
        "max_input_tokens": max(lengths),
        "embedding_bytes": vectors.nbytes,
        "seconds": time.perf_counter() - started,
        "input_ids": [u["unit_id"] for u in units],
        "input_text_sha256": hashlib.sha256(
            json.dumps(windows, ensure_ascii=False).encode()
        ).hexdigest(),
        "query_sha256": hashlib.sha256(
            json.dumps(questions, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    (output / "window_manifest.json").write_text(json.dumps(report, indent=2))
    return [
        [
            units[j]["unit_id"]
            for j in sorted(range(len(units)), key=lambda k: (-float(row[k]), units[k]["unit_id"]))
        ]
        for row in unit_scores
    ]
