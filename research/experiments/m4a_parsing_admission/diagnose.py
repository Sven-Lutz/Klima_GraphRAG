"""Bounded offline diagnosis; never chunks, retrieves, calls models or changes gold."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent
EXPORT = ROOT / "research/experiments/e2_v11_acquisition_20261024/export"
RELEASE = ROOT / "research/benchmarks/sprint01/releases/pilot-gold-v1/manifest.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def guard(event, args):
    if event in {
        "socket.connect",
        "socket.connect_ex",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.bind",
        "urllib.Request",
    }:
        raise RuntimeError("M4-A offline: " + event)
    if event == "subprocess.Popen":
        require(Path(str(args[0])).name == "pdftotext", "Only pdftotext subprocess allowed")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def filter_components():
    """Reuse literal production AST and SQL; do not execute chunking/engine startup."""
    engine = ast.parse((ROOT / "crawler/core/engine.py").read_text())
    wanted = {
        "_HIGH_IMPACT_WORDS",
        "HIGH_IMPACT_REGEX",
        "_NEGATIVE_WORDS",
        "NEGATIVE_REGEX",
        "_segment_features",
    }
    selected = [
        node
        for node in engine.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets)
        )
    ]
    require(len(selected) == 5, "Unexpected production scorer AST")
    scope = {"re": re, "Tuple": Tuple}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), "<unchanged-engine-AST>", "exec"), scope
    )
    tree = ast.parse((ROOT / "crawler/scripts/chunk_documents.py").read_text())
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_segments"
    )
    query = next(
        ast.literal_eval(n.value)
        for n in fn.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "query" for t in n.targets)
    )
    defaults = dict(zip([a.arg for a in fn.args.args][-len(fn.args.defaults) :], fn.args.defaults))
    require(
        [ast.literal_eval(defaults[n]) for n in ["min_len", "min_score", "limit"]]
        == [80, 10, 100000],
        "Standard defaults changed",
    )
    return scope["_segment_features"], query


def admission(segments):
    scorer, query = filter_components()
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("""CREATE TABLE documents_raw(document_id TEXT,municipality_id TEXT);
        CREATE TABLE segments(segment_id TEXT,document_id TEXT,text TEXT,heading_context TEXT,
        page_ref TEXT,impact_score INTEGER,is_negative INTEGER);
        CREATE TABLE chunks(segment_id TEXT);
        INSERT INTO documents_raw VALUES ('source','fixed-source');""")
        decisions = []
        for i, seg in enumerate(segments):
            score, hits, negative = scorer(seg["text"])
            conn.execute(
                "INSERT INTO segments VALUES (?,?,?,?,?,?,?)",
                (
                    str(i),
                    "source",
                    seg["text"],
                    seg["heading_context"],
                    seg["page_ref"],
                    score,
                    negative,
                ),
            )
            decisions.append(
                {
                    "segment_index": i,
                    "length": len(seg["text"]),
                    "score": score,
                    "hits": hits,
                    "negative": bool(negative),
                    "reasons": (["LENGTH_LT_80"] if len(seg["text"]) < 80 else [])
                    + (["NEGATIVE"] if negative else [])
                    + (["SCORE_LT_10"] if score < 10 else []),
                }
            )
        chosen = {int(row[0]) for row in conn.execute(query, (False, 80, 10, 100000))}
        for d in decisions:
            d["admitted"] = d["segment_index"] in chosen
            require(
                d["admitted"] == (not d["reasons"]), "SQL/reason disagreement or limit truncation"
            )
        return chosen, decisions, hashlib.sha256(query.encode()).hexdigest()


def normalized(text):
    chars, mapping = [], []
    for m in re.finditer(r"\s+|\S", text):
        chars.append(" " if m.group().isspace() else m.group())
        mapping.append((m.start(), m.end()))
    return "".join(chars), mapping


def occurrences(haystack, needle):
    start = 0
    while needle and (position := haystack.find(needle, start)) >= 0:
        yield position, position + len(needle)
        start = position + 1


def matches(segments, quote, chosen=None):
    """Return every reversible locator, including contiguous cross-segment mappings."""
    groups, group = [], []
    for i, seg in enumerate(segments):
        if chosen is None or i in chosen:
            group.append((i, seg))
        elif group:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    result = []
    for group in groups:
        parts, ranges, cursor = [], [], 0
        for i, seg in group:
            parts.append(seg["text"])
            ranges.append((cursor, cursor + len(seg["text"]), i))
            cursor += len(seg["text"]) + 1
        joined = "\n".join(parts)
        norm, mapping = normalized(joined)
        nq, _ = normalized(quote)
        found = {(a, b): "EXACT" for a, b in occurrences(joined, quote)}
        for a, b in occurrences(norm, nq):
            found.setdefault((mapping[a][0], mapping[b - 1][1]), "WHITESPACE_ONLY")
        for (a, b), kind in sorted(found.items()):
            pieces = []
            for left, right, index in ranges:
                if max(a, left) < min(b, right):
                    seg = segments[index]
                    pieces.append(
                        {
                            "segment_index": index,
                            "parser_order_index": seg["order_index"],
                            "start_char": max(a, left) - left,
                            "end_char": min(b, right) - left,
                            "page_ref": seg["page_ref"],
                            "text_sha256": seg["text_sha256"],
                        }
                    )
            rebuilt = "\n".join(
                segments[p["segment_index"]]["text"][p["start_char"] : p["end_char"]]
                for p in pieces
            )
            require(normalized(rebuilt)[0] == nq, "Non-reversible locator")
            result.append({"match": kind, "pieces": pieces})
    return result


def historical_locator(evidence, unit):
    if unit is None:
        return "INVALID_HISTORICAL_LOCATOR"
    if unit["source_url"] != evidence["source_url"] or unit["raw_hashes"] != [
        evidence["raw_sha256"]
    ]:
        return "WRONG_SOURCE"
    a, b = evidence["start_char"], evidence["end_char"]
    if (
        not 0 <= a < b <= len(unit["text"])
        or unit["text"][a:b] != evidence["quote"]
        or unit["page_ref"] != evidence["page_ref"]
    ):
        return "INVALID_HISTORICAL_LOCATOR"
    return "VALID"


def classify(locator, full, segment, admitted, condition):
    if locator != "VALID":
        return locator
    current = admitted if condition == "P2" else segment
    if len(current) > 1:
        return "UNRESOLVED_MAPPING"
    if current:
        return "FOUND"
    if condition == "P2" and segment:
        return "ADMISSION_LOSS"
    if full:
        return "SEGMENTATION_LOSS"
    return "PARSER_TEXT_LOSS"


def parsed_snapshot(parsed):
    return [
        {
            "order_index": s.order_index,
            "text": s.text,
            "segment_type": s.segment_type,
            "page_ref": s.page_ref,
            "heading_context": s.heading_context,
            "text_sha256": hashlib.sha256(s.text.encode()).hexdigest(),
        }
        for s in parsed.segments
    ]


def run(out):
    require(not out.exists(), "Output must be absent")
    protocol = json.loads((BASE / "protocol.json").read_bytes())
    protected = protocol["protected_sha256"]
    require(all(sha(ROOT / p) == h for p, h in protected.items()), "Protected input drift")
    out.mkdir(parents=True)
    sys.addaudithook(guard)
    # Exercise actual offline restriction before parsing.
    blocked = False
    try:
        socket.getaddrinfo("m4a.invalid", 80)
    except RuntimeError:
        blocked = True
    require(blocked, "Offline guard failed")
    runtime = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "dependencies": {p: importlib.metadata.version(p) for p in ["beautifulsoup4", "lxml"]},
        "pdftotext": subprocess.run(
            ["pdftotext", "-v"], capture_output=True, text=True
        ).stderr.strip(),
        "driver_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(BASE / "protocol.json"),
        "network_guard_checked": blocked,
        "argv": sys.argv,
        "executable": sys.executable,
    }
    write(out / "preflight.json", runtime)  # persisted before first parser invocation
    sys.path.insert(0, str(ROOT))
    from crawler.core.models import FetchResult

    old = ROOT / "research/experiments/e2_v11_acquisition_20261024/implementation_snapshot"
    require(
        sha(old / "crawler/core/models.py") == sha(ROOT / "crawler/core/models.py"), "Model drift"
    )
    parsers = {}
    for label, prefix in [("P0", old), ("P1", ROOT)]:
        parsers[label] = {
            kind: load(prefix / f"crawler/core/parsers/{kind}_parser.py", f"_m4a_{label}_{kind}")
            for kind in ["html", "pdf"]
        }
    manifest = json.loads(RELEASE.read_bytes())
    catalog = [json.loads(line) for line in (EXPORT / "catalog.jsonl").read_bytes().splitlines()]
    units = {
        row["unit_id"]: row
        for row in (
            json.loads(line) for line in (EXPORT / "corpus.jsonl").read_bytes().splitlines()
        )
    }
    rows, source_rows, filters, errors = [], [], [], []
    for source in manifest["sources"]:
        raw = source["raw_sha256"]
        cat = [c for c in catalog if c["body_sha256"] == raw and c["url"] == source["source_url"]]
        require(
            len(cat) == 1 and cat[0]["ags"] == source["acquisition_ags"], "Source identity mismatch"
        )
        cat = cat[0]
        body = (EXPORT / "raw" / raw).read_bytes()
        require(hashlib.sha256(body).hexdigest() == raw, "Original mismatch")
        fetch = FetchResult(source["source_url"], 200, cat["content_type"], body, {})
        parsed, snapshots, timings, failures = {}, {}, {}, {}
        for label in ["P0", "P1"]:
            start = time.monotonic()
            try:
                kind = cat["source_type"]
                parsed[label] = getattr(parsers[label][kind], "parse_" + kind)(
                    fetch, source["source_url"]
                )
                snapshots[label] = parsed_snapshot(parsed[label])
                require(bool(parsed[label].text) and bool(snapshots[label]), "Empty parser result")
                if label == "P0":
                    original = [
                        u
                        for u in units.values()
                        if u["raw_hashes"] == [raw] and u["source_url"] == source["source_url"]
                    ]
                    replay = [s for s in snapshots[label] if s["text"].strip()]
                    require(len(original) == len(replay), "Historical replay count mismatch")
                    for i, s in enumerate(replay):
                        u = units[f"{cat['ags']}-{raw}-{i}"]
                        require(
                            (
                                u["text"],
                                u["heading_context"],
                                u["page_ref"],
                                u["origins"][0]["segment_order"],
                                u["origins"][0]["segment_type"],
                            )
                            == (
                                s["text"],
                                s["heading_context"],
                                s["page_ref"],
                                s["order_index"],
                                s["segment_type"],
                            ),
                            "Historical replay metadata/text mismatch",
                        )
            except Exception as exc:
                failures[label] = type(exc).__name__ + ": " + str(exc)
                errors.append({"raw_sha256": raw, "condition": label, "error": failures[label]})
            timings[label] = time.monotonic() - start
        chosen, decisions, sql_sha = set(), [], None
        if "P1" not in failures:
            try:
                prefilter_digest = digest(snapshots["P1"])
                chosen, decisions, sql_sha = admission(snapshots["P1"])
                require(prefilter_digest == digest(snapshots["P1"]), "Admission mutated P1")
            except Exception as exc:
                failures["P2"] = type(exc).__name__ + ": " + str(exc)
                errors.append({"raw_sha256": raw, "condition": "P2", "error": failures["P2"]})
        else:
            failures["P2"] = "P1 prerequisite failed"
        filters.append({"raw_sha256": raw, "sql_sha256": sql_sha, "segments": decisions})
        for condition in ["P0", "P1", "P2"]:
            parser_label = "P0" if condition == "P0" else "P1"
            source_rows.append(
                {
                    "raw_sha256": raw,
                    "source_url": source["source_url"],
                    "condition": condition,
                    "parser_segments": len(snapshots.get(parser_label, [])),
                    "admitted_segments": len(chosen) if condition == "P2" else None,
                    "parse_snapshot_sha256": digest(snapshots.get(parser_label, [])),
                    "parse_text_sha256": hashlib.sha256(
                        parsed[parser_label].text.encode()
                    ).hexdigest()
                    if parser_label in parsed
                    else None,
                    "parse_seconds": timings.get(parser_label),
                    "error": failures.get(condition),
                    "full_parse_snapshot_reused_for_P2": condition == "P2",
                }
            )
        for case in manifest["cases"]:
            for index, evidence in enumerate(case["evidence_spans"]):
                if evidence["raw_sha256"] != raw:
                    continue
                locator = historical_locator(evidence, units.get(evidence["unit_id"]))
                require(evidence["source_url"] == source["source_url"], "Release source mismatch")
                for condition in ["P0", "P1", "P2"]:
                    label = "P0" if condition == "P0" else "P1"
                    row = {
                        "case_id": case["question_id"],
                        "evidence_index": index,
                        "raw_sha256": raw,
                        "source_url": source["source_url"],
                        "acquisition_ags": source["acquisition_ags"],
                        "condition": condition,
                        "quote_sha256": hashlib.sha256(evidence["quote"].encode()).hexdigest(),
                        "shared_evidence_key": digest(evidence),
                        "historical_locator_status": locator,
                        "historical_locator": {
                            k: evidence[k]
                            for k in ["unit_id", "start_char", "end_char", "page_ref"]
                        },
                    }
                    if condition in failures:
                        row.update(status="NON_REPRODUCIBLE", error=failures[condition])
                    else:
                        segs = snapshots[label]
                        full = matches(
                            [
                                {
                                    "text": parsed[label].text,
                                    "order_index": 0,
                                    "page_ref": None,
                                    "text_sha256": hashlib.sha256(
                                        parsed[label].text.encode()
                                    ).hexdigest(),
                                }
                            ],
                            evidence["quote"],
                        )
                        seg = matches(segs, evidence["quote"])
                        kept = (
                            matches(segs, evidence["quote"], chosen) if condition == "P2" else seg
                        )

                        # Preserve the historical PDF page as a locator constraint.
                        def valid(hits):
                            return [
                                hit
                                for hit in hits
                                if evidence["page_ref"] is None
                                or all(p["page_ref"] == evidence["page_ref"] for p in hit["pieces"])
                            ]

                        valid_seg, valid_kept = valid(seg), valid(kept)
                        status = classify(locator, full, valid_seg, valid_kept, condition)
                        if (kept and not valid_kept) and locator == "VALID":
                            status = "UNRESOLVED_MAPPING"
                        row.update(
                            status=status,
                            parser_text_matches=full,
                            parser_segment_matches=seg,
                            output_matches=kept,
                            page_valid_output_matches=valid_kept,
                            text_found=bool(full),
                            segments_found=bool(valid_seg),
                            output_found=bool(valid_kept),
                            location_ambiguous=len(valid_kept) > 1,
                            rejected_evidence_segments=[
                                decisions[i]
                                for i in sorted(
                                    {p["segment_index"] for hit in valid_seg for p in hit["pieces"]}
                                )
                                if i not in chosen
                            ]
                            if condition == "P2"
                            else [],
                        )
                    rows.append(row)
    require(len(rows) == 75, "Expected 25 occurrences in each of three conditions")
    aggregate = {}
    for condition in ["P0", "P1", "P2"]:
        part = [r for r in rows if r["condition"] == condition]
        aggregate[condition] = {
            "denominator": len(part),
            "status_counts": dict(Counter(r["status"] for r in part)),
            "per_source": {
                s["raw_sha256"]: dict(
                    Counter(r["status"] for r in part if r["raw_sha256"] == s["raw_sha256"])
                )
                for s in manifest["sources"]
            },
        }
    require(all(sha(ROOT / p) == h for p, h in protected.items()), "Protected inputs changed")
    deterministic = {
        "observations": rows,
        "aggregates": aggregate,
        "filter_decisions": filters,
        "source_outputs": [
            {k: v for k, v in s.items() if k != "parse_seconds"} for s in source_rows
        ],
        "errors": errors,
        "protected_inputs_unchanged": True,
    }
    write(out / "results.json", deterministic)
    write(out / "timings.json", source_rows)
    print(
        json.dumps(
            {
                "status": "COMPLETE" if not errors else "COMPLETE_WITH_ERRORS",
                "aggregates": {k: v["status_counts"] for k, v in aggregate.items()},
                "results_sha256": sha(out / "results.json"),
            }
        )
    )


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    run(ap.parse_args().out.resolve())
