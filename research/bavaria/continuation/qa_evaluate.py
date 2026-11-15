"""Six frozen source-scoped Q&A cases through the canonical answer contract."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from research.bavaria.workspace.core import Workspace, answer

QUESTIONS = [
    (
        "direct_funder",
        "Wer finanziert die kommunale Wärmeplanung in Winkelhaid?",
        "BMWK as source-reported funder, not proof of paid money.",
    ),
    (
        "shared_funder",
        "Welche Institution wird sowohl für die Wärmeplanung Winkelhaid als auch Neuhaus/Pegnitz als Fördergeber genannt?",  # noqa: E501 — frozen text/regex literal
        "Same source-reported ministry in two distinct sources; preserve roles.",
    ),
    (
        "project_carrier",
        "Welche Institution ist Projektträger der kommunalen Wärmeplanung in Winkelhaid?",
        "ZUG is project carrier; distinguish from funder and study author.",
    ),
    (
        "funding_period",
        "Bis wann wurde die Förderphase der kommunalen Wärmeplanung Neuhaus/Pegnitz verlängert und belegt dies schon gebaute Wärmenetze?",  # noqa: E501 — frozen text/regex literal
        "31.12.2026 is the extended funding end, not completed heat infrastructure.",
    ),
    (
        "false_premise",
        "Ist in der Quellenbasis eine abgeschlossene Windpark-Inbetriebnahme durch ÜZ Mainfranken in Arnstein/Werneck belegt?",  # noqa: E501 — frozen text/regex literal
        "No verified operation in the bounded corpus; no real-world absence claim.",
    ),
    (
        "current_status",
        "Ist die kommunale Wärmeplanung Winkelhaid aktuell umgesetzt?",
        "Abstain about present implementation; historical study evidence insufficient.",
    ),
]


def evaluate(db, relations, manifest, endpoint, model, output, provider="ollama"):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a fresh Q&A result directory")
    output.mkdir(parents=True)
    workspace = Workspace(db, relations, manifest)
    frozen = {
        "schema": "bavaria-source-scoped-qa-v1",
        "status": "FROZEN_BEFORE_GENERATION",
        "questions": QUESTIONS,
        "model": model,
        "provider": provider,
        "parameters": {
            "temperature": 0,
            "seed": 42,
            "context_window_tokens": 2048 if provider == "ollama" else None,
            "num_threads": 1 if provider == "ollama" else None,
            "think": False if provider == "ollama" else None,
            "output_tokens": 384 if provider == "ollama" else 512,
            "max_context_characters": 4500 if provider == "ollama" else 18000,
        },
        "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        "independent_human_gold": False,
        "method_note": "Heuristic natural-language planner; not supplied logical oracle queries.",
    }
    (output / "FREEZE.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    results = []
    for identifier, question, _ in QUESTIONS:
        started = time.perf_counter()
        result = answer(
            workspace,
            question,
            output / "answer-runs.jsonl",
            provider=provider,
            model=model,
            ollama_url=endpoint,
        )
        (output / (identifier + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        (output / (identifier + ".md")).write_text(result["canonical_markdown"])
        canonical = result["canonical_answer"]
        results.append(
            {
                "id": identifier,
                "question": question,
                "status": canonical["status"],
                "answerability": result["answerability"],
                "seconds": time.perf_counter() - started,
                "answer_text": canonical["answer_text"],
                "citations": len(canonical.get("citations", [])),
                "semantic_entailment": "PENDING_SOURCE_FIRST_AGENT_REVIEW_NOT_HUMAN_GOLD",
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    report = {
        "schema": "bavaria-source-scoped-qa-results-v1",
        "cases": len(results),
        "results": results,
        "independent_human_labels": 0,
        "structural_citation_status_is_not_semantic_entailment": True,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--relations-db", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=("ollama", "workspace_rules"), default="ollama")
    args = parser.parse_args()
    evaluate(
        args.db,
        args.relations_db,
        args.manifest,
        args.endpoint,
        args.model,
        args.output,
        args.provider,
    )
