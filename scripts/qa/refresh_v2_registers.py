"""Refresh current V2 bindings while preserving explicitly frozen V1 rows."""

import argparse
import csv
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "docs/paper"


def rewrite(path, rows, fields, delimiter):
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=delimiter, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args()
    code = subprocess.check_output(["git", "rev-parse", args.code_commit], text=True).strip()
    paths = [
        "crawler/web/conversation.py",
        "crawler/web/conversation_state.py",
        "crawler/web/conversation_planning.py",
        "crawler/web/conversation_generation.py",
        "crawler/web/conversation_provider.py",
        "crawler/web/conversation_workspaces.py",
        "crawler/web/conversation_demo.py",
        "crawler/web/conversation_review.py",
        "crawler/web/review_protocol.py",
        "tests/test_conversation_v2.py",
        "scripts/qa/conversation_v2_browser.py",
        "scripts/qa/inventory_conversation_corpus.py",
        "scripts/qa/conversation_layout_browser.py",
        "scripts/qa/review_queue_load.py",
        "docs/product/CONVERSATIONAL_V2.md",
        "docs/product/V2_PROTOCOLS.md",
        "docs/product/V2_COVERAGE_REPORT.md",
        "docs/product/V2_QA_REPORT.md",
        "docs/product/V2_CHECKS.json",
        "docs/product/V2_QUEUE_LOAD.json",
    ]
    manifest = PAPER / "ARTIFACT_MANIFEST.tsv"
    with manifest.open() as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields, rows = list(reader.fieldnames), list(reader)
    # Frozen V1 records are never refreshed against current files.
    rows = [r for r in rows if r.get("source_commit") not in {"", "CURRENT_TREE"}]
    for path in paths:
        raw = (ROOT / path).read_bytes()
        rows.append(
            dict(
                path=path,
                sha256=hashlib.sha256(raw).hexdigest(),
                source_bytes=len(raw),
                access_rights="PUBLIC_OWN_CODE_DOCS_SYNTHETIC_CONTRACTS",
                code_commit=code,
                corpus_binding="V2_SYNTHETIC_OR_METADATA_INVENTORY_ONLY",
                prompt_model="conversational-claims-v2.1_OPTIONAL_NO_LIVE_MODEL_QUALIFIED",
                tokenizer="NO_MODEL_TOKENIZER_EXECUTED",
                source_commit="CURRENT_TREE",
                restore="checkout release commit; current bytes bound by sha256",
            )
        )
    rewrite(manifest, rows, fields, "\t")
    claims = PAPER / "CLAIM_EVIDENCE_REGISTER.csv"
    with claims.open() as stream:
        reader = csv.DictReader(stream)
        fields, rows = list(reader.fieldnames), list(reader)
    rows = [r for r in rows if r.get("source_commit") not in {"", "CURRENT_TREE"}]
    for cid, sentence, path, span in [
        (
            "V2-P01",
            "V2 records versioned context decisions and exposes unresolved place scope.",
            "crawler/web/conversation_state.py",
            "resolve",
        ),
        (
            "V2-P02",
            "Optional generation checks spans, numbers and qualifiers, not semantic entailment.",
            "crawler/web/conversation_generation.py",
            "synthesize",
        ),
        (
            "V2-P03",
            "Charts derive observations from evidence quotes rather than model prose.",
            "crawler/web/conversation_workspaces.py",
            "visualization",
        ),
    ]:
        rows.append(
            dict(
                claim_id=cid,
                sentence=sentence,
                source=path,
                sha256=hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
                original_span=span,
                study="Conversational-v2-technical",
                exposure="SYNTHETIC_DEVELOPMENT_EXPOSED",
                confidence_basis="Code and mechanical regression only; receipt separately reported",
                status="VERIFIED",
                rights="OWN_CODE",
                independent_human_validation="false",
                source_commit="CURRENT_TREE",
            )
        )
    rewrite(claims, rows, fields, ",")
    print("Refreshed V2 bindings; preserved frozen V1 records")


if __name__ == "__main__":
    main()
