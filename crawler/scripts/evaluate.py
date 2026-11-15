"""
evaluate.py
===========
Stage 5 of the GraphRAG pipeline — Scientific Evaluation & Benchmarking.

Since ground-truth Q&A pairs for Bavarian municipal climate data barely exist,
this module implements a fully synthetic evaluation pipeline:

  AutoQ  →  Generate test questions spanning local → global complexity
  AutoE  →  LLM-as-a-Judge head-to-head comparison (Comprehensiveness + Diversity)
  RAGAS  →  Hard metrics: Context Recall, Faithfulness, Answer Correctness
  Claimify → Decompose answers into verifiable atomic claims → count supported claims

Output: a benchmark report (JSON + human-readable Markdown)

Usage:
    # Generate 50 test questions and evaluate the query agent
    python -m crawler.scripts.evaluate --questions 50 --output eval_report.json

    # Evaluate a specific system against a saved question set
    python -m crawler.scripts.evaluate --load questions.json --output eval_report.json

    # Compare two systems head-to-head (AutoE)
    python -m crawler.scripts.evaluate --compare system_a.json system_b.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from crawler.core.config import (
    LLM_SEED,
    LLM_TEMPERATURE,
    OLLAMA_MODEL,
    OLLAMA_URL,
    PROMPT_VERSION,
)
from crawler.core.database import (
    RuntimeDatabase,
    connect_existing_read_only,
    resolve_runtime_database,
)

OPENAI_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TestQuestion:
    question_id: str
    question: str
    question_type: Literal["local", "global", "aggregation"]
    expected_entities: list[str] = field(default_factory=list)
    cypher_ground_truth: Optional[str] = None  # for aggregation questions
    source_community: Optional[int] = None  # for global questions
    source_doc_id: Optional[str] = None  # for local questions


@dataclass
class EvalResult:
    question_id: str
    question: str
    system_answer: str
    retrieved_context: list[str]
    # RAGAS metrics
    context_recall: Optional[float] = None
    faithfulness: Optional[float] = None
    answer_correctness: Optional[float] = None
    # Claimify
    n_claims: int = 0
    n_supported_claims: int = 0
    # AutoE
    autoe_score: Optional[float] = None  # 0-1, from judge comparison
    autoe_reasoning: Optional[str] = None


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _llm(
    system: str,
    user: str,
    backend: str,
    json_mode: bool = False,
    model: Optional[str] = None,
) -> str:
    """LLM-Call mit festem Seed + Temperatur (Reproduzierbarkeit).

    `model` überschreibt das Default-Modell — genutzt von AutoQ, damit die
    Fragen-Generierung mit einem ANDEREN Modell laufen kann als das zu
    evaluierende System (sonst Zirkularitäts-Bias).
    """
    if backend == "ollama":
        import urllib.request

        payload = json.dumps(
            {
                "model": model or OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"temperature": LLM_TEMPERATURE, "seed": LLM_SEED, "num_predict": 2048},
                **({"format": "json"} if json_mode else {}),
            }
        ).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["message"]["content"]
    else:
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model=model or OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
        )
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# AutoQ — Synthetic question generation
# ---------------------------------------------------------------------------

SYSTEM_AUTOQ_LOCAL = """\
Generiere eine spezifische Frage über eine konkrete Klimaschutz-Maßnahme oder ein Projekt
aus dem gegebenen Textauszug. Die Frage soll mit einem guten RAG-System beantwortbar sein
und eine klare, faktenbasierte Antwort haben.
Antworte mit JSON: {"question": "...", "expected_entities": ["..."]}
"""

SYSTEM_AUTOQ_GLOBAL = """\
Generiere eine abstrakte Übersichts-Frage zu einem Klimaschutz-Thema, die Community-Level-Wissen
erfordert (z. B. "Welche Technologien werden in bayerischen Kommunen am häufigsten eingesetzt?").
Die Frage soll nicht aus einem einzelnen Dokument beantwortbar sein.
Antworte mit JSON: {"question": "...", "focus_domain": "Wärme|Strom|Mobilität|Finanzen|Governance"}
"""

SYSTEM_AUTOQ_AGG = """\
Generiere eine Frage, die eine exakte Zählung oder Aggregation erfordert
(z. B. "Wie viele Gemeinden haben...?", "Was ist die durchschnittliche...?").
Antworte mit JSON: {"question": "...", "cypher": "MATCH ... RETURN ..."}
"""


def generate_local_questions(
    conn: sqlite3.Connection, n: int, backend: str, model: Optional[str] = None
) -> list[TestQuestion]:
    rows = conn.execute(
        """
        SELECT s.rowid, d.document_id, s.text, s.heading_context
        FROM segments s JOIN documents_raw d ON d.document_id = s.document_id
        WHERE COALESCE(s.impact_score, 0) >= 20 AND length(s.text) >= 200
          AND COALESCE(s.is_negative, 0) = 0
        ORDER BY RANDOM() LIMIT ?
        """,
        (n,),
    ).fetchall()

    questions = []
    for rowid, doc_id, text, heading in rows:
        ctx = f"[{heading}]\n{text[:600]}" if heading else text[:600]
        try:
            raw = _llm(SYSTEM_AUTOQ_LOCAL, ctx, backend, json_mode=True, model=model)
            data = json.loads(raw)
            questions.append(
                TestQuestion(
                    question_id=f"local_{rowid}",
                    question=data["question"],
                    question_type="local",
                    expected_entities=data.get("expected_entities", []),
                    source_doc_id=doc_id,
                )
            )
        except Exception:
            continue
    return questions


def generate_global_questions(
    conn: sqlite3.Connection, n: int, backend: str, model: Optional[str] = None
) -> list[TestQuestion]:
    try:
        rows = conn.execute(
            "SELECT community_id, title, summary FROM community_reports "
            "ORDER BY importance DESC LIMIT ?",
            (n,),
        ).fetchall()
    except Exception:
        return []

    questions = []
    for cid, title, summary in rows:
        ctx = f"Community: {title}\n{summary}"
        try:
            raw = _llm(SYSTEM_AUTOQ_GLOBAL, ctx, backend, json_mode=True, model=model)
            data = json.loads(raw)
            questions.append(
                TestQuestion(
                    question_id=f"global_{cid}",
                    question=data["question"],
                    question_type="global",
                    source_community=cid,
                )
            )
        except Exception:
            continue
    return questions


def generate_aggregation_questions(
    n: int, backend: str, model: Optional[str] = None
) -> list[TestQuestion]:
    topics = [
        "Klimaschutzkonzepte in bayerischen Gemeinden",
        "Solarenergie-Projekte und Photovoltaik-Anlagen",
        "Förderprogramme (KfW, BAFA, NKI) in Kommunen",
        "Elektromobilität und Ladeinfrastruktur",
        "Wärmepumpen und Wärmenetze",
    ]
    questions = []
    for i, topic in enumerate(topics[:n]):
        try:
            raw = _llm(SYSTEM_AUTOQ_AGG, f"Thema: {topic}", backend, json_mode=True, model=model)
            data = json.loads(raw)
            questions.append(
                TestQuestion(
                    question_id=f"agg_{i}",
                    question=data["question"],
                    question_type="aggregation",
                    cypher_ground_truth=data.get("cypher"),
                )
            )
        except Exception:
            continue
    return questions


# ---------------------------------------------------------------------------
# RAGAS evaluation
# ---------------------------------------------------------------------------

SYSTEM_FAITHFULNESS = """\
Prüfe, ob die gegebene Antwort ausschließlich durch den Kontext gestützt wird.
Jede Behauptung in der Antwort muss im Kontext belegt sein.
Antworte mit JSON: {"score": 0.0-1.0, "unsupported_claims": ["..."]}
"""

SYSTEM_CONTEXT_RECALL = """\
Prüfe, ob die gegebene Antwort alle relevanten Informationen aus dem Kontext enthält.
Antworte mit JSON: {"score": 0.0-1.0, "missed_info": ["..."]}
"""

SYSTEM_ANSWER_CORRECTNESS = """\
Vergleiche die Systemantwort mit der Referenzantwort. Bewerte:
1. Faktische Korrektheit (0-1)
2. Vollständigkeit (0-1)
Antworte mit JSON: {"factual_correctness": 0.0-1.0, "completeness": 0.0-1.0}
"""


def score_faithfulness(answer: str, context: list[str], backend: str) -> float:
    ctx = "\n---\n".join(context[:5])
    raw = _llm(
        SYSTEM_FAITHFULNESS, f"ANTWORT: {answer[:1000]}\n\nKONTEXT:\n{ctx}", backend, json_mode=True
    )
    try:
        return float(json.loads(raw).get("score", 0.5))
    except Exception:
        return 0.5


def score_context_recall(answer: str, context: list[str], backend: str) -> float:
    ctx = "\n---\n".join(context[:5])
    raw = _llm(
        SYSTEM_CONTEXT_RECALL,
        f"ANTWORT: {answer[:1000]}\n\nKONTEXT:\n{ctx}",
        backend,
        json_mode=True,
    )
    try:
        return float(json.loads(raw).get("score", 0.5))
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Claimify — Atomic claim extraction + verification
# ---------------------------------------------------------------------------

SYSTEM_CLAIMIFY = """\
Extrahiere alle überprüfbaren Einzelfakten ("Claims") aus der Antwort.
Jeder Claim soll eine einzelne, konkrete Aussage sein.
Antworte mit JSON: {"claims": ["...", "..."]}
"""

SYSTEM_CLAIM_VERIFY = """\
Prüfe, ob der gegebene Claim durch den Kontext gestützt wird.
Antworte mit JSON: {"supported": true/false, "evidence": "..."}
"""


def claimify(answer: str, context: list[str], backend: str) -> tuple[int, int]:
    """Returns (n_claims, n_supported)."""
    raw = _llm(SYSTEM_CLAIMIFY, f"ANTWORT: {answer[:1500]}", backend, json_mode=True)
    try:
        claims = json.loads(raw).get("claims", [])
    except Exception:
        return 0, 0

    ctx = "\n---\n".join(context[:3])
    supported = 0
    for claim in claims[:10]:  # cap to avoid excessive LLM calls
        try:
            r = _llm(
                SYSTEM_CLAIM_VERIFY, f"CLAIM: {claim}\n\nKONTEXT:\n{ctx}", backend, json_mode=True
            )
            if json.loads(r).get("supported", False):
                supported += 1
        except Exception:
            continue
    return len(claims), supported


# ---------------------------------------------------------------------------
# AutoE — LLM-as-a-Judge head-to-head comparison
# ---------------------------------------------------------------------------

SYSTEM_AUTOE = """\
Du bist ein strenger, objektiver Richter für RAG-System-Antworten.
Vergleiche die beiden Antworten auf die gegebene Frage hinsichtlich:
  1. Umfassendheit (Completeness): Werden alle Aspekte der Frage abgedeckt?
  2. Vielfalt (Diversity): Werden verschiedene Perspektiven/Fakten genannt?
  3. Quellenvertrauen (Faithfulness): Keine Halluzinationen?

