"""Paired graph/relational retrieval experiment on exposed E2 tasks.

Known anchors are incomplete agent-prepared references. Metrics measure capture
of those anchors, never corpus recall or independently confirmed answer quality.
"""

import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from research.climate_policy.retrieval import intent, lexical_rank


def explore(db, node):
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM edges WHERE subject=? OR object=?
                               ORDER BY id LIMIT 200""",
            (node, node),
        ).fetchall()
        return {
            "node": node,
            "edges": [dict(row) for row in rows],
            "limit": 200,
            "warning": "Derived paths are not proof of policy relationships.",
        }


def run(db, output, model=None):
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a fresh study output directory")
    output.mkdir(parents=True)
    tasks_path = Path("research/sprints/sprint-02/acceleration/benchmark01/tasks.json")
    tasks = json.loads(tasks_path.read_bytes())["tasks"]
    with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        # Scope freeze: exactly the original 894 E2 units. New Bavaria sources do not enter.
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM evidence WHERE unit_id GLOB '09??????-*' ORDER BY unit_id"
            )
        ]
        if len(rows) != 894:
            raise ValueError("Study requires the unchanged 894-unit E2 scope")
        units = [
            dict(
                unit_id=r["unit_id"],
                text=r["text"],
                heading_context=r["heading"],
                acquisition_ags=r["source_frame_ags"],
                page_ref=r["page_ref"],
                source_url=r["source_url"],
                raw_hashes=[r["source_sha256"]],
            )
            for r in rows
        ]
        text = {r["unit_id"]: r["text"] for r in rows}
        allowed = set(text)
        snapshot = dict(conn.execute("SELECT key,value FROM metadata"))["snapshot"]
        semantic = None
        if model:
            from .semantic import rankings

            semantic = rankings(model, units, [t["question"] for t in tasks], output)
        results = []
        for task_index, task in enumerate(tasks):
            question = task["question"]
            fields = intent(question)["fields"] or ["SOURCE_STATEMENT"]
            args = ",".join("?" for _ in fields)
            lexical = [uid for uid, _ in lexical_rank(units, question, german=True)]
            context = []
            for u in units:
                enriched = dict(u)
                enriched["text"] = " ".join(
                    [u["heading_context"] or "", u["source_url"], u["text"]]
                )
                context.append(enriched)
            bm25_context = [uid for uid, _ in lexical_rank(context, question, german=True)]
            methods = [
                "bm25_german",
                "bm25_context",
                "sql_field",
                "graph_field",
                "graph_text_rrf",
                "sql_text_rrf",
            ]
            if semantic is not None:
                methods += ["semantic", "hybrid_rrf", "graph_semantic_rrf", "sql_semantic_rrf"]
            for method in methods:
                started = time.perf_counter()
                if method in (
                    "sql_field",
                    "graph_field",
                    "graph_text_rrf",
                    "sql_text_rrf",
                    "graph_semantic_rrf",
                    "sql_semantic_rrf",
                ):
                    if method in ("sql_field", "sql_text_rrf", "sql_semantic_rrf"):
                        selected = {
                            r[0]
                            for r in conn.execute(
                                f"SELECT DISTINCT unit_id FROM claims WHERE field IN ({args})",
                                fields,
                            )
                        }
                    else:
                        # A real two-edge traversal: FieldType <- Claim -> Evidence.
                        selected = {
                            r[0]
                            for r in conn.execute(
                                f"""
                            SELECT DISTINCT support.object FROM edges typed JOIN edges support
                            ON support.subject=typed.subject
                            WHERE typed.predicate='HAS_FIELD_TYPE'
                            AND support.predicate='SUPPORTED_BY'
                            AND typed.object IN ({args})""",
                                ["field:" + f for f in fields],
                            )
                        }
                    selected &= allowed
                    structured = [uid for uid in bm25_context if uid in selected]
                    if method in (
                        "graph_text_rrf",
                        "sql_text_rrf",
                        "graph_semantic_rrf",
                        "sql_semantic_rrf",
                    ):
                        scores = Counter()
                        inputs = [bm25_context, structured]
                        if method in ("graph_semantic_rrf", "sql_semantic_rrf"):
                            inputs.append(semantic[task_index])
                        for ranking in inputs:
                            for rank, uid in enumerate(ranking[:50], 1):
                                scores[uid] += 1 / (60 + rank)
                        ranked = sorted(scores, key=lambda uid: (-scores[uid], uid))
                    else:
                        ranked = structured
                elif method in ("semantic", "hybrid_rrf"):
                    ranked = semantic[task_index]
                    if method == "hybrid_rrf":
                        scores = Counter()
                        for ranking in (bm25_context, semantic[task_index]):
                            for rank, uid in enumerate(ranking[:50], 1):
                                scores[uid] += 1 / (60 + rank)
                        ranked = sorted(scores, key=lambda uid: (-scores[uid], uid))
                else:
                    ranked = lexical if method == "bm25_german" else bm25_context
                # Common 5-unit / 6000-character context. Whole units, no partial evidence.
                chosen, chars = [], 0
                for uid in ranked:
                    if uid not in allowed:
                        raise ValueError("Out-of-scope evidence in method result")
                    if len(chosen) < 5 and chars + len(text[uid]) <= 6000:
                        chosen.append(uid)
                        chars += len(text[uid])
                groups = defaultdict(set)
                for uid in task["known_reference_units"]:
                    groups[text[uid]].add(uid)
                captured = sum(bool(set(chosen) & group) for group in groups.values())
                results.append(
                    {
                        "task": task["id"],
                        "family": task["family"],
                        "method": method,
                        "selected": chosen,
                        "context_characters": chars,
                        "known_anchor_groups": len(groups),
                        "captured": captured,
                        "all_known_anchors_captured": captured == len(groups),
                        "selection_seconds": time.perf_counter() - started,
                        "latency_scope": "EXCLUDES_SHARED_BM25_PRECOMPUTATION",
                        "status": "EXPLORATORY_DEVELOPMENT_EXPOSED",
                    }
                )
        summaries = {}
        for method in sorted({r["method"] for r in results}):
            group = [r for r in results if r["method"] == method]
            summaries[method] = {
                "captured": sum(r["captured"] for r in group),
                "known_anchor_groups": sum(r["known_anchor_groups"] for r in group),
                "complete_tasks": sum(r["all_known_anchors_captured"] for r in group),
                "tasks": len(group),
            }
        equivalence = all(
            next(
                r["selected"]
                for r in results
                if r["task"] == t["id"] and r["method"] == "sql_field"
            )
            == next(
                r["selected"]
                for r in results
                if r["task"] == t["id"] and r["method"] == "graph_field"
            )
            for t in tasks
        )
    report = {
        "snapshot": snapshot,
        "source_units": 894,
        "tasks": 14,
        "summary": summaries,
        "graph_sql_rankings_identical": equivalence,
        "independent_gold": False,
        "semantic_arm": "EXECUTED_LOCAL_ONNX" if model else "NOT_EXECUTED_NO_TEXT_MODEL",
        "claim_support": None,
        "statistical_superiority": "NOT_ESTABLISHED",
        "limitation": "Field paths are not policy multi-hop reasoning; references incomplete.",
    }
    (output / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    (output / "report.json").write_text(json.dumps(report, indent=2))
    return report
