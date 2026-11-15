"""Re-run existing evaluator contracts; no real gold judgments or method comparisons."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "research/offline_evaluation"
REFERENCE = ROOT / "research/experiments/e2_v11_reference_20261025"


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def validate(out):
    """Publish small intentionally versioned test receipts, without copying municipal text."""
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError("Use a fresh validation output directory")
    out.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    commands = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    def run(args, label):
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=120, check=False)
        (out / f"{label}.stdout.txt").write_text(result.stdout)
        (out / f"{label}.stderr.txt").write_text(result.stderr)
        commands.append({"argv": args, "cwd": str(ROOT), "exit_code": result.returncode,
                         "stdout": f"{label}.stdout.txt", "stderr": f"{label}.stderr.txt"})
        if result.returncode:
            write_json(out / "FAILED.json", {"commands": commands, "failed_stage": label})
            raise RuntimeError(f"{label} failed; see recorded stderr")
        return result

    run([sys.executable, "-m", "unittest", "research.offline_evaluation.test_adapter", "-v"],
        "unittest")
    # Existing synthetic generator is the executable schema fixture. Its invented labels
    # remain confined to TECHNICAL_TEST and are never exported as municipal references.
    from research.offline_evaluation.synthetic import SyntheticPackage

    smoke = {}
    with tempfile.TemporaryDirectory(prefix="klima-sprint01-contract-") as temporary:
        temp = Path(temporary)
        for label, empty in (("complete_synthetic", False), ("empty_reference_synthetic", True)):
            fixture = SyntheticPackage()
            if empty:
                fixture.documents["references"] = {}
            manifest, expected = fixture.freeze(temp / label / "input")
            output = temp / label / "output"
            run([sys.executable, "-m", "research.offline_evaluation", "--manifest", str(manifest),
                 "--manifest-sha256", expected, "--out", str(output)], label)
            report = json.loads((output / "report.json").read_text())
            rows = [json.loads(line) for line in (output / "metrics.jsonl").read_text().splitlines()]
            expected_status = "DIAGNOSTIC" if empty else "SCORABLE_COMPLETE"
            if report["status"] != expected_status or report["purpose"] != "TECHNICAL_TEST":
                raise RuntimeError(f"Unexpected smoke classification: {label}")
            if empty and any(r["value"] is not None or not r["NA_reason"] for r in rows
                             if r["metric_id"] in {f"M{i:02}" for i in range(3, 15)}):
                raise RuntimeError("Empty reference leaked a scientific quality score")
            receipt = out / label
            receipt.mkdir()
            # Keep actual outputs and hashes, not the invented source/annotation fixture.
            for path in output.iterdir():
                shutil.copyfile(path, receipt / path.name)
            smoke[label] = {"purpose": "TECHNICAL_TEST", "status": report["status"],
                            "manifest_sha256": expected, "metric_rows": len(rows),
                            "input_sha256": {p.name: checksum(p) for p in sorted(manifest.parent.iterdir())},
                            "output_sha256": {p.name: checksum(p) for p in sorted(output.iterdir())},
                            "scientific_results": False, "seed": "NOT_APPLICABLE_DETERMINISTIC"}

        # Reuse the existing ranking-free source census, not a new annotation framework.
        packet = temp / "source_census"
        run([sys.executable, str(REFERENCE / "prepare_reference.py"), "--out", str(packet)],
            "source_census")
        matrix = list(csv.DictReader((packet / "document_task_matrix.csv").open()))
        if any(r["review_status"] != "NOT_ASSESSED" or r["reviewer_id"] or r["evidence_ids"]
               for r in matrix):
            raise RuntimeError("Source census unexpectedly manufactured annotations")
        hashes = {p.name: checksum(p) for p in sorted(packet.iterdir())}
        comparison = {name: digest == checksum(REFERENCE / "reference01" / name)
                      for name, digest in hashes.items()}
        if not all(comparison.values()):
            raise RuntimeError("Regenerated source census differs from frozen reference01")
        census = {"status": "UNANNOTATED_SOURCE_CENSUS_NOT_GOLD", "matrix_rows": len(matrix),
                  "adjudicated_labels": 0, "ranking_inputs_read": False,
                  "output_sha256": hashes, "byte_equal_to_reference01": comparison}
        write_json(out / "source_census_verification.json", census)

    sources = sorted(ADAPTER.glob("*.py")) + [REFERENCE / "prepare_reference.py",
              REFERENCE / "REFERENCE_PROTOCOL.md", Path(__file__).resolve(),
              ROOT / "research/experiments/architecture_evaluation_v02_20261025/METRIC_CONTRACT.csv"]
    summary = {"status": "TECHNICAL_VALIDATION_PASSED_NOT_M3_ACCEPTANCE",
               "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "platform": platform.platform(),
               "provider": "NONE_OFFLINE", "model": "NONE", "prompt_version": "NOT_APPLICABLE",
               "commands": commands, "smoke": smoke, "source_census": census,
               "source_sha256": {str(p.relative_to(ROOT)): checksum(p) for p in sources},
               "m3_scientific_acceptance": "BLOCKED_INDEPENDENT_ANNOTATION_REQUIRED",
               "raw_municipal_text_copied_to_this_report": False}
    write_json(out / "validation_manifest.json", summary)
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "SHA256SUMS").write_text("".join(f"{checksum(p)}  {p.relative_to(out)}\n" for p in files))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = validate(args.out)
    print(json.dumps({"status": result["status"], "out": str(args.out)}))


if __name__ == "__main__":
    main()
