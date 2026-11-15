"""Frozen 2x2 offline experiment. Research-only outputs; no pipeline/model calls."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import subprocess
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from research.experiments.m4a2_segmentation_admission import candidates
from research.experiments.m4a2_segmentation_admission.prepare_controls import clean, node_path
from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def source_identity(record, source):
    return (
        record["raw_sha256"] == source["body_sha256"]
        and record["source_url"] == source["url"]
        and record["acquisition_ags"] == source["ags"]
    )


def observe(evidence, segments, chosen):
    all_matches = d.matches(segments, evidence["quote"])
    kept_matches = d.matches(segments, evidence["quote"], chosen)
    if evidence.get("page_ref"):
        kept_matches = [
            h
            for h in kept_matches
            if all(p["page_ref"] == evidence["page_ref"] for p in h["pieces"])
        ]
    return {
        "parsed_matches": all_matches,
        "output_matches": kept_matches,
        "parsed_found": bool(all_matches),
        "output_found": bool(kept_matches),
        "status": "UNRESOLVED_MAPPING"
        if len(kept_matches) > 1
        else "FOUND"
        if kept_matches
        else "ADMISSION_LOSS"
        if all_matches
        else "SEGMENTATION_LOSS",
    }


def validate_controls(controls):
    for control in controls:
        body = (d.EXPORT / "raw" / control["raw_sha256"]).read_bytes()
        d.require(
            hashlib.sha256(body).hexdigest() == control["raw_sha256"], "Control original drift"
        )
        soup = BeautifulSoup(body, "lxml")
        matches = [n for n in soup.find_all(control["tag"]) if node_path(n) == control["dom_path"]]
        d.require(
            len(matches) == 1 and clean(matches[0].get_text(" ", strip=True)) == control["quote"],
            "Control DOM locator mismatch",
        )


def run(out):
    d.require(not out.exists(), "Output must be absent")
    protocol = json.loads((BASE / "protocol.json").read_bytes())
    protected = protocol["protected_sha256"]
    d.require(all(d.sha(d.ROOT / p) == h for p, h in protected.items()), "Protected input drift")
    d.require(d.sha(BASE / "controls.json") == protocol["control_manifest_sha256"], "Control drift")
    freeze = json.loads((BASE / "implementation_freeze.json").read_bytes())
    d.require(
        all(d.sha(BASE / p) == h for p, h in freeze["source_sha256"].items()), "Frozen code drift"
    )
    sys.addaudithook(d.guard)
    offline_checked = False
    try:
        import socket

        socket.getaddrinfo("m4a2.invalid", 80)
    except RuntimeError:
        offline_checked = True
    d.require(offline_checked, "Offline guard failed")
    out.mkdir(parents=True)
    controls = json.loads((BASE / "controls.json").read_bytes())["controls"]
    validate_controls(controls)
    manifest = json.loads(d.RELEASE.read_bytes())
    units = {
        u["unit_id"]: u
        for u in (
            json.loads(line) for line in (d.EXPORT / "corpus.jsonl").read_bytes().splitlines()
        )
    }
    historical = json.loads(
        (d.ROOT / "research/experiments/m4a_parsing_admission/run02/results.json").read_bytes()
    )
    expected_baseline = {
        (r["case_id"], r["evidence_index"]): r["status"]
        for r in historical["observations"]
        if r["condition"] == "P2"
    }
    runtime = {
        "python": platform.python_version(),
        "executable": sys.executable,
        "dependencies": {p: importlib.metadata.version(p) for p in ["beautifulsoup4", "lxml"]},
        "pdftotext": subprocess.run(["pdftotext", "-v"], capture_output=True, text=True).stderr,
        "protocol_sha256": d.sha(BASE / "protocol.json"),
        "controls_sha256": d.sha(BASE / "controls.json"),
        "implementation_freeze_sha256": d.sha(BASE / "implementation_freeze.json"),
        "argv": sys.argv,
        "offline_guard_exercised": True,
    }
    d.write(out / "preflight.json", runtime)
    from crawler.core.models import FetchResult
    from crawler.core.parsers.html_parser import parse_html
    from crawler.core.parsers.pdf_parser import parse_pdf

    experimental = candidates.experimental_parser()
    observations, outputs, decisions, witnesses, timings, gate_events = [], [], [], [], [], []
    for source in protocol["selected_sources"]:
        raw = source["body_sha256"]
        body = (d.EXPORT / "raw" / raw).read_bytes()
        fetch = FetchResult(source["url"], 200, source["content_type"], body, {})
        versions = {}
        for variant in ["current", "candidate"]:
            start = time.monotonic()
            tracemalloc.start()
            before = len(experimental.witnesses)
            parser = (
                parse_pdf
                if source["source_type"] == "pdf"
                else (parse_html if variant == "current" else experimental.parse_html)
            )
            parsed = parser(fetch, source["url"])
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            timings.append(
                {
                    "raw_sha256": raw,
                    "operation": "parse_" + variant,
                    "seconds": time.monotonic() - start,
                    "python_peak_bytes": peak,
                }
            )
            d.require(parsed.text and parsed.segments, "Empty parser result")
            versions[variant] = d.parsed_snapshot(parsed)
            witnesses.extend(
                dict(w, raw_sha256=raw, source_url=source["url"])
                for w in experimental.witnesses[before:]
            )
        for arm, (variant, policy) in protocol["arms"].items():
            segments = versions[variant]
            before_hash = d.digest(segments)
            start = time.monotonic()
            if policy == "standard":
                chosen, detail, sql_sha = d.admission(segments)
            else:
                chosen, detail = candidates.alternative(segments)
                sql_sha = None
            d.require(before_hash == d.digest(segments), "Admission mutated parser output")
            timings.append(
                {
                    "raw_sha256": raw,
                    "operation": arm + "_admission",
                    "seconds": time.monotonic() - start,
                }
            )
            decisions.append(
                {"raw_sha256": raw, "arm": arm, "decisions": detail, "sql_sha256": sql_sha}
            )
            segment_meta = []
            for i, s in enumerate(segments):
                segment_meta.append(
                    {k: v for k, v in s.items() if k not in ["text", "heading_context"]}
                    | {
                        "segment_index": i,
                        "characters": len(s["text"]),
                        "normalized_sha256": hashlib.sha256(clean(s["text"]).encode()).hexdigest(),
                        "heading_context_sha256": d.digest(s["heading_context"]),
                        "admitted": i in chosen,
                    }
                )
            outputs.append(
                {
                    "raw_sha256": raw,
                    "source_url": source["url"],
                    "acquisition_ags": source["ags"],
                    "arm": arm,
                    "variant": variant,
                    "parse_snapshot_sha256": before_hash,
                    "segments": segment_meta,
                    "parsed_segments": len(segments),
                    "admitted_segments": len(chosen),
                    "admitted_characters": sum(len(segments[i]["text"]) for i in chosen),
                }
            )
            entries = []
            for case in manifest["cases"]:
                for index, e in enumerate(case["evidence_spans"]):
                    if e["raw_sha256"] == raw:
                        d.require(
                            d.historical_locator(e, units.get(e["unit_id"])) == "VALID",
                            "Gold locator drift",
                        )
                        entries.append(
                            (
                                "development",
                                f"{case['question_id']}/{index}",
                                dict(e, acquisition_ags=units[e["unit_id"]]["acquisition_ags"]),
                                {"expected": "POSITIVE", "anchor_key": d.digest(e)},
                            )
                        )
            entries.extend(
                (
                    "control",
                    c["control_id"],
                    c,
                    {
                        "expected": c["expected"],
                        "anchor_key": d.digest(
                            {"raw": raw, "path": c["dom_path"], "quote": c["quote"]}
                        ),
                        "category": c["category"],
                        "provisional": True,
                    },
                )
                for c in controls
                if c["raw_sha256"] == raw
            )
            for group, identity, e, metadata in entries:
                d.require(source_identity(e, source), "Source identity regression")
                result = observe(e, segments, chosen)
                row = dict(
                    result,
                    arm=arm,
                    group=group,
                    id=identity,
                    raw_sha256=raw,
                    source_url=source["url"],
                    acquisition_ags=source["ags"],
                    quote_sha256=hashlib.sha256(e["quote"].encode()).hexdigest(),
                    source_identity_valid=True,
                    **metadata,
                )
                if group == "control" and metadata["expected"] == "SCOPE_GUARD":
                    row["query_target_ags"] = e["counterfactual_query_target_ags"]
                    row["original_claim_ags"] = e["claim_ags"]
                    row["scope_conflated"] = row["query_target_ags"] == row["acquisition_ags"]
                    d.require(not row["scope_conflated"], "Scope provenance regression")
                if arm == "B00" and group == "development":
                    d.require(
                        result["status"]
                        == expected_baseline[
                            (identity.rsplit("/", 1)[0], int(identity.rsplit("/", 1)[1]))
                        ],
                        "B00 did not reproduce original M4-A P2",
                    )
                if result["status"] == "UNRESOLVED_MAPPING":
                    gate_events.append(
                        {"arm": arm, "id": identity, "event": "AMBIGUOUS_LOCATOR_NO_ADOPTION"}
                    )
                observations.append(row)
    totals = {}
    for arm in protocol["arms"]:
        part = [o for o in observations if o["arm"] == arm]
        dev = [o for o in part if o["group"] == "development"]
        control = [o for o in part if o["group"] == "control"]
        d.require(len(dev) == 25 and len(control) == len(controls), "Missing observations")
        pos = [o for o in control if o["expected"] == "POSITIVE"]
        neg = [o for o in control if o["expected"] == "NEGATIVE"]
        tp = sum(o["status"] == "FOUND" for o in pos)
        fp = sum(o["output_found"] for o in neg)
        if fp:
            gate_events.append(
                {"arm": arm, "event": "NEGATIVE_CONTROL_ADMISSION_NO_ADOPTION", "count": fp}
            )
        anchor_status = {o["anchor_key"]: o["status"] for o in dev}
        source_part = [s for s in outputs if s["arm"] == arm]
        kept = [x for s in source_part for x in s["segments"] if x["admitted"]]
        exact = Counter(x["text_sha256"] for x in kept)
        norm = Counter(x["normalized_sha256"] for x in kept)
        totals[arm] = {
            "development": {
                "occurrences": 25,
                "status_counts": dict(Counter(o["status"] for o in dev)),
                "unique_anchors": len(anchor_status),
                "unique_found": sum(s == "FOUND" for s in anchor_status.values()),
            },
            "controls": {
                "total": len(control),
                "positive_denominator": len(pos),
                "positive_found": tp,
                "negative_denominator": len(neg),
                "negative_admitted": fp,
                "conditional_control_precision": tp / (tp + fp) if tp + fp else None,
                "precision_scope": (
                    "Only provisional positive/negative controls, not output population"
                ),
                "other_label_statuses": {
                    label: dict(Counter(o["status"] for o in control if o["expected"] == label))
                    for label in ["REDUNDANCY", "UNASSESSED", "SCOPE_GUARD"]
                },
            },
            "outputs": {
                "admitted_segments": len(kept),
                "admitted_characters": sum(s["admitted_characters"] for s in source_part),
                "exact_duplicate_excess": sum(n - 1 for n in exact.values()),
                "normalized_duplicate_excess": sum(n - 1 for n in norm.values()),
            },
            "source_identity_errors": sum(not o["source_identity_valid"] for o in part),
            "per_source": {
                s["raw_sha256"]: {
                    "development": dict(
                        Counter(o["status"] for o in dev if o["raw_sha256"] == s["raw_sha256"])
                    ),
                    "controls": dict(
                        Counter(
                            (o["expected"] + ":" + o["status"])
                            for o in control
                            if o["raw_sha256"] == s["raw_sha256"]
                        )
                    ),
                }
                for s in source_part
            },
        }
    d.require(all(d.sha(d.ROOT / p) == h for p, h in protected.items()), "Protected inputs changed")
    d.write(
        out / "results.json",
        {
            "observations": observations,
            "outputs": outputs,
            "admission_decisions": decisions,
            "fragment_witnesses": witnesses,
            "totals": totals,
            "gate_events": gate_events,
            "protected_inputs_unchanged": True,
            "automatic_adoption": False,
        },
    )
    d.write(
        out / "resources.json",
        {
            "timings": timings,
            "peak_process_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    )
    print(
        json.dumps(
            {
                "status": "COMPLETE_REVIEW_REQUIRED",
                "totals": {
                    a: {"dev": t["development"], "control": t["controls"], "output": t["outputs"]}
                    for a, t in totals.items()
                },
                "results_sha256": d.sha(out / "results.json"),
            }
        )
    )


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    run(ap.parse_args().out.resolve())
