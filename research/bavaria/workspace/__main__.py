"""Question-driven, read-only research over the available Bavaria corpus."""

import argparse
import json
from pathlib import Path

from .core import Workspace, answer, make_manifest, write_guard


def render_markdown(result: dict) -> str:
    if "canonical_markdown" in result:
        canonical = result["canonical_markdown"]
        plan = result["question_plan"]
        scope = ", ".join(plan["scope_ags"]) or "explicit relation corpus"
        return (
            "Vorläufige Quellenanalyse — Agentenreview, keine unabhängige Fachvalidierung.\n\n"
            f"Generation qualification: {result.get('generation_qualification', 'unassessed')}\n\n"
            f"Scope: {scope}\n\nAnswerability: {result['answerability']}\n\n"
            + canonical
            + "\nResearch limitations: "
            + " ".join(result["limitations"])
            + "\n"
        )
    if "results" in result:
        lines = [
            "Evidence discovery — no complete factual answer",
            "",
            f"Question: {result.get('question', '')}",
            "",
            "The following original-linked passages require interpretation; frame ≠ claim scope.",
            "",
        ]
        for row in result["results"]:
            lines.extend(
                [
                    f"- {row['unit_id']} — {row['source_url']}",
                    f"  Page/heading: {row['page_ref'] or row['heading'] or 'unknown'}",
                    f"  Original SHA-256: {row['sha256']}",
                    "  " + row["text"],
                    "",
                ]
            )
        return "\n".join(lines)
    return "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("bind", "search", "ask", "municipality", "compare", "paths", "temporal"):
        command = subcommands.add_parser(name)
        command.add_argument(
            "--db", required=True, help="Explicit existing Bavaria acquisition ledger"
        )
        command.add_argument(
            "--source-db",
            action="append",
            default=[],
            help="Additional immutable source ledger; repeat explicitly",
        )
        command.add_argument(
            "--disposition-db", help="Completed coverage overlay, also selected as source-db"
        )
        command.add_argument(
            "--relations-db", help="Explicit existing original-reviewed relation sidecar"
        )
        if name != "bind":
            command.add_argument("--manifest", help="Immutable workspace binding; required for ask")
            command.add_argument("--output", help="Fresh local JSON/Markdown export path")
            command.add_argument("--format", choices=("json", "markdown"), default="json")
        if name in ("search", "ask"):
            command.add_argument("question")
            command.add_argument("--ags", action="append", help="Repeat for multiple official AGS")
            command.add_argument("--top-k", type=int, default=8)
        if name == "ask":
            command.add_argument("--run-log", required=True)
            command.add_argument(
                "--provider", choices=("workspace_rules", "ollama"), default="workspace_rules"
            )
            command.add_argument(
                "--experimental-unqualified",
                action="store_true",
                help="Acknowledge that local generation has not passed semantic qualification",
            )
            command.add_argument("--model", default=None)
            command.add_argument("--ollama-url", default=None)
        elif name in ("municipality", "compare"):
            command.add_argument("--ags", action="append", required=True)
        elif name == "paths":
            command.add_argument("--start", required=True)
            command.add_argument("--target")
            command.add_argument("--hops", type=int, default=3)
            command.add_argument("--limit", type=int, default=20)
        elif name == "temporal":
            command.add_argument("--entity", required=True)
        elif name == "bind":
            command.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "bind":
        print(
            json.dumps(
                make_manifest(
                    args.db, args.relations_db, args.output, args.source_db, args.disposition_db
                ),
                indent=2,
            )
        )
        return
    if args.output:
        if Path(args.output).exists():
            parser.error("Refuse to overwrite an existing research export")
        write_guard(args.output)
    workspace = Workspace(
        args.db, args.relations_db, args.manifest, args.source_db, args.disposition_db
    )
    if args.command == "search":
        result = workspace.discover(args.question, args.ags, args.top_k)
    elif args.command == "ask":
        if args.provider == "ollama" and not args.experimental_unqualified:
            parser.error(
                "Generator qualification failed; experimental use requires "
                "--experimental-unqualified"
            )
        if args.provider == "ollama" and (not args.model or not args.ollama_url):
            parser.error("Local Ollama answers require explicit --model and --ollama-url")
        result = answer(
            workspace,
            args.question,
            args.run_log,
            args.ags,
            args.top_k,
            args.provider,
            args.model or "workspace-relation-composer-v1",
            args.ollama_url,
        )
    elif args.command in ("municipality", "compare"):
        result = workspace.dossiers(args.ags)
    elif args.command == "paths":
        result = workspace.graph_paths(args.start, args.target, args.hops, args.limit)
    else:
        result = workspace.temporal(args.entity)
    rendered = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.format == "json"
        else render_markdown(result)
    )
    if args.output:
        size = len((rendered + "\n").encode())
        if size > 8 * 1024**2:
            parser.error("Research export exceeds the eight-MiB bound; reduce scope/top-k")
        write_guard(args.output, size)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered + "\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
