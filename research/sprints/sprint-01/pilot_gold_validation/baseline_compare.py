"""Compare static diagnostics on identical baseline source paths, then rerun checks."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BASE = "d7a25d42129d1816cc2b31317c21a88f360d94b0"
DEFAULT_PYTHON = "/tmp/klima-m3-verification-20261102/bin/python"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def compare_diagnostics(baseline, current):
    left = Counter(json.dumps(row, sort_keys=True) for row in baseline)
    right = Counter(json.dumps(row, sort_keys=True) for row in current)
    return {
        "baseline_diagnostics": len(baseline),
        "current_diagnostics": len(current),
        "unchanged": sum((left & right).values()),
        "new": [json.loads(row) for row, count in (right - left).items() for _ in range(count)],
        "resolved": [
            json.loads(row) for row, count in (left - right).items() for _ in range(count)
        ],
    }


def normalized(path, line, code, message, root):
    path = Path(path)
    if path.is_absolute():
        path = path.relative_to(root.resolve())
    source = root / path
    lines = source.read_text().splitlines() if source.is_file() else []
    content = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    return {
        "path": str(path),
        "code": code,
        "message": message,
        "source_content": re.sub(r"\s+", " ", content),
    }


def execute(base, python, output, run_tests):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipts = []
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TIKTOKEN_CACHE_DIR": str(ROOT / ".venv/tokenizer-cache"),
    }
    env.pop("PYTHONPATH", None)
    env.pop("MYPYPATH", None)

    def run(label, args, cwd=ROOT, allowed=(0,)):
        start = time.monotonic()
        result = subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300, check=False
        )
        (output / f"{label}.stdout.txt").write_text(result.stdout)
        (output / f"{label}.stderr.txt").write_text(result.stderr)
        receipts.append(
            {
                "label": label,
                "argv": args,
                "cwd": str(cwd),
                "exit_code": result.returncode,
                "elapsed_seconds": time.monotonic() - start,
                "allowed_exit_codes": list(allowed),
                "exit_expected": result.returncode in allowed,
                "stdout_sha256": digest(result.stdout.encode()),
                "stderr_sha256": digest(result.stderr.encode()),
            }
        )
        save(output / "command_receipts.json", receipts)
        return result

    base_sha = run("base_commit", ["git", "rev-parse", base]).stdout.strip()
    head = run("worktree_head", ["git", "rev-parse", "HEAD"]).stdout.strip()
    listed = run("base_tree", ["git", "ls-tree", "-r", "--name-only", base_sha]).stdout.splitlines()
    sources = sorted(path for path in listed if path.endswith(".py"))
    current_paths = set(
        run(
            "current_source_inventory",
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        ).stdout.splitlines()
    )
    extra = sorted(path for path in current_paths if path.endswith(".py") and path not in sources)
    missing = [path for path in sources if not (ROOT / path).is_file()]
    if missing:
        raise RuntimeError(f"Baseline source files missing from current tree: {missing}")
    save(
        output / "scope.json",
        {
            "base_commit": base_sha,
            "observed_head": head,
            "same_baseline_source_paths": sources,
            "new_source_paths": extra,
            "missing_current_paths": missing,
            "source_hashes_current": {p: digest((ROOT / p).read_bytes()) for p in sources + extra},
        },
    )
    run(
        "versions",
        [
            python,
            "-c",
            "import sys,importlib.metadata,json; print(json.dumps({"
            "'python':sys.version,**{x:importlib.metadata.version(x) "
            "for x in ('ruff','mypy','pytest')}}))",
        ],
    )
    run("environment_freeze", [python, "-m", "pip", "freeze"])
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="klima-static-baseline-") as temporary:
        baseline_root = Path(temporary).resolve()
        archive_args = [
            "git",
            "archive",
            "--format=tar",
            base_sha,
            "--",
            *sources,
            "pyproject.toml",
        ]
        archive_result = subprocess.run(
            archive_args,
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        archive = archive_result.stdout
        save(
            output / "archive_receipt.json",
            {
                "argv": archive_args,
                "cwd": str(ROOT),
                "exit_code": archive_result.returncode,
                "archive_sha256": digest(archive),
                "archive_bytes": len(archive),
                "stderr_sha256": digest(archive_result.stderr),
                "retained": False,
                "reason": "Temporary regular source/config extraction only",
            },
        )
        # Extract only regular source/config files named in the frozen path inventory.
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream.getmembers():
                if member.isfile() and member.name in {*sources, "pyproject.toml"}:
                    target = baseline_root / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(stream.extractfile(member).read())
        save(
            output / "baseline_source_hashes.json",
            {p: digest((baseline_root / p).read_bytes()) for p in sources},
        )
        tool_rows = {}
        for state, root in (("baseline", baseline_root), ("current", ROOT)):
            ruff = run(
                f"{state}_ruff",
                [python, "-m", "ruff", "check", "--output-format=json", "--no-cache", *sources],
                cwd=root,
                allowed=(0, 1),
            )
            ruff_rows = json.loads(ruff.stdout)
            tool_rows[f"{state}_ruff"] = [
                normalized(
                    row["filename"], row["location"]["row"], row["code"], row["message"], root
                )
                for row in ruff_rows
            ]
            mypy_sources = [
                p for p in sources if p.startswith(("crawler/", "research/benchmarks/sprint01/"))
            ]
            mypy = run(
                f"{state}_mypy",
                [
                    python,
                    "-m",
                    "mypy",
                    "--namespace-packages",
                    "--explicit-package-bases",
                    "--follow-imports=silent",
                    "--cache-dir=/dev/null",
                    "--show-error-codes",
                    "--no-error-summary",
                    "--no-pretty",
                    *mypy_sources,
                ],
                cwd=root,
                allowed=(0, 1),
            )
            rows = []
            unparsed = []
            for line in mypy.stdout.splitlines():
                matched = re.match(r"^(.+?):(\d+)(?::\d+)?: error: (.*?)(?:  \[([^]]+)\])?$", line)
                if matched:
                    path, location, message, code = matched.groups()
                    rows.append(normalized(path, int(location), code, message, root))
                elif ": error:" in line:
                    unparsed.append(line)
            tool_rows[f"{state}_mypy"] = rows
            save(
                output / f"{state}_mypy_parse.json",
                {"parsed_errors": len(rows), "unparsed_errors": unparsed},
            )
            if unparsed:
                raise RuntimeError("Mypy diagnostics could not be normalized")
        comparisons = {
            name: compare_diagnostics(tool_rows[f"baseline_{name}"], tool_rows[f"current_{name}"])
            for name in ("ruff", "mypy")
        }
        save(output / "diagnostic_comparison.json", comparisons)
        for label, rows in tool_rows.items():
            save(output / f"{label}_normalized.json", rows)
    new_file_counts = {"ruff": 0, "mypy": 0, "source_files": len(extra)}
    if extra:
        new_ruff = run(
            "new_files_ruff",
            [python, "-m", "ruff", "check", "--no-cache", "--output-format=json", *extra],
            allowed=(0, 1),
        )
        new_ruff_rows = json.loads(new_ruff.stdout)
        new_file_counts["ruff"] = len(new_ruff_rows)
        save(
            output / "new_files_ruff_normalized.json",
            [
                normalized(
                    row["filename"], row["location"]["row"], row["code"], row["message"], ROOT
                )
                for row in new_ruff_rows
            ],
        )
        new_mypy = run(
            "new_files_mypy",
            [
                python,
                "-m",
                "mypy",
                "--namespace-packages",
                "--explicit-package-bases",
                "--follow-imports=silent",
                "--cache-dir=/dev/null",
                *extra,
            ],
            allowed=(0, 1),
        )
        new_mypy_rows = []
        for line in new_mypy.stdout.splitlines():
            matched = re.match(r"^(.+?):(\d+)(?::\d+)?: error: (.*?)(?:  \[([^]]+)\])?$", line)
            if matched:
                path, location, message, code = matched.groups()
                new_mypy_rows.append(normalized(path, int(location), code, message, ROOT))
            elif ": error:" in line:
                raise RuntimeError("New-file Mypy diagnostic could not be normalized")
        new_file_counts["mypy"] = len(new_mypy_rows)
        save(output / "new_files_mypy_normalized.json", new_mypy_rows)
    if run_tests:
        run(
            "project_pytest", [python, "-m", "pytest", "-o", "addopts=-p no:cacheprovider", "tests"]
        )
        run(
            "review_gate_unittest",
            [
                python,
                "-m",
                "unittest",
                "research.benchmarks.sprint01.test_review",
                "research.benchmarks.sprint01.test_release_gates",
                "-v",
            ],
        )
        run(
            "e2_contracts",
            [python, "-m", "unittest", "tests_e2", "test_amendment", "-v"],
            cwd=ROOT / "research/experiments/e2_offline_20261024/kit",
        )
        run(
            "evaluator_smoke",
            [
                python,
                "-m",
                "research.benchmarks.sprint01.validate_readiness",
                "--out",
                str(output / "harness_smoke"),
            ],
        )
    finished = {
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": base_sha,
        "current_head_observed": head,
        "baseline_python_paths": len(sources),
        "new_python_paths": len(extra),
        "mypy_target_count": len(mypy_sources),
        "new_file_static_diagnostics_baseline_zero": new_file_counts,
        "same_scope_comparison": comparisons,
        "all_command_exit_codes_expected": all(r["exit_expected"] for r in receipts),
        "historical_1260_ruff_49_mypy_are_not_assumed_current_results": True,
        "diagnostic_identity": "path + code + message + source line "
        "(whitespace-normalized multiset)",
        "scope_note": "Ruff uses explicit baseline-versioned Python paths, "
        "not unrestricted check dot; "
        "Mypy uses identical baseline crawler and sprint01 Python files in both roots. "
        "New files are separate, never attributed to the pre-change baseline.",
        "script_sha256": digest(Path(__file__).read_bytes()),
    }
    save(output / "COMPARISON_REPORT.json", finished)
    files = sorted(p for p in output.rglob("*") if p.is_file())
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest(p.read_bytes())}  {p.relative_to(output)}\n" for p in files)
    )
    return finished


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--python", default=DEFAULT_PYTHON)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()
    result = execute(args.base, args.python, args.out, args.run_tests)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
