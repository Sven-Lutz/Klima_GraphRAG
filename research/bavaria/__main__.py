"""Explicit-path orchestration for the Bavaria research acquisition programme."""

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from .acquisition import initialize, wave
from .registry import build_registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    registry = commands.add_parser("registry")
    registry.add_argument("--official", required=True)
    registry.add_argument("--seeds", required=True)
    registry.add_argument("--output", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True)
    init.add_argument("--registry", required=True)
    crawl = commands.add_parser("crawl")
    crawl.add_argument("--db", required=True)
    crawl.add_argument("--root", required=True)
    crawl.add_argument("--limit", type=int, default=0)
    crawl.add_argument("--max-docs", type=int, default=4)
    crawl.add_argument("--workers", type=int, default=8)
    for command in (
        "coverage",
        "project",
        "candidates",
        "import-e2",
        "search",
        "graph",
        "study",
        "municipality",
        "compare",
    ):
        sub = commands.add_parser(command)
        sub.add_argument("--db", required=True)
        if command in ("coverage", "project", "candidates", "study"):
            sub.add_argument("--output", required=True)
        if command == "study":
            sub.add_argument("--model")
        if command == "import-e2":
            sub.add_argument("--export", required=True)
        if command == "search":
            sub.add_argument("question")
            sub.add_argument("--ags")
        if command == "graph":
            sub.add_argument("--node", required=True)
        if command in ("municipality", "compare"):
            sub.add_argument("--ags", nargs="+", required=True)
    args = parser.parse_args()
    if args.command == "registry":
        result = build_registry(args.official, args.seeds, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "init":
        initialize(args.db, args.registry)
    elif args.command == "crawl":
        if not Path(args.db).is_file():
            parser.error("Explicit database does not exist; run init first")
        if not 1 <= args.workers <= 12 or not 1 <= args.max_docs <= 20:
            parser.error("workers must be 1..12; max-docs must be 1..20")
        wave(args.db, args.root, args.limit, args.max_docs, args.workers)
    else:
        from .analysis import benchmark_candidates, build, coverage, import_e2, search

        if not Path(args.db).is_file():
            parser.error("Explicit database does not exist")
        if args.command in ("project", "candidates", "study"):
            from .runs import stage

            lifecycle = stage(args)
        else:
            lifecycle = nullcontext()
        with lifecycle:
            result = execute_stage(args, benchmark_candidates, build, coverage, import_e2, search)
        print(json.dumps(result, ensure_ascii=False, indent=2))


def execute_stage(args, benchmark_candidates, build, coverage, import_e2, search):
    if args.command == "coverage":
        result = coverage(args.db, args.output)
    elif args.command == "project":
        result = build(args.db, args.output)
    elif args.command == "candidates":
        result = benchmark_candidates(args.db, args.output)
    elif args.command == "import-e2":
        result = import_e2(args.db, args.export)
    elif args.command == "search":
        result = search(args.db, args.question, args.ags)
    elif args.command in ("municipality", "compare"):
        from .analysis import municipality_dossiers

        result = municipality_dossiers(args.db, args.ags)
    elif args.command == "study":
        from .study import run

        result = run(args.db, args.output, args.model)
    else:
        from .study import explore

        result = explore(args.db, args.node)
    return result


if __name__ == "__main__":
    main()
