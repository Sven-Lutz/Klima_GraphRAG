"""
llm_judge.py
============

Run the existing GraphRAG query agent on benchmark questions and evaluate each
answer with a local Ollama model acting as an LLM judge.

Expected input:
    A CSV file containing at least a `question` column.

Optional columns that are automatically used when present:
    municipality
    human_label
    expected_answer
    gold_answer
    evidence
    gold_evidence
    source_url
    notes

Example:
    python -m crawler.scripts.llm_judge ^
        --input evaluation/manual_benchmark.csv ^
        --output evaluation/llm_judge_results.csv ^
        --limit 10

The script:
  1. reads each benchmark question,
  2. calls query_agent.answer(..., return_context=True),
  3. sends question, retrieved context, GraphRAG answer and optional human
     reference data to Ollama,
  4. writes structured judge scores and explanations to CSV.

Important:
    LLM-as-a-Judge is not ground truth. Compare a subset of judge decisions
    against human evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from crawler.core.config import (
    LLM_SEED,
    OLLAMA_MODEL,
    OLLAMA_URL,
)
from crawler.scripts.query_agent import answer


JUDGE_SYSTEM_PROMPT = """\
Du bist ein strenger, unabhängiger Evaluator für ein kommunales
GraphRAG-System.

Du bewertest eine Antwort ausschließlich anhand:
1. der Frage,
2. des abgerufenen Kontexts,
3. optionaler menschlicher Referenzdaten.

Bewertungsregeln:

- correctness:
  Ist die Antwort sachlich korrekt? Wenn eine menschliche Referenz vorliegt,
  muss sie berücksichtigt werden.

- groundedness:
  Sind alle konkreten Behauptungen durch den abgerufenen Kontext belegt?

- relevance:
  Beantwortet die Antwort direkt die gestellte Frage?

- completeness:
  Deckt die Antwort die wichtigen Informationen aus dem Kontext ab, ohne
  unbelegte Ergänzungen?

- status_accuracy:
  Unterscheidet die Antwort korrekt zwischen umgesetzt, beschlossen, geplant,
  in Bearbeitung, beendet und unklar?

- principal_accuracy:
  Wird korrekt unterschieden, ob die Maßnahme von der Kommune, einem
  Stadtwerk, dem Land, dem Bund oder einer anderen Organisation stammt?

- hallucination:
  Wie stark enthält die Antwort unbelegte oder erfundene Behauptungen?
  Hier bedeutet 1 = keine Halluzination und 5 = starke Halluzination.

Verwende für alle anderen Qualitätswerte eine Skala von 1 bis 5:
1 = sehr schlecht
2 = schlecht
3 = teilweise korrekt
4 = gut
5 = sehr gut

Wichtig:
- Fehlender Kontext darf nicht als Beweis für das Nichtvorhandensein einer
  Maßnahme behandelt werden.
- "Kein ausreichender Beleg gefunden" ist angemessen, wenn der Kontext die
  Aussage nicht trägt.
- Bestrafe Vermutungen wie "es kann davon ausgegangen werden".
- Gib nur gültiges JSON aus, ohne Markdown oder zusätzlichen Text.

Erforderliches JSON-Format:
{
  "correctness": 1,
  "groundedness": 1,
  "relevance": 1,
  "completeness": 1,
  "status_accuracy": 1,
  "principal_accuracy": 1,
  "hallucination": 1,
  "overall": 1.0,
  "verdict": "pass|partial|fail",
  "supported_claims": ["..."],
  "unsupported_claims": ["..."],
  "missing_information": ["..."],
  "reason": "Kurze, konkrete Begründung"
}
"""


SCORE_FIELDS = (
    "correctness",
    "groundedness",
    "relevance",
    "completeness",
    "status_accuracy",
    "principal_accuracy",
    "hallucination",
)


REFERENCE_COLUMNS = (
    "human_label",
    "expected_answer",
    "gold_answer",
    "evidence",
    "gold_evidence",
    "source_url",
    "notes",
)


def _clip(text: Any, limit: int) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _join_contexts(contexts: Iterable[str], max_chars: int = 14000) -> str:
    parts: list[str] = []
    used = 0

    for index, context in enumerate(contexts, start=1):
        clean = str(context or "").strip()
        if not clean:
            continue

        block = f"[CONTEXT {index}]\n{clean}"
        remaining = max_chars - used

        if remaining <= 0:
            break

        if len(block) > remaining:
            block = block[:remaining] + "\n...[truncated]"

        parts.append(block)
        used += len(block)

    return "\n\n".join(parts)


def _human_reference(row: dict[str, str]) -> str:
    blocks: list[str] = []

    for column in REFERENCE_COLUMNS:
        value = str(row.get(column, "") or "").strip()
        if value:
            blocks.append(f"{column}: {value}")

    if not blocks:
        return "Keine menschlichen Referenzdaten vorhanden."

    return "\n".join(blocks)


def _judge_user_prompt(
    question: str,
    graph_answer: str,
    contexts: list[str],
    row: dict[str, str],
) -> str:
    municipality = str(row.get("municipality", "") or "").strip()
    municipality_text = municipality or "Nicht angegeben"

    return f"""\
