# ruff: noqa: E501
"""Paired policy-relation retrieval experiment under frozen logical requests.

SQL and adjacency traversal use identical source-reviewed provisional relations.
This is a representation/evidence-assembly experiment, not an end-to-end IE or
independent human benchmark. Logical requests are available to every arm.
"""

import argparse
import json
import os
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research.climate_policy.retrieval import lexical_rank

from .prepare import RESERVE, sha


def validate_patterns(patterns):
    if not 1 <= len(patterns) <= 5:
        raise ValueError("A logical request must contain one to five patterns")
    if any(
        len(pattern) != 3
        or any(not isinstance(x, str) or not x for x in pattern)
        or pattern[1].startswith("?")
        for pattern in patterns
    ):
        raise ValueError("Patterns require three nonempty strings and a fixed predicate")


def sql_matches(conn, patterns):
    validate_patterns(patterns)
    columns, conditions, values, variables = [], [], [], {}
    for i, pattern in enumerate(patterns):
        alias = "r" + str(i)
        columns.append(alias + ".relation_id")
        for field, term in zip(("subject_id", "predicate", "object_id"), pattern):
            column = alias + "." + field
            if term.startswith("?"):
                if term in variables:
                    conditions.append(column + "=" + variables[term])
                else:
                    variables[term] = column
            else:
                conditions.append(column + "=?")
                values.append(term)
    table = ",".join("relations r" + str(i) for i in range(len(patterns)))
    statement = "SELECT DISTINCT " + ",".join(columns) + " FROM " + table
    if conditions:
        statement += " WHERE " + " AND ".join(conditions)
    rows = [tuple(r) for r in conn.execute(statement + " LIMIT 200", values)]
    return sorted({item for row in rows for item in row})


def graph_matches(records, patterns):
    validate_patterns(patterns)
    index = defaultdict(list)
    for record in records:
        index[record["predicate"]].append(record)
    states, found = 0, set()

    def traverse(position, bindings, path):
        nonlocal states
        if position == len(patterns):
            found.update(path)
            return
        subject, predicate, obj = patterns[position]
        for record in index.get(predicate, []):
            states += 1
            if states > 50000:
                raise RuntimeError("Graph traversal state budget exceeded")
            new_bindings = dict(bindings)
            valid = True
            for term, actual in ((subject, record["subject_id"]), (obj, record["object_id"])):
                if term.startswith("?"):
                    if term in new_bindings and new_bindings[term] != actual:
                        valid = False
                        break
                    new_bindings[term] = actual
                elif term != actual:
                    valid = False
                    break
            if valid:
                traverse(position + 1, new_bindings, path + [record["relation_id"]])

    traverse(0, {}, [])
    return sorted(found), states


def evidence_for(relations, record_map):
    result = []
    for rid in relations:
        record = record_map[rid]
        result.extend([record["evidence_id"], *record["context_evidence_ids"]])
    return list(dict.fromkeys(result))


def context_select(ranking, texts, items, characters):
    selected, used = [], 0
    for eid in ranking:
        if eid in selected:
            continue
        size = len(texts[eid])
        if len(selected) < items and used + size <= characters:
            selected.append(eid)
            used += size
    return selected, used


