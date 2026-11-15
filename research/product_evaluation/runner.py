"""Incremental evaluation execution and safe public dashboard, no provider implicit calls."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from research.bavaria.workspace.core import write_guard
from research.gold_studio.retrieval_benchmark.contract import digest

from .catalog import ERROR_CLASSES, build_catalog, load_questions, public_manifest
from .metrics import evaluate_case, summarize_results


def execute(
    directory,
    output,
    execute_question,
    *,
    split="regression",
    method="lexical",
    reference_by_id=None,
    release_receipt=None,
    limit=None,
    selected_question_ids=None,
):
    """execute_question(question_record)->normalized output; callable controls provider policy.

    Uses one output directory per immutable configuration. Completed cases resume only
    with exactly the same dataset, method and reference digest. Failures remain failed.
    """
    directory, output = Path(directory), Path(output)
    rows = load_questions(directory, split, release_receipt=release_receipt)
    if selected_question_ids is not None:
        if not isinstance(selected_question_ids, list) or len(selected_question_ids) != len(
            set(selected_question_ids)
        ):
            raise ValueError("Distinct selected question IDs required")
        by_id = {q["question_id"]: q for q in rows}
        if set(selected_question_ids) - by_id.keys():
            raise ValueError("Selected question outside evaluation split")
        rows = [by_id[key] for key in selected_question_ids]
    reference_by_id = reference_by_id or {}
    if set(reference_by_id) - {q["question_id"] for q in rows}:
        raise ValueError("Reference IDs outside selected split")
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("Positive limit required")
        rows = rows[:limit]
    config = dict(
        schema="product-evaluation-run-v1",
        split=split,
        method=method,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        dataset_sha256=hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
        reference_sha256=digest(reference_by_id),
        questions=[q["question_id"] for q in rows],
    )
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "configuration.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError("Resume configuration/reference mismatch; use fresh run")
    _write(config_path, config)
    path = output / "results.jsonl"
    previous = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    completed = {r["question_id"] for r in previous if r["status"] == "succeeded"}
    for row in rows:
        if row["question_id"] in completed:
            continue
        started_at = datetime.now(timezone.utc).isoformat()
        start = time.perf_counter()
        record = dict(
            question_id=row["question_id"],
            query_sha256=row["query_sha256"],
            snapshot_id=row["snapshot_id"],
            method=method,
            split=split,
            provider="none-unless-recorded-by-executor",
            model=None,
            prompt_version=None,
            parameters={},
            seed=None,
            started_at=started_at,
        )
        record["run_id"] = digest([config, row["question_id"], len(previous)])
        record["input_identifiers"] = [row["question_id"], row["query_sha256"]]
        _append(output / "lifecycle.jsonl", {**record, "status": "running"})
        try:
            predicted = execute_question(row)
            predicted.setdefault("latency_ms", (time.perf_counter() - start) * 1000)
            errors = predicted.pop("errors", [])
            if set(errors) - set(ERROR_CLASSES):
                raise ValueError("Unknown error taxonomy")
            record.update(
                status="succeeded",
                output=predicted,
                errors=errors,
                metrics=evaluate_case(predicted, reference_by_id.get(row["question_id"])),
                output_sha256=digest(predicted),
            )
            for key in ["provider", "model", "prompt_version", "parameters", "seed"]:
                if key in predicted:
                    record[key] = predicted[key]
        except Exception as exc:
            record.update(
                status="failed",
                error_class=type(exc).__name__,
                errors=["user_experience"],
                metrics={},
            )
        record["ended_at"] = datetime.now(timezone.utc).isoformat()
        record["output_identifiers"] = (
            [record["output_sha256"]] if record.get("output_sha256") else []
        )
        _append(path, record)
        _append(
            output / "lifecycle.jsonl",
            {key: value for key, value in record.items() if key not in {"output", "metrics"}},
        )
        previous.append(record)
    # Latest attempt per question; failures are retained in event history.
    latest = {r["question_id"]: r for r in previous}
    summary = summarize_results(list(latest.values()))
    summary.update(
        configuration=config,
        cases=len(latest),
        succeeded=sum(r["status"] == "succeeded" for r in latest.values()),
        failed=sum(r["status"] == "failed" for r in latest.values()),
        results_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    _write(output / "summary.json", summary)
    (output / "dashboard.html").write_text(render_dashboard(summary))
    return summary


def _write(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    write_guard(path, len(raw.encode()))
    path.write_text(raw)


def _append(path, value):
    raw = json.dumps(value, ensure_ascii=False) + "\n"
    write_guard(path, len(raw.encode()))
    with path.open("a") as stream:
        stream.write(raw)


def dashboard_payload(directory, run_directories=()):
    manifest = public_manifest(directory)
    runs = []
    for folder in run_directories:
        summary = json.loads((Path(folder) / "summary.json").read_text())
        # Holdout results only after release; no raw queries or evidence in dashboard.
        runs.append(summary)
    return dict(
        catalog=manifest,
        runs=runs,
        error_taxonomy=list(ERROR_CLASSES),
        annotation_status="GENERATED_QUESTIONS_ARE_NOT_HUMAN_GOLD",
    )


def render_dashboard(summary):
    esc = lambda x: html.escape(str(x))  # noqa: E731
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(m.get(k))}</td>"
            for k in ["method", "metric", "label_origin", "value", "evaluated", "total", "missing"]
        )
        + "</tr>"
        for m in summary.get("metrics", [])
    )
    bars = "".join(
        f'<li>{esc(name)}: <meter min="0" max="{max(1, summary.get("cases", 1))}" '
        f'value="{count}">{count}</meter> {count}</li>'
        for name, count in summary.get("errors", {}).items()
    )
    return (
        '<!doctype html><html lang="de"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Klima Research Evaluation</title><style>"
        "body{font:16px system-ui;background:#fff;color:#173b32;"
        "max-width:80rem;margin:auto;padding:2rem}"
        "table{border-collapse:collapse}td,th{padding:.6rem;border:1px solid #ccc;text-align:left}"
        ".scroll{overflow:auto}meter{width:12rem}</style><h1>Evaluation</h1>"
        "<p>Generierte Fragen und synthetische Urteile sind keine unabhängige "
        "Human-Gold-Validierung. "
        "Fehlende Referenzen bleiben unbewertet. Methoden und Labelursprünge werden getrennt.</p>"
        f"<p>Fälle: {esc(summary.get('cases'))}; erfolgreich: {esc(summary.get('succeeded'))}; "
        f"fehlgeschlagen: {esc(summary.get('failed'))}</p><h2>Fehlerklassen</h2><ul>{bars}</ul>"
        '<h2>Metriken</h2><div class="scroll"><table><thead><tr>'
        "<th>Methode</th><th>Metrik</th><th>Labelursprung</th><th>Wert</th><th>Bewertet</th>"
        "<th>Gesamt</th><th>Fehlende Referenzen</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></html>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Fresh immutable catalog output")
    parser.add_argument("--catalog", type=Path, help="Existing catalog to evaluate")
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument(
        "--split", choices=["development", "regression", "adversarial"], default="regression"
    )
    parser.add_argument("--method", default="lexical_expanded")
    parser.add_argument("--index", type=Path, help="Explicit existing snapshot-bound term index")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--local-state",
        type=Path,
        help="Explicit existing local product state for answer evaluation",
    )
    parser.add_argument("--gold-export", type=Path)
    args = parser.parse_args()
    from research.gold_studio.retrieval_benchmark.preview_readiness import workspace

    if args.catalog:
        if not args.run_directory:
            parser.error("--catalog requires --run-directory")
        report = run_local(
            args.manifest,
            args.catalog,
            args.run_directory,
            method=args.method,
            split=args.split,
            limit=args.limit,
            local_state=args.local_state,
            gold_export=args.gold_export,
            index_path=args.index,
        )
        print(json.dumps({key: report[key] for key in ["cases", "succeeded", "failed"]}))
        return
    if not args.output:
        parser.error("Provide --output to build or --catalog/--run-directory to evaluate")
    report = build_catalog(workspace(args.manifest), args.output)
    print(
        json.dumps(
            {
                "questions": report["questions"],
                "splits": {key: row["count"] for key, row in report["splits"].items()},
            },
            indent=2,
        )
    )


def run_local(
    manifest,
    catalog,
    output,
    *,
    method="lexical_expanded",
    split="regression",
    limit=None,
    local_state=None,
    gold_export=None,
    index_path=None,
):
    """Explicit local CLI composition; no implicit provider/network calls or holdout release."""
    from crawler.core.retrieval import RetrievalMode
    from crawler.web.bavaria import create_bavaria_service
    from crawler.web.local_index import LocalIndex
    from crawler.web.service import QueryOptions

    from .review_bridge import reference_from_review_export

    if split == "holdout":
        raise PermissionError("Development CLI cannot unseal holdout")
    mode = RetrievalMode(method)
    product = None
    if local_state is not None:
        from crawler.web.local_product import create_local_product

        local_state = Path(local_state).resolve(strict=True)
        app = create_local_product(Path(manifest), local_state, technical=True)
        service, product = app.state.research_service, app.state.research_product
        if product.engine.provider_gate.provider != "local_extractive":
            raise ValueError("Product evaluator permits only explicitly local synthesis")
    else:
        service = create_bavaria_service(Path(manifest))
        # An explicit snapshot-bound persistent index is reused only if present.
        index = Path(index_path) if index_path is not None else None
        if index is None or not index.exists():
            service.close()
            raise ValueError("Explicit local-research term index is required; no implicit rebuild")
        service.workspace.discovery_index = LocalIndex(service.workspace, index)
    references = {}
    try:
        questions = load_questions(catalog, split)
        if gold_export:
            references = reference_from_review_export(
                json.loads(Path(gold_export).read_text()), questions, skip_other_tasks=True
            )

        def predict(question):
            options = QueryOptions(
                question["question"],
                mode,
                8,
                8,
                24000,
                product is not None,
                tuple(question["municipality_ids"]),
            )
            if product:
                from crawler.web.evaluation_dashboard import normalize_product_result

                return normalize_product_result(product.ask(options, "technical_evaluation"))
            result = service.execute(options)
            return dict(
                ranked_ids=[e["source_id"] for e in result["evidence"]],
                claims=[],
                retrieval_digest=digest(result["retrieval_result"]["items"]),
                scope_ids=question["municipality_ids"],
                provider="none",
                model="none",
                prompt_version="none",
                input_tokens=0,
                output_tokens=0,
                provider_cost=0,
            )

        return execute(
            catalog,
            output,
            predict,
            split=split,
            method=("product:" if product else "scientific:") + method,
            reference_by_id=references,
            limit=limit,
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
