"""Bounded, read-only Pilot-Gold input audit using existing review contracts.

Does not import decisions, change labels, authorize release or run retrieval.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

EXPECTED_CANDIDATES = "4981740d4a2d13a2d4cdbbc7ba239238b87933539a9ff696d96f9dc631790630"
EXPECTED_LOG = "7095553ff8edd1584758724589e8df920dd613f305a48bb8473dfd27c1bc34ef"
EXPECTED_CHAIN = "165a6cbc534e1dba2d65bb69069ee8f7af56edf7e056bd0565a75703efc305ff"
HANDOFF_HASHES = {
    "M3_HUMAN_REVIEW_CONSOLIDATED_2026-11-02.json": (
        "cfc589971db2a9ef754faa7acd95805249bba28db590df84e0c0d46d3e93eb5d"
    ),
    "M3_ABNAHME_SPRINT1_2026-11-02.md": (
        "06b83128084eed380e2090560f393687f864ccb82347ed0362752f5939f73c22"
    ),
    "M3_WORK_CODEX_UEBERGABE_2026-11-02.md": (
        "148607c7afe65f748884a9a2818820b5b4561c3acec8ee60eda8d0a7654defd6"
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(out: Path) -> None:
    from research.benchmarks.sprint01 import human_import, release_gates, review, review_log
    from research.offline_evaluation.contracts import canonical, require

    require(not out.exists(), "Use an absent output directory")
    sys.dont_write_bytecode = True
    candidates = ROOT / "research/benchmarks/sprint01/review_packet/candidates.json"
    log = candidates.parent / "human_reviews.jsonl"
    handoff_root = ROOT / "research/sprints/sprint-01/m3_acceptance/handoff"
    handoff = handoff_root / "M3_HUMAN_REVIEW_CONSOLIDATED_2026-11-02.json"
    protected = [
        candidates,
        log,
        *(handoff_root / name for name in HANDOFF_HASHES),
        review.EXPORT / "corpus.jsonl",
        review.EXPORT / "catalog.jsonl",
    ]
    before = {str(p.relative_to(ROOT)): sha(p) for p in protected}
    require(sha(candidates) == EXPECTED_CANDIDATES, "Frozen candidate file changed")
    require(sha(log) == EXPECTED_LOG, "Frozen human decision log changed")
    for name, expected in HANDOFF_HASHES.items():
        require(sha(handoff_root / name) == expected, "Original handoff changed: " + name)
    out.mkdir(parents=True)

    def guard(event, args):
        if event in ("socket.connect", "socket.connect_ex", "socket.getaddrinfo"):
            raise RuntimeError("Bounded integrity audit forbids network access")
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int)
                and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if (
                writing
                and not isinstance(path, int)
                and not Path(path).resolve().is_relative_to(out)
            ):
                raise RuntimeError("Integrity audit write outside new output: " + str(path))

    sys.addaudithook(guard)
    package, manifest, units = review.validate(candidates)
    rows = package["candidates"]
    require(
        len(rows) == 10 and sum(len(r["evidence"]) for r in rows) == 25,
        "Frozen pilot cardinality changed",
    )
    events = review_log.verify_log(log.read_bytes())
    require(
        len(events) == 10 and events[-1]["event_sha256"] == EXPECTED_CHAIN,
        "Frozen decision chain changed",
    )
    records, prepared_manifest = human_import.prepare(handoff.read_bytes(), candidates, "sven-lutz")
    require(prepared_manifest == manifest, "Handoff validation source manifest differs")
    by_id = {r["candidate_id"]: r for r in records}
    require(len(by_id) == 10, "Handoff coverage not 10 distinct decisions")
    require(
        all(
            canonical(e["decision"]) == canonical(by_id[e["decision"]["candidate_id"]])
            for e in events
        ),
        "Original handoff and complete log decision differ",
    )
    require(
        all(e["candidate_file_sha256"] == EXPECTED_CANDIDATES for e in events),
        "Event binds different candidate file",
    )
    require(
        all(e["decision_file_sha256"] == HANDOFF_HASHES[handoff.name] for e in events),
        "Event binds different human handoff",
    )
    require(
        all(
            e["decision"]["reviewed_at"] is None
            and e["decision"]["reviewed_at_status"] == "NOT_RECORDED_BY_REVIEWER"
            for e in events
        ),
        "Human review reading times inferred/altered",
    )
    require(
        all(
            e["decision"]["decision"] == "ACCEPT"
            and e["decision"]["acceptance_scope"] == "REFERENCE_ANSWER_TEXT_ONLY"
            for e in events
        ),
        "Review scope changed",
    )
    basis = Counter(
        "direct_original"
        if r["source_basis"]["source_directly_opened"]
        else "chat_editorial_source_text"
        for r in records
    )
    require(
        dict(basis) == {"direct_original": 1, "chat_editorial_source_text": 9},
        "Actual human source-access basis changed",
    )
    direct = [r["candidate_id"] for r in records if r["source_basis"]["source_directly_opened"]]
    require(direct == ["SR-01"], "Unexpected direct-original review case")
    partial = {r["id"]: r["answerability"] for r in rows if r["answerability"] == "PARTIAL"}
    require(
        partial == {"SR-02": "PARTIAL", "WB-03": "PARTIAL", "WB-04": "PARTIAL"},
        "Historical PARTIAL labels changed",
    )
    source_groups = release_gates.dependency_groups(rows, False)
    conservative = release_gates.dependency_groups(rows, True)
    require(len(source_groups) == 7 and len(conservative) == 2, "Dependency baseline changed")
    assignments = {row["id"]: "DEVELOPMENT_EXPOSED" for row in rows}
    release_gates.validate_split(rows, assignments)
    original_dependencies = json.loads(
        (
            ROOT / "research/sprints/sprint-01/m3_acceptance/gate_analysis/dependency_groups.json"
        ).read_text()
    )
    require(
        source_groups == original_dependencies["source_version_groups"]
        and conservative == original_dependencies["conservative_municipality_source_groups"],
        "Recomputed dependency membership differs from original analysis",
    )
    source_keys = {
        (s["acquisition_ags"], s["source_url"], s["raw_sha256"])
        for row in rows
        for s in row["source_versions"]
    }
    require(len(source_keys) == 8, "Eight unique source versions required")
    catalog = review.read_rows(review.EXPORT / "catalog.jsonl")
    selected_catalog = [r for r in catalog if (r["ags"], r["url"], r["body_sha256"]) in source_keys]
    spec = importlib.util.spec_from_file_location(
        "pilot_original_provenance",
        ROOT / "research/experiments/e2_v11_acquisition_20261024/protocol/provenance_contract.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load preserved E2 provenance contract")
    pc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pc
    spec.loader.exec_module(pc)
    require(
        pc.load_catalog(selected_catalog, review.EXPORT / "raw") == source_keys,
        "Original acquisition AGS/URL/body contract mismatch",
    )
    m2 = json.loads((ROOT / "research/corpus/sprint-01/freeze01/corpus_manifest.json").read_text())
    expected_m2 = {r["path"]: r["sha256"] for r in m2["files"]}
    source_receipts = []
    for ags, url, body in sorted(source_keys):
        path = review.EXPORT / "raw" / body
        protected.append(path)
        before[str(path.relative_to(ROOT))] = sha(path)
        require(
            sha(path) == expected_m2[str(path.relative_to(ROOT))] == body,
            "M2 original raw identity changed",
        )
        source_receipts.append(
            {
                "acquisition_ags": ags,
                "source_url": url,
                "raw_sha256": body,
                "raw_bytes": path.stat().st_size,
                "m2_manifest_matches": True,
            }
        )
    spans = []
    unit_index = {u["unit_id"]: u for u in units}
    for row in rows:
        for e in row["evidence"]:
            u = unit_index[e["unit_id"]]
            require(
                any(
                    (s["acquisition_ags"], s["source_url"], s["raw_sha256"])
                    == (u["acquisition_ags"], e["source_url"], e["raw_sha256"])
                    for s in row["source_versions"]
                ),
                "Anchor acquisition AGS not bound to admitted source",
            )
            spans.append(
                {
                    "candidate_id": row["id"],
                    "unit_id": e["unit_id"],
                    "start_char": e["start_char"],
                    "end_char": e["end_char"],
                    "page_ref": e["page_ref"],
                    "quote_sha256": hashlib.sha256(e["quote"].encode()).hexdigest(),
                    "canonical_literal_verified": True,
                }
            )
    after = {str(p.relative_to(ROOT)): sha(p) for p in protected}
    require(before == after, "Protected input changed during independent audit")

    def write(name, value):
        (out / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )

    write("candidate_integrity.json", manifest)
    write("source_integrity.json", source_receipts)
    write("span_integrity.json", spans)
    write(
        "dependency_integrity.json",
        {
            "source_version_groups": source_groups,
            "conservative_municipality_source_groups": conservative,
            "assignments": assignments,
            "held_out_test_items": 0,
            "grouping_is_independence_proof": False,
        },
    )
    write(
        "integrity_receipt.json",
        {
            "schema_version": "klima-pilot-input-integrity-receipt/1",
            "status": "PASS_INPUT_INTEGRITY_ONLY_NOT_RELEASE_AUTHORIZATION",
            "candidate_file_sha256": EXPECTED_CANDIDATES,
            "candidate_count": 10,
            "source_versions": 8,
            "canonical_spans": 25,
            "human_log_sha256": EXPECTED_LOG,
            "human_log_events": len(events),
            "chain_tip": EXPECTED_CHAIN,
            "all_handoff_files_hash_verified": HANDOFF_HASHES,
            "complete_handoff_and_logged_records_identical": True,
            "human_review_basis_counts": dict(basis),
            "direct_original_case_ids": direct,
            "human_reading_times_all_null": True,
            "historical_partial_labels_unchanged": partial,
            "source_dependency_groups": len(source_groups),
            "conservative_dependency_groups": len(conservative),
            "raw_sources_match_original_M2_manifest": True,
            "protected_inputs_unchanged": True,
            "protected_input_sha256": after,
            "human_annotations_added": 0,
            "label_mutations": 0,
            "human_identity_and_actual_work_not_machine_certified": True,
            "scope": (
                "Independent machine input integrity; no Research-Gold acceptance, "
                "no answerability adjudication, no policies or M4/M5."
            ),
        },
    )
    modules = [
        Path(__file__),
        Path(review.__file__),
        Path(review_log.__file__),
        Path(human_import.__file__),
        Path(release_gates.__file__),
    ]
    write(
        "run_manifest.json",
        {
            "command": (
                "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python "
                "research/sprints/sprint-01/pilot_gold_validation/integrity/check_inputs.py --out "
            )
            + str(out.relative_to(ROOT)),
            "python": platform.python_version(),
            "network": "denied by Python audit hook",
            "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in modules},
            "output_sha256": {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()},
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "candidate_count": 10,
                "sources": 8,
                "spans": 25,
                "log_events": 10,
                "source_dependency_groups": len(source_groups),
                "conservative_groups": len(conservative),
            }
        )
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    main(ap.parse_args().out.resolve())
