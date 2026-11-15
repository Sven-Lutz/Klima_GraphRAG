"""Explicit post-hoc strong lexical control; original four arms remain immutable."""

import argparse
import types
from pathlib import Path

from research.climate_policy import answer, evaluate, retrieval, store
from research.offline_evaluation.contracts import require


def retrieve(db, question, method="bm25_context", k=5, budget_chars=6000, allowed_sources=None):
    require(method == "bm25_context" and 1 <= k <= 10, "Invalid context baseline")
    with store.connect(db) as conn:
        units = retrieval.load_units(conn)
    if allowed_sources is not None:
        units = [u for u in units if u["raw_hashes"][0] in allowed_sources]
    indexed = [
        {
            **u,
            "text": store.MUNICIPALITIES[u["acquisition_ags"]]
            + " "
            + (u["heading_context"] or "")
            + " "
            + u["text"],
        }
        for u in units
    ]
    ranked = retrieval.lexical_rank(indexed, question, False)
    originals = {u["unit_id"]: u for u in units}
    selected = []
    used = 0
    for uid, score in ranked:
        u = originals[uid]
        if used + len(u["text"]) > budget_chars:
            continue
        selected.append({**u, "score": score, "rank": len(selected) + 1})
        used += len(u["text"])
        if len(selected) >= k:
            break
    return {
        "items": selected,
        "operations": ["metadata_context_bm25"],
        "candidate_units": len(units),
        "context_chars": used,
        "question_intent": retrieval.intent(question),
        "k": k,
        "budget_chars": budget_chars,
        "access_scope_sha256": store.ident(sorted(u["unit_id"] for u in units)),
    }


# Private global environments reuse exactly the frozen composer/evaluator bytecode.
# No mutable patching of shared modules or historical ranking implementation.
ANALYZE = types.FunctionType(
    answer.analyze.__code__,
    {**answer.__dict__, "retrieval": types.SimpleNamespace(retrieve=retrieve)},
    "context_analyze",
    answer.analyze.__defaults__,
)
EVALUATE = types.FunctionType(
    evaluate.evaluate.__code__,
    {**evaluate.__dict__, "analyze": ANALYZE},
    "context_evaluate",
    evaluate.evaluate.__defaults__,
)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    EVALUATE(args.benchmark, args.out)