FRAGE:
{_clip(question, 2000)}

GEMEINDE:
{_clip(municipality_text, 500)}

ABGERUFENER KONTEXT:
{_join_contexts(contexts)}

GRAPHRAG-ANTWORT:
{_clip(graph_answer, 6000)}

MENSCHLICHE REFERENZ:
{_clip(_human_reference(row), 5000)}

Bewerte jetzt die GraphRAG-Antwort nach den vorgegebenen Kriterien.
"""


def _call_ollama_judge(
    system_prompt: str,
    user_prompt: str,
    model: str,
    timeout: int = 180,
    max_retries: int = 3,
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "seed": LLM_SEED,
                "num_predict": 1800,
            },
        }
    ).encode("utf-8")

    last_error: Exception = RuntimeError("Ollama judge request failed")

    for attempt in range(max_retries):
        request = urllib.request.Request(
            f"{OLLAMA_URL.rstrip('/')}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))

            return str(body["message"]["content"])

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Judge request failed after retries: {last_error}")


def _extract_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1

        if start == -1 or end <= start:
            return {}

        try:
            parsed = json.loads(text[start:end])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def _score(value: Any, default: int = 1) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = default

    return min(5, max(1, numeric))


def _normalise_judgement(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for field in SCORE_FIELDS:
        result[field] = _score(data.get(field), default=1)

    quality_scores = [
        result["correctness"],
        result["groundedness"],
        result["relevance"],
        result["completeness"],
        result["status_accuracy"],
        result["principal_accuracy"],
        6 - result["hallucination"],
    ]
    calculated_overall = sum(quality_scores) / len(quality_scores)

    try:
        provided_overall = float(data.get("overall"))
        if not math.isfinite(provided_overall):
            raise ValueError
        result["overall"] = round(min(5.0, max(1.0, provided_overall)), 2)
    except (TypeError, ValueError):
        result["overall"] = round(calculated_overall, 2)

    verdict = str(data.get("verdict", "") or "").strip().lower()
    if verdict not in {"pass", "partial", "fail"}:
        if result["overall"] >= 4.0 and result["hallucination"] <= 2:
            verdict = "pass"
        elif result["overall"] >= 2.5:
            verdict = "partial"
        else:
            verdict = "fail"

    result["verdict"] = verdict

    for field in (
        "supported_claims",
        "unsupported_claims",
        "missing_information",
    ):
        value = data.get(field, [])
        if not isinstance(value, list):
            value = [str(value)] if value else []
        result[field] = json.dumps(
            [str(item) for item in value],
            ensure_ascii=False,
        )

    result["reason"] = str(data.get("reason", "") or "").strip()

    return result


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)

        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")

        fieldnames = [str(name) for name in reader.fieldnames]
        rows = [
            {
                str(key): "" if value is None else str(value)
                for key, value in row.items()
            }
            for row in reader
        ]

    if "question" not in fieldnames:
        raise ValueError(
            "Input CSV must contain a column named 'question'."
        )

    return fieldnames, rows


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()

    keys: set[tuple[str, str]] = set()

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                keys.add(
                    (
                        str(row.get("municipality", "") or "").strip(),
                        str(row.get("question", "") or "").strip(),
                    )
                )
    except Exception:
        return set()

    return keys


def _output_fields(input_fields: list[str]) -> list[str]:
    additional = [
        "graphrag_answer",
        "retrieved_context",
        "context_count",
        "judge_model",
        "correctness",
        "groundedness",
        "relevance",
        "completeness",
        "status_accuracy",
        "principal_accuracy",
        "hallucination",
        "overall",
        "verdict",
        "supported_claims",
        "unsupported_claims",
        "missing_information",
        "judge_reason",
        "run_error",
    ]

    fields = list(input_fields)
    for field in additional:
        if field not in fields:
            fields.append(field)

    return fields


def run_evaluation(
    input_path: Path,
    output_path: Path,
    judge_model: str,
    graph_backend: str,
    limit: int | None,
    resume: bool,
    verbose: bool,
) -> None:
    input_fields, rows = _read_csv(input_path)
    output_fields = _output_fields(input_fields)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    processed = _existing_keys(output_path) if resume else set()
    write_header = not output_path.exists() or not resume

    selected_rows = rows[:limit] if limit is not None else rows

    with output_path.open(
        "a" if resume else "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=output_fields,
            extrasaction="ignore",
        )

        if write_header:
            writer.writeheader()

        for index, row in enumerate(selected_rows, start=1):
            question = str(row.get("question", "") or "").strip()
            municipality = str(
                row.get("municipality", "") or ""
            ).strip()
            key = (municipality, question)

            if not question:
                continue

            if resume and key in processed:
                if verbose:
                    print(f"[{index}] Skipping existing row: {municipality}")
                continue

            result_row: dict[str, Any] = dict(row)
            result_row["judge_model"] = judge_model
            result_row["run_error"] = ""

            print(
                f"[{index}/{len(selected_rows)}] "
                f"{municipality or 'unknown municipality'}: {question}"
            )

            try:
                graph_result = answer(
                    question,
                    backend=graph_backend,
                    verbose=verbose,
                    return_context=True,
                )

                if isinstance(graph_result, tuple):
                    graph_answer, contexts = graph_result
                else:
                    graph_answer, contexts = graph_result, []

                contexts = [str(item) for item in contexts]

                result_row["graphrag_answer"] = graph_answer
                result_row["retrieved_context"] = _join_contexts(
                    contexts,
                    max_chars=30000,
                )
                result_row["context_count"] = len(contexts)

                judge_prompt = _judge_user_prompt(
                    question=question,
                    graph_answer=str(graph_answer),
                    contexts=contexts,
                    row=row,
                )

                raw_judgement = _call_ollama_judge(
                    system_prompt=JUDGE_SYSTEM_PROMPT,
                    user_prompt=judge_prompt,
                    model=judge_model,
                )
                judgement = _normalise_judgement(
                    _extract_json(raw_judgement)
                )

                for field in SCORE_FIELDS:
                    result_row[field] = judgement[field]

                result_row["overall"] = judgement["overall"]
                result_row["verdict"] = judgement["verdict"]
                result_row["supported_claims"] = judgement[
                    "supported_claims"
                ]
                result_row["unsupported_claims"] = judgement[
                    "unsupported_claims"
                ]
                result_row["missing_information"] = judgement[
                    "missing_information"
                ]
                result_row["judge_reason"] = judgement["reason"]

                print(
                    f"  verdict={judgement['verdict']} "
                    f"overall={judgement['overall']} "
                    f"groundedness={judgement['groundedness']} "
                    f"hallucination={judgement['hallucination']}"
                )

            except Exception as exc:
                result_row["run_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                print(f"  ERROR: {result_row['run_error']}")

            writer.writerow(result_row)
            handle.flush()

    print(f"\nDone. Results written to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate GraphRAG answers with an Ollama LLM judge."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Benchmark CSV containing at least a 'question' column.",
    )
    parser.add_argument(
        "--output",
        default="evaluation/llm_judge_results.csv",
        help="Output CSV path.",
    )
    parser.add_argument(
        "--judge-model",
        default=OLLAMA_MODEL,
        help="Ollama model used as judge.",
    )
    parser.add_argument(
        "--graph-llm",
        choices=["ollama", "openai"],
        default="ollama",
        help="Backend used by query_agent for GraphRAG answers.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N rows.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append and skip municipality/question pairs already evaluated.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show query-agent routing and critic output.",
    )

    args = parser.parse_args()

    run_evaluation(
        input_path=Path(args.input),
        output_path=Path(args.output),
        judge_model=args.judge_model,
        graph_backend=args.graph_llm,
        limit=args.limit,
        resume=args.resume,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