Wähle die bessere Antwort und begründe kurz.
Antworte mit JSON:
{"winner": "A"|"B"|"tie",
 "scores_A": {"completeness":0-10,"diversity":0-10,"faithfulness":0-10},
 "scores_B": {...}, "reasoning": "..."}
"""


def _autoe_single(question: str, answer_a: str, answer_b: str, backend: str) -> dict:
    prompt = f"FRAGE: {question}\n\nANTWORT A:\n{answer_a[:1500]}\n\nANTWORT B:\n{answer_b[:1500]}"
    raw = _llm(SYSTEM_AUTOE, prompt, backend, json_mode=True)
    try:
        return json.loads(raw)
    except Exception:
        return {"winner": "tie", "reasoning": "Parse error"}


def autoe_compare(question: str, answer_a: str, answer_b: str, backend: str) -> dict:
    """Positions-Bias-Kontrolle: Judge läuft zweimal mit vertauschter
    Reihenfolge. Nur ein konsistenter Sieger zählt, sonst 'tie'."""
    r1 = _autoe_single(question, answer_a, answer_b, backend)
    r2 = _autoe_single(question, answer_b, answer_a, backend)  # A/B getauscht
    w1 = r1.get("winner", "tie")
    w2_raw = r2.get("winner", "tie")
    # Rückübersetzen: "A" im getauschten Lauf ist das ursprüngliche B
    w2 = {"A": "B", "B": "A"}.get(w2_raw, "tie")
    if w1 == w2:
        winner = w1
    else:
        winner = "tie"  # inkonsistent über Positionen → kein belastbarer Sieger
    return {
        "winner": winner,
        "consistent": w1 == w2,
        "pass_1": r1,
        "pass_2_swapped": r2,
        "reasoning": r1.get("reasoning", ""),
    }


# ---------------------------------------------------------------------------
# Full evaluation run
# ---------------------------------------------------------------------------


def _git_commit() -> Optional[str]:
    try:
        import subprocess

        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or None
        )
    except Exception:
        return None


def load_questions(path: Path) -> list[TestQuestion]:
    """Lädt ein eingefrorenes Fragen-Fixture (reproduzierbare Benchmarks)."""
    data = json.loads(path.read_text())
    return [
        TestQuestion(
            question_id=q["question_id"],
            question=q["question"],
            question_type=q["question_type"],
            expected_entities=q.get("expected_entities", []),
            cypher_ground_truth=q.get("cypher_ground_truth"),
            source_community=q.get("source_community"),
            source_doc_id=q.get("source_doc_id"),
        )
        for q in data
    ]


def save_questions(questions: list[TestQuestion], path: Path) -> None:
    path.write_text(json.dumps([q.__dict__ for q in questions], ensure_ascii=False, indent=2))
    print(f"   Fragen-Fixture gespeichert: {path} (einchecken für reproduzierbare Läufe!)")


def run_evaluation(
    n_local: int = 20,
    n_global: int = 15,
    n_aggregation: int = 10,
    backend: str = "ollama",
    output_path: Optional[Path] = None,
    questions_file: Optional[Path] = None,
    save_questions_path: Optional[Path] = None,
    autoq_model: Optional[str] = None,
    db_path: str | Path | RuntimeDatabase | None = None,
) -> dict:
    raise RuntimeError(
        "The legacy query-agent evaluator is disabled until it is migrated to the canonical "
        "manifested retrieval, citation, grounding, and LLM-run-metadata contracts"
    )

    # Historical implementation retained below as an explicit migration reference.
    from crawler.scripts.query_agent import answer as qa_answer  # pragma: no cover

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_name = OLLAMA_MODEL if backend == "ollama" else OPENAI_MODEL
    database = (
        db_path if isinstance(db_path, RuntimeDatabase) else resolve_runtime_database(db_path)
    )

    # Report-Historie: nie überschreiben, jeder Lauf bekommt eine eigene Datei
    if output_path is None:
        out_dir = Path("eval_runs")
        out_dir.mkdir(exist_ok=True)
        output_path = out_dir / f"eval_report_{run_id}.json"

    if questions_file:
        print(f"📋 Lade eingefrorenes Fragen-Fixture: {questions_file}")
        questions = load_questions(questions_file)
    else:
        print("📋 Generiere Test-Fragen (AutoQ)…")
        if autoq_model is None:
            print("   ⚠  AutoQ nutzt dasselbe Modell wie das zu evaluierende System —")
            print("      Zirkularitäts-Bias! Besser: --autoq-model <anderes Modell>")
            print("      oder Fixture generieren, kuratieren und mit --load verwenden.")
        conn = connect_existing_read_only(database.path)
        questions = []
        questions += generate_local_questions(conn, n_local, backend, model=autoq_model)
        questions += generate_global_questions(conn, n_global, backend, model=autoq_model)
        questions += generate_aggregation_questions(n_aggregation, backend, model=autoq_model)
        conn.close()

    if save_questions_path:
        save_questions(questions, save_questions_path)

    print(
        f"   {len(questions)} Fragen  "
        f"(local={sum(1 for q in questions if q.question_type == 'local')}, "
        f"global={sum(1 for q in questions if q.question_type == 'global')}, "
        f"agg={sum(1 for q in questions if q.question_type == 'aggregation')})"
    )

    results = []
    n_missing_context = 0
    for q in questions:
        print(f"\n⚙️  [{q.question_type}] {q.question[:80]}…")
        try:
            # return_context=True liefert den ECHTEN Retrieval-Kontext —
            # Faithfulness gegen die Antwort selbst zu messen wäre wertlos.
            system_answer, context_snippets = qa_answer(
                q.question, backend=backend, return_context=True, db_path=database
            )
        except Exception as ex:
            system_answer = f"ERROR: {ex}"
            context_snippets = []

        result = EvalResult(
            question_id=q.question_id,
            question=q.question,
            system_answer=system_answer,
            retrieved_context=context_snippets,
        )

        if context_snippets:
            try:
                result.faithfulness = score_faithfulness(system_answer, context_snippets, backend)
                result.context_recall = score_context_recall(
                    system_answer, context_snippets, backend
                )
                result.n_claims, result.n_supported_claims = claimify(
                    system_answer, context_snippets, backend
                )
                print(
                    f"   faithfulness={result.faithfulness:.2f}  "
                    f"ctx_recall={result.context_recall:.2f}  "
                    f"claims={result.n_supported_claims}/{result.n_claims}"
                )
            except Exception as ex:
                print(f"   Metriken-Fehler: {ex}")
        else:
            # Kein Kontext → Metriken bleiben None statt gegen die Antwort
            # selbst zu messen. Wird im Report separat ausgewiesen.
            n_missing_context += 1
            print("   ⚠  Kein Retrieval-Kontext — Metriken übersprungen")

        results.append(result)
        time.sleep(0.2)

    # Aggregate
    def avg(vals):
        v = [x for x in vals if x is not None]
        return sum(v) / len(v) if v else None

    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # Reproduzierbarkeits-Metadaten — Pflicht für zitierfähige Benchmarks
        "backend": backend,
        "model": model_name,
        "seed": LLM_SEED,
        "temperature": LLM_TEMPERATURE,
        "prompt_version": PROMPT_VERSION,
        "git_commit": _git_commit(),
        "questions_file": str(questions_file) if questions_file else None,
        "autoq_model": autoq_model,
        "n_questions": len(results),
        "n_missing_context": n_missing_context,
        "avg_faithfulness": avg([r.faithfulness for r in results]),
        "avg_context_recall": avg([r.context_recall for r in results]),
        "avg_claim_support": avg(
            [r.n_supported_claims / r.n_claims if r.n_claims > 0 else None for r in results]
        ),
    }

    report = {
        "summary": summary,
        "questions": [
            {
                "question_id": r.question_id,
                "question": r.question,
                "answer": r.system_answer,
                "retrieved_context": r.retrieved_context,
                "faithfulness": r.faithfulness,
                "context_recall": r.context_recall,
                "n_claims": r.n_claims,
                "n_supported": r.n_supported_claims,
            }
            for r in results
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def fmt(v):
        return f"{v:.2f}" if v is not None else "n/a"

    print(f"\n📊 Evaluation Report gespeichert: {output_path}")
    print(f"   run_id={run_id}  model={model_name}  seed={LLM_SEED}  prompts=v{PROMPT_VERSION}")
    print(f"   avg faithfulness  = {fmt(summary['avg_faithfulness'])}")
    print(f"   avg ctx recall    = {fmt(summary['avg_context_recall'])}")
    print(f"   avg claim support = {fmt(summary['avg_claim_support'])}")
    if n_missing_context:
        print(f"   ⚠  {n_missing_context} Fragen ohne Retrieval-Kontext (Metriken = None)")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="GraphRAG Evaluation Framework (Stage 5)")
    ap.add_argument("--questions", type=int, default=45, help="Gesamtzahl Testfragen")
    ap.add_argument("--llm", choices=["ollama", "openai"], default="ollama")
    ap.add_argument(
        "--output", default=None, help="Ausgabedatei (Default: eval_runs/eval_report_<run_id>.json)"
    )
    ap.add_argument(
        "--load",
        default=None,
        help="Eingefrorenes Fragen-Fixture laden (reproduzierbarer Benchmark)",
    )
    ap.add_argument(
        "--save-questions",
        default=None,
        metavar="PATH",
        help="Generierte Fragen als Fixture speichern (zum Kuratieren + Einchecken)",
    )
    ap.add_argument(
        "--autoq-model",
        default=None,
        metavar="MODEL",
        help="Anderes Modell für die Fragen-Generierung (vermeidet Zirkularitäts-Bias)",
    )
    ap.add_argument(
        "--compare",
        nargs=2,
        default=None,
        help="Zwei Systemantwort-JSONs vergleichen (AutoE, mit Positions-Swap)",
    )
    ap.add_argument("--db", default=None, help="SQLite database; overrides KLIMA_DB_PATH")
    args = ap.parse_args(argv)

    if args.compare:
        a_data = json.loads(Path(args.compare[0]).read_text())
        b_data = json.loads(Path(args.compare[1]).read_text())
        wins = {"A": 0, "B": 0, "tie": 0}
        n_inconsistent = 0
        for qa, qb in zip(a_data["questions"], b_data["questions"]):
            r = autoe_compare(qa["question"], qa["answer"], qb["answer"], args.llm)
            wins[r.get("winner", "tie")] += 1
            if not r.get("consistent", True):
                n_inconsistent += 1
            print(
                f"  {qa['question'][:60]}…  → Winner: {r.get('winner')}"
                f"{'' if r.get('consistent', True) else '  (positions-inkonsistent → tie)'}"
            )
        print(f"\n🏆 AutoE Ergebnis: A={wins['A']}  B={wins['B']}  Tie={wins['tie']}")
        if n_inconsistent:
            print(
                f"   {n_inconsistent} Urteile waren positionsabhängig und wurden als Tie gewertet"
            )
    else:
        n = args.questions
        run_evaluation(
            n_local=n // 3,
            n_global=n // 3,
            n_aggregation=n - 2 * (n // 3),
            backend=args.llm,
            output_path=Path(args.output) if args.output else None,
            questions_file=Path(args.load) if args.load else None,
            save_questions_path=Path(args.save_questions) if args.save_questions else None,
            autoq_model=args.autoq_model,
            db_path=args.db,
        )


if __name__ == "__main__":
    main()