def _evaluate(dataset, output, semantic_model=None, windowed_semantic=False):
    dataset, output = Path(dataset), Path(output)
    if output.exists():
        raise ValueError("Choose a fresh result directory")
    if shutil.disk_usage(output.parent).free < RESERVE + 15 * 1024**2:
        raise RuntimeError("Storage reserve plus complete experiment allowance unavailable")
    output.mkdir(parents=True)
    lifecycle = output / "lifecycle.jsonl"
    common_run = {
        "schema": "relational-retrieval-run-v1",
        "provider": "local",
        "model": semantic_model,
        "prompt_version": "deterministic-relational-v1",
        "seed": None,
        "input_manifest": str((dataset / "FREEZE.json").resolve()),
        "output": str(output.resolve()),
    }

    def emit(status):
        with lifecycle.open("a") as stream:
            stream.write(
                json.dumps(
                    {**common_run, "status": status, "at": datetime.now(timezone.utc).isoformat()}
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    emit("pending")
    freeze = json.loads((dataset / "FREEZE.json").read_bytes())
    common_run["input_manifest_sha256"] = sha((dataset / "FREEZE.json").read_bytes())
    common_run["parameters"] = freeze["budgets"]
    method_freeze = {
        "status": "FROZEN_BEFORE_METHOD_EXECUTION",
        "at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": sha(Path(__file__).read_bytes()),
        "input_manifest_sha256": common_run["input_manifest_sha256"],
        "parameters": freeze["budgets"],
        "model": semantic_model,
        "metrics": [
            "complete_known_bundle",
            "captured_known_evidence",
            "represented_known_relations",
            "bounded_abstention",
        ],
        "independent_human_gold": False,
        "windowed_semantic_ablation": windowed_semantic,
    }
    (output / "METHOD_FREEZE.json").write_text(json.dumps(method_freeze, indent=2))
    emit("running")
    for name, expected in freeze["files_sha256"].items():
        if sha((dataset / name).read_bytes()) != expected:
            raise ValueError("Frozen study artifact changed: " + name)
    conn = sqlite3.connect((dataset / "evidence.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    evidence = [
        dict(r)
        for r in conn.execute(
            "SELECT e.*,s.source_url,s.source_frame_ags_json "
            "FROM evidence e JOIN sources s USING(source_sha256) "
            "ORDER BY evidence_id"
        )
    ]
    labels = dict(conn.execute("SELECT entity_id,label FROM entities"))
    records = [
        json.loads(r[0])
        for r in conn.execute("SELECT record_json FROM relations ORDER BY relation_id")
    ]
    record_map = {r["relation_id"]: r for r in records}
    texts = {r["evidence_id"]: r["text"] for r in evidence}
    tasks = [json.loads(line) for line in (dataset / "tasks.jsonl").read_text().splitlines()]
    units = [
        {
            "unit_id": r["evidence_id"],
            "text": r["text"],
            "heading_context": r["heading"],
            "page_ref": r["page_ref"],
            "acquisition_ags": json.loads(r["source_frame_ags_json"])[0],
            "source_url": r["source_url"],
            "raw_hashes": [r["source_sha256"]],
        }
        for r in evidence
    ]
    enriched = [
        {**u, "text": " ".join([u["heading_context"] or "", u["source_url"], u["text"]])}
        for u in units
    ]
    card_units = None
    if freeze.get("include_relation_cards"):
        cards = defaultdict(list)
        for r in records:
            cards[r["evidence_id"]].append(
                labels[r["subject_id"]]
                + " | "
                + r["predicate"]
                + " | "
                + labels[r["object_id"]]
                + " | scope="
                + str(r["claim_scope_ags"])
                + " | "
                + str(r.get("reference_time"))
                + " | "
                + r["modality"]
            )
        card_units = [
            {**u, "text": u["text"] + "\n" + "\n".join(cards[u["unit_id"]])} for u in enriched
        ]
    queries = []
    for task in tasks:
        constants = list(
            dict.fromkeys(
                term
                for pattern in task["patterns"]
                for term in (pattern[0], pattern[2])
                if not term.startswith("?")
            )
        )
        queries.append(
            task["question"] + " " + " ".join(labels.get(term, term) for term in constants)
        )
    semantic = None
    if semantic_model:
        from research.bavaria.semantic import rankings

        semantic_units = [
            {**unit, "text": unit["source_url"] + " " + unit["text"]} for unit in units
        ]
        semantic = rankings(semantic_model, semantic_units, queries, output)
    semantic_windows = None
    if windowed_semantic:
        if not semantic_model:
            raise ValueError("Windowed semantic retrieval requires explicit local model")
        from research.bavaria.semantic import window_rankings

        semantic_windows = window_rankings(semantic_model, units, queries, output)
    budgets = freeze["budgets"]
    results = []
    for i, task in enumerate(tasks):
        constants = list(
            dict.fromkeys(
                term
                for pattern in task["patterns"]
                for term in (pattern[0], pattern[2])
                if not term.startswith("?")
            )
        )
        query = queries[i]
        started = time.perf_counter()
        lexical = [eid for eid, _ in lexical_rank(enriched, query, german=True)]
        lexical_seconds = time.perf_counter() - started
        started = time.perf_counter()
        sql = sql_matches(conn, task["patterns"])
        sql_seconds = time.perf_counter() - started
        started = time.perf_counter()
        graph, traversal_states = graph_matches(records, task["patterns"])
        graph_seconds = time.perf_counter() - started
        proof = evidence_for(sql, record_map)
        graph_proof = evidence_for(graph, record_map)
        # Local path context (GraphRAG-style, without LLM/community summaries)
        # prioritizes complete source-grounded path proofs, then textual context.
        arms = {
            "bm25_context": lexical,
            "sql_relations": proof,
            "graph_traversal": graph_proof,
            "graph_local_rag": graph_proof + lexical,
            "graph_enhanced_text": lexical[:2] + graph_proof + lexical[2:],
        }
        if card_units is not None:
            arms["bm25_same_relation_information"] = [
                eid for eid, _ in lexical_rank(card_units, query, german=True)
            ]
        first_pass = lexical[:3]
        # A deterministic two-tool workflow: lexical search, then the same SQL
        # logical query when that query's proof is not yet covered. No gold used.
        need_second = not set(proof).issubset(first_pass) or not sql
        arms["tools_two_step"] = proof + lexical if need_second else first_pass
        if semantic is not None:
            arms["semantic"] = semantic[i]
            for method, inputs in [
                ("hybrid_rrf", [lexical, semantic[i]]),
                ("graph_semantic_rrf", [lexical, semantic[i], graph_proof]),
                ("sql_semantic_rrf", [lexical, semantic[i], proof]),
            ]:
                scores = Counter()
                for ranking in inputs:
                    for rank, eid in enumerate(ranking[: budgets["top_candidates"]], 1):
                        scores[eid] += 1 / (budgets["rrf_constant"] + rank)
                arms[method] = sorted(scores, key=lambda eid: (-scores[eid], eid))
        if semantic_windows is not None:
            arms["semantic_windows"] = semantic_windows[i]
            window_scores = Counter()
            for ranking in [lexical, semantic_windows[i]]:
                for rank, eid in enumerate(ranking[: budgets["top_candidates"]], 1):
                    window_scores[eid] += 1 / (budgets["rrf_constant"] + rank)
            arms["hybrid_windows_rrf"] = sorted(
                window_scores, key=lambda eid: (-window_scores[eid], eid)
            )
        for method, ranking in arms.items():
            if method in ("graph_local_rag", "tools_two_step") and not sql:
                # Structured absence yields only bounded abstention, never an
                # assertion that the real-world relation does not exist.
                ranking = []
            enriched_context = card_units is not None and method not in {
                "bm25_context",
                "semantic",
                "hybrid_rrf",
                "semantic_windows",
                "hybrid_windows_rrf",
            }
            method_texts = (
                {u["unit_id"]: u["text"] for u in card_units} if enriched_context else texts
            )
            chosen, used = context_select(
                ranking,
                method_texts,
                budgets["max_evidence_items"],
                budgets["max_context_characters"],
            )
            reference = set(task["required_evidence"])
            required_relations = set(task["required_relations"])
            represented_relations = {
                rid
                for rid in required_relations
                if set(evidence_for([rid], record_map)).issubset(chosen)
            }
            semantic_answer = None
            if method in ("sql_relations", "graph_traversal", "graph_local_rag", "tools_two_step"):
                semantic_answer = (
                    "ABSTAIN_NOT_ESTABLISHED" if not sql else "SOURCE_BOUNDED_RELATION_CONTEXT"
                )
            times = {
                "shared_bm25_query_seconds": lexical_seconds,
                "sql_query_seconds": sql_seconds,
                "graph_query_seconds": graph_seconds,
                "graph_traversal_states": traversal_states,
            }
            results.append(
                {
                    "task_id": task["task_id"],
                    "family": task["family"],
                    "split": task["split"],
                    "component_ids": task["component_ids"],
                    "method": method,
                    "selected_evidence": chosen,
                    "context_characters": used,
                    "context_representation": "text_plus_provisional_relation_cards"
                    if enriched_context
                    else "original_parsed_text",
                    "selected_assertions": [
                        r["relation_id"]
                        for r in records
                        if enriched_context and r["evidence_id"] in chosen
                    ],
                    "known_required_evidence": len(reference),
                    "captured_required_evidence": len(reference & set(chosen)),
                    "complete_known_bundle": bool(reference) and reference.issubset(chosen),
                    "known_required_relations": len(required_relations),
                    "represented_required_relations": len(represented_relations),
                    "graph_sql_same_relations": graph == sql,
                    "sql_result_relations": sql,
                    "graph_result_relations": graph,
                    "answerability_disposition": semantic_answer,
                    "negative_task": not required_relations,
                    "negative_retrieval_context_count": len(chosen)
                    if not required_relations
                    else None,
                    "tool_calls": 1 + int(need_second) if method == "tools_two_step" else None,
                    "timing": times,
                    "reference_status": task["reference_status"],
                }
            )
    summary = {}
    for method in sorted({r["method"] for r in results}):
        rows = [
            r
            for r in results
            if r["method"] == method
            and not r["negative_task"]
            and r["split"] == "evaluation_provisional"
        ]
        summary[method] = {
            "tasks": len(rows),
            "complete_known_bundles": sum(r["complete_known_bundle"] for r in rows),
            "known_evidence": sum(r["known_required_evidence"] for r in rows),
            "captured_known_evidence": sum(r["captured_required_evidence"] for r in rows),
            "known_relations": sum(r["known_required_relations"] for r in rows),
            "represented_known_relations": sum(r["represented_required_relations"] for r in rows),
        }
    report = {
        "schema": "bavaria-policy-relations-evaluation-v1",
        "corpus_id": freeze["corpus_id"],
        "tasks": len(tasks),
        "sources": freeze["sources"],
        "evidence_units": len(units),
        "relations": len(records),
        "summary_evaluation_provisional_positive": summary,
        "graph_sql_all_tasks_same_relations": all(r["graph_sql_same_relations"] for r in results),
        "reference_status": "PROVISIONAL_AGENT_ORIGINAL_REVIEW_NOT_INDEPENDENT_HUMAN_GOLD",
        "confidence_intervals": None,
        "evaluation_dependency_components": len(
            {c for t in tasks if t["split"] == "evaluation_provisional" for c in t["component_ids"]}
        ),
        "uncertainty_reason": "Source-exposed provisional labels, incomplete qrels and institution/project dependence; no independent human inference.",
        "preprocessing_information": (
            "Structured arms receive the same curated relation sidecar. Text arms "
            "receive original text plus headings/URLs and the same entity "
            "constants. Extraction quality is not this experiment."
        ),
        "generation_quality": None,
        "timing_note": (
            "Per-query retrieval component times; shared lexical work recorded "
            "once, not an end-to-end model benchmark."
        ),
        "scope": f"Frozen {freeze['sources']}-source research subset, not exhaustive municipal policy or real-world absence.",
    }
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results)
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "RUN.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "model": semantic_model,
                "seed": None,
                "parameters": budgets,
                "input_manifest_sha256": sha((dataset / "FREEZE.json").read_bytes()),
                "output_sha256": sha((output / "results.jsonl").read_bytes()),
                "implementation_sha256": sha(Path(__file__).read_bytes()),
            },
            indent=2,
        )
    )
    emit("succeeded")
    return report


def evaluate(dataset, output, semantic_model=None, windowed_semantic=False):
    fresh_output = not Path(output).exists()
    try:
        return _evaluate(dataset, output, semantic_model, windowed_semantic)
    except BaseException as exc:
        lifecycle = Path(output) / "lifecycle.jsonl"
        if fresh_output and lifecycle.exists():
            last = json.loads(lifecycle.read_text().splitlines()[-1])
            with lifecycle.open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            **last,
                            "status": "failed",
                            "at": datetime.now(timezone.utc).isoformat(),
                            "error_class": type(exc).__name__,
                        }
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--semantic-model")
    parser.add_argument("--windowed-semantic", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.dataset, args.output, args.semantic_model, args.windowed_semantic),
            ensure_ascii=False,
            indent=2,
        )
    )
