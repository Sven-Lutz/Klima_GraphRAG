"""CLI: validate frozen local files and publish only to a fresh output directory."""
from __future__ import annotations

import argparse
import csv
import io
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .contracts import Inputs, ValidationError, digest, require
from .evaluate import Evaluation


def execute(manifest: Path, expected_sha256: str, output: Path):
    output = Path(output).absolute()
    require(not output.is_symlink(), "Output path already exists as a symlink")
    output = output.resolve()
    require(not output.exists(), "Output path already exists; never overwrite results")
    inputs = Inputs(manifest, expected_sha256)
    report = Evaluation(inputs).compute()
    inputs.unchanged()
    code_hashes = {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))}
    report["reproduction"] = {
        "adapter_version": __version__, "python": platform.python_version(),
        "manifest_sha256": expected_sha256,
        "asset_sha256": {k: digest(v) for k, v in inputs.assets.items()},
        "code_sha256": code_hashes,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "integrity_is_not_a_trusted_timestamp": True,
        "command": [sys.executable, "-m", "research.offline_evaluation", "--manifest",
                    str(inputs.path), "--manifest-sha256", expected_sha256, "--out", "FRESH_OUTPUT_PATH"],
    }
    rows = report.pop("rows")
    resources = report.pop("resources")
    selections = report.pop("resolved_selections")
    citations = report.pop("citation_links")
    payloads = {
        "report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "metrics.jsonl": "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows),
        "resources.jsonl": "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in resources),
        "trace.json": json.dumps({"selections": selections, "citations": citations},
                                 ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    }
    fieldnames = list(dict.fromkeys(k for r in rows for k in r))
    table = io.StringIO(newline="")
    writer = csv.DictWriter(table, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                         if isinstance(v, (dict, list)) else v for k, v in row.items()})
    payloads["metrics.csv"] = table.getvalue()
    inputs.unchanged()
    # Atomic mkdir claims the namespace. No existing file, directory or symlink is reused.
    output.mkdir(parents=True, exist_ok=False)
    try:
        for name, text in payloads.items():
            with (output / name).open("x", encoding="utf-8", newline="") as stream:
                stream.write(text)
        with (output / "SHA256SUMS").open("x") as stream:
            stream.write("".join(digest((output / name).read_bytes()) + "  " + name + "\n"
                                 for name in sorted(payloads)))
        inputs.unchanged()
        with (output / "COMPLETE").open("x") as stream:
            stream.write("TECHNICAL_TEST\n" if inputs.manifest["purpose"] == "TECHNICAL_TEST"
                         else "OFFLINE_CONTRACT_VALIDATED_NOT_SCIENTIFIC_TRUTH\n")
    except BaseException:
        # Leave partial artifacts observable, never label a partial write complete.
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = execute(args.manifest, args.manifest_sha256, args.out)
    except (ValidationError, OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "VALIDATION_ERROR", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "purpose": report["purpose"], "output": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
