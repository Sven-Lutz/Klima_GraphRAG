"""Read-only replay/locator observation of frozen B01; no policy/candidate calls."""

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

from research.experiments.m4a2_segmentation_admission.prepare_controls import clean, node_path
from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent
A2 = d.ROOT / "research/experiments/m4a2_segmentation_admission"


def observed_html_parser():
    """Add origin-only observer arguments to a private AST; output must hash-identical."""
    tree = ast.parse((d.ROOT / "crawler/core/parsers/html_parser.py").read_text())
    counts = {"emit_definition": 0, "emit_calls": 0}

    class Observer(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name == "emit":
                node.args.args.append(ast.arg(arg="_origin"))
                node.body.insert(0, ast.parse("_audit_capture(_origin, text, kind)").body[0])
                counts["emit_definition"] += 1
            return self.generic_visit(node)

        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == "emit":
                node.args.append(ast.Name(id="node", ctx=ast.Load()))
                counts["emit_calls"] += 1
            return node

    tree = Observer().visit(tree)
    ast.fix_missing_locations(tree)
    d.require(counts == {"emit_definition": 1, "emit_calls": 3}, "Unexpected observer structure")
    origins, originals = [], {}

    def soup_factory(*args, **kwargs):
        soup = BeautifulSoup(*args, **kwargs)
        for node in soup.find_all(True):
            ancestors = [
                {"tag": a.name, "id": a.get("id"), "class": a.get("class"), "role": a.get("role")}
                for a in [node, *list(node.parents)[:5]]
                if a.name != "[document]"
            ]
            heading = node.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
            originals[id(node)] = {
                "dom_path": node_path(node),
                "tag": node.name,
                "original_node_text": clean(node.get_text(" ", strip=True)),
                "previous_heading": clean(heading.get_text(" ", strip=True)) if heading else None,
                "ancestors": ancestors,
            }
        return soup

    def capture(node, text, kind):
        d.require(id(node) in originals, "Missing original DOM object")
        info = originals[id(node)]
        ranges = list(d.occurrences(info["original_node_text"], clean(text)))
        context = info["original_node_text"]
        if ranges:
            a, b = ranges[0]
            context = context[max(0, a - 180) : min(len(context), b + 180)]
        origins.append(
            {k: v for k, v in info.items() if k != "original_node_text"}
            | {
                "normalized_ranges_in_original_node": ranges,
                "original_node_text_sha256": hashlib.sha256(
                    info["original_node_text"].encode()
                ).hexdigest(),
                "original_context_excerpt": context[:1200],
                "origin_mapping_status": "EXACT_NODE_TEXT_RANGE"
                if ranges
                else "NODE_ONLY_TEXT_LAYOUT_TRANSFORM",
                "segment_type": kind,
            }
        )

    ns = {"__name__": "_m4a3_origin_observer", "_audit_capture": capture}
    exec(compile(tree, "<unchanged-parser-plus-origin-observer>", "exec"), ns)
    ns["BeautifulSoup"] = soup_factory
    return ns["parse_html"], origins


def stable_id(raw, index):
    return raw + ":" + str(index)


def length_class(n):
    return "LT30" if n < 30 else "30_79" if n < 80 else "80_249" if n < 250 else "GE250"


def select_sample(items, limit=48):
    ordered = sorted(items, key=lambda x: d.digest({"salt": "M4A3-v1", "id": x["id"]}))
    selected = []
    seen = set()

    def add(row):
        if row["id"] not in seen and len(selected) < limit:
            selected.append(row)
            seen.add(row["id"])

    # Source coverage is mandatory; then greedy metadata-coverage, stable hash tie-breaking.
    for raw in sorted({r["raw_sha256"] for r in ordered}):
        add(next(r for r in ordered if r["raw_sha256"] == raw))

    def features(row):
        return {
            ("media", row["source_type"]),
            ("length", length_class(row["characters"])),
            ("reason", row["admission_reason"]),
            ("structure", row["origin"].get("tag", "pdf")),
            ("heading", bool(row["heading_context"])),
            ("duplicate", row["duplicate_group"] or "SINGLETON"),
        }

    covered = set().union(*(features(r) for r in selected))
    while len(selected) < min(limit, len(ordered)):
        remaining = [r for r in ordered if r["id"] not in seen]
        row = max(remaining, key=lambda r: len(features(r) - covered))
        add(row)
        covered |= features(row)
    return {
        "schema_version": "klima-m4a3-sample/1",
        "status": "FROZEN_BEFORE_QUALITATIVE_REVIEW",
        "frame_count": len(items),
        "maximum": limit,
        "selected_count": len(selected),
        "selection": "Stable SHA(M4A3-v1,id); one per source, t"
        "hen greedy new metadata feature coverage; "
        "ties preserve stable SHA order. No text/relevance features.",
        "selected_ids": [r["id"] for r in selected],
        "covered_features": sorted(covered, key=str),
        "stratum_counts": {
            "source": dict(Counter(r["raw_sha256"] for r in selected)),
            "length": dict(Counter(length_class(r["characters"]) for r in selected)),
            "reason": dict(Counter(r["admission_reason"] for r in selected)),
            "media": dict(Counter(r["source_type"] for r in selected)),
        },
        "unsupported_strata": "No added PDF units in frozen B01 diffe"
        "rence; removed PDF examined separately.",
    }


def main(out):
    d.require(not out.exists(), "Output must be absent")
    sys.addaudithook(d.guard)
    protocol = json.loads((BASE / "audit_protocol.json").read_bytes())
    d.require(
        all(d.sha(d.ROOT / p) == h for p, h in protocol["protected_sha256"].items()), "Input drift"
    )
    out.mkdir(parents=True)
    old = json.loads((A2 / "run01/results.json").read_bytes())
    sources = json.loads((A2 / "protocol.json").read_bytes())["selected_sources"]
    control = json.loads((A2 / "controls.json").read_bytes())["controls"]
    os.environ["TIKTOKEN_CACHE_DIR"] = str(d.ROOT / ".venv/tokenizer-cache")
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        token_status = "cl100k_base cached offline encoding"
    except (ImportError, RuntimeError, ValueError) as exc:
        encoder = None
        token_status = "NOT_MEASURED: " + type(exc).__name__
    from crawler.core.models import FetchResult
    from crawler.core.parsers.pdf_parser import parse_pdf

    records = {}
    snapshots = {}
    all_origins = {}
    for source in sources:
        raw = source["body_sha256"]
        body = (d.EXPORT / "raw" / raw).read_bytes()
        d.require(hashlib.sha256(body).hexdigest() == raw, "Raw hash mismatch")
        fetch = FetchResult(source["url"], 200, source["content_type"], body, {})
        if source["source_type"] == "html":
            parser, origins = observed_html_parser()
            parsed = parser(fetch, source["url"])
        else:
            parsed = parse_pdf(fetch, source["url"])
            origins = [
                {
                    "tag": "pdf_page",
                    "page_ref": s.page_ref,
                    "origin_mapping_status": "PDF_PAGE_AND_TEXT_RANGE",
                    "original_context_excerpt": s.text[:240],
                }
                for s in parsed.segments
            ]
        segments = d.parsed_snapshot(parsed)
        expected = next(s for s in old["outputs"] if s["arm"] == "B01" and s["raw_sha256"] == raw)
        d.require(d.digest(segments) == expected["parse_snapshot_sha256"], "Read-only replay drift")
        d.require(len(origins) == len(segments), "Observer alignment mismatch")
        snapshots[raw] = segments
        all_origins[raw] = origins
        decision = next(
            s for s in old["admission_decisions"] if s["arm"] == "B01" and s["raw_sha256"] == raw
        )
        baseline = next(s for s in old["outputs"] if s["arm"] == "B00" and s["raw_sha256"] == raw)
        b0 = {s["segment_index"] for s in baseline["segments"] if s["admitted"]}
        for i, (s, o) in enumerate(zip(segments, origins)):
            meta = expected["segments"][i]
            d.require(s["text_sha256"] == meta["text_sha256"], "Segment identity mismatch")
            record = {
                "id": stable_id(raw, i),
                "raw_sha256": raw,
                "source_url": source["url"],
                "acquisition_ags": source["ags"],
                "source_type": source["source_type"],
                "segment_index": i,
                "parser_order_index": s["order_index"],
                "page_ref": s["page_ref"],
                "text": s["text"],
                "text_sha256": s["text_sha256"],
                "characters": len(s["text"]),
                "token_count": len(encoder.encode(s["text"], disallowed_special=()))
                if encoder
                else None,
                "heading_context": s["heading_context"],
                "segment_type": s["segment_type"],
                "origin": o,
                "B00_admitted": i in b0,
                "B01_admitted": meta["admitted"],
                "admission_reason": decision["decisions"][i]["reason"],
                "admission_decision": decision["decisions"][i],
                "whole_segment_review_status": "UNASSESSED",
                "prior_control_spans": [],
            }
            for obs in old["observations"]:
                if obs["arm"] == "B01" and obs["group"] == "control" and obs["raw_sha256"] == raw:
                    pieces = [
                        p
                        for hit in obs["output_matches"]
                        for p in hit["pieces"]
                        if p["segment_index"] == i
                    ]
                    if pieces:
                        record["prior_control_spans"].append(
                            {
                                "id": obs["id"],
                                "expected": obs["expected"],
                                "match_status": obs["status"],
                                "pieces": pieces,
                                "whole_segment_validation": False,
                            }
                        )
            records[record["id"]] = record
    b01 = [r for r in records.values() if r["B01_admitted"]]
    groups = defaultdict(list)
    for r in b01:
        groups[r["text_sha256"]].append(r["id"])
    duplicate_groups = []
    for text_sha, ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        dup_id = "D" + str(len(duplicate_groups) + 1).zfill(2)
        raws = {records[i]["raw_sha256"] for i in ids}
        contexts = {d.digest(records[i]["heading_context"]) for i in ids}
        duplicate_groups.append(
            {
                "id": dup_id,
                "text_sha256": text_sha,
                "text": records[ids[0]]["text"],
                "occurrence_ids": ids,
                "occurrences": len(ids),
                "excess": len(ids) - 1,
                "source_count": len(raws),
                "context_count": len(contexts),
                "within_source_excess": sum(
                    n - 1 for n in Counter(records[i]["raw_sha256"] for i in ids).values()
                ),
                "cross_source_excess": len(raws) - 1,
                "review_status": "UNASSESSED",
            }
        )
    membership = {i: g["id"] for g in duplicate_groups for i in g["occurrence_ids"]}
    for r in records.values():
        r["duplicate_group"] = membership.get(r["id"])
    added = [r for r in records.values() if r["B01_admitted"] and not r["B00_admitted"]]
    removed = [r for r in records.values() if r["B00_admitted"] and not r["B01_admitted"]]
    d.require(len(added) == 338 and len(removed) == 2, "Difference count mismatch")
    d.require(
        len(b01) == 425 and sum(g["excess"] for g in duplicate_groups) == 75,
        "Duplicate census mismatch",
    )
    # Independently compare difference IDs with archived multiset-based A2 post-analysis.
    prior = json.loads((A2 / "post_analysis.json").read_bytes())
    prior_ids = {
        stable_id(x["raw_sha256"], x["segment_index"])
        for x in prior["inventories"]["B01"]["additional_units"]
    }
    d.require(prior_ids == {r["id"] for r in added}, "Archived difference mismatch")
    sample = select_sample(added)
    sample["inventory_content_sha256"] = d.digest(added)
    d.write(
        out / "inventory.json", {"added": added, "removed": removed, "token_metric": token_status}
    )
    d.write(out / "sample_freeze.json", sample)
    d.write(
        out / "duplicates.json",
        {
            "groups": duplicate_groups,
            "total_excess": 75,
            "within_source_excess": sum(g["within_source_excess"] for g in duplicate_groups),
            "cross_source_excess": sum(g["cross_source_excess"] for g in duplicate_groups),
            "denominator_B01_segments": 425,
        },
    )
    # Original locator records for every duplicate occurrence, without whole unchanged documents.
    ids = {i for g in duplicate_groups for i in g["occurrence_ids"]}
    d.write(out / "duplicate_origins.json", {i: records[i] for i in sorted(ids)})
    c22 = next(c for c in control if c["control_id"] == "C22")
    hits = next(
        o
        for o in old["observations"]
        if o["arm"] == "B01" and o["group"] == "control" and o["id"] == "C22"
    )
    c22_origins = [
        records[stable_id(c22["raw_sha256"], p["segment_index"])]
        for hit in hits["output_matches"]
        for p in hit["pieces"]
    ]
    matched = [r for r in c22_origins if r["origin"].get("dom_path") == c22["dom_path"]]
    d.write(
        out / "c22_lineage.json",
        {
            "historical_status": "UNRESOLVED_MAPPING",
            "frozen_control_dom_path": c22["dom_path"],
            "competing_origins": c22_origins,
            "matching_frozen_dom_ids": [r["id"] for r in matched],
            "new_audit_mapping_status": "UNIQUE_ORIGINAL_DOM_LINEAGE"
            if len(matched) == 1
            else "UNRESOLVED",
            "historical_annotations_changed": False,
        },
    )
    d.require(
        all(d.sha(d.ROOT / p) == h for p, h in protocol["protected_sha256"].items()),
        "Inputs modified",
    )
    d.write(
        out / "receipt.json",
        {
            "status": "READ_ONLY_INVENTORY_AND_SAMPLE_FROZEN",
            "protected_inputs_unchanged": True,
            "source_versions_replayed": 20,
            "parser_output_sha_matches": 20,
            "added": 338,
            "removed": 2,
            "sample": len(sample["selected_ids"]),
            "duplicate_groups": len(duplicate_groups),
            "duplicate_excess": 75,
            "script_sha256": d.sha(Path(__file__)),
            "sample_sha256": d.sha(out / "sample_freeze.json"),
            "python": platform.python_version(),
            "libraries": {p: importlib.metadata.version(p) for p in ["beautifulsoup4", "lxml"]},
        },
    )
    print(
        json.dumps(
            {
                "added": 338,
                "removed": 2,
                "sample": len(sample["selected_ids"]),
                "sample_sha256": d.sha(out / "sample_freeze.json"),
                "media": sample["stratum_counts"]["media"],
                "duplicates": len(duplicate_groups),
            }
        )
    )


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    main(ap.parse_args().out.resolve())
