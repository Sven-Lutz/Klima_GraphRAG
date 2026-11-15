"""Read-only PDF-block, C22 and C20/C21 source inspection; no repair rules."""

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

from research.experiments.m4a2_segmentation_admission.prepare_controls import clean, node_path
from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent
A2 = d.ROOT / "research/experiments/m4a2_segmentation_admission"


def main(out=None):
    sys.addaudithook(d.guard)
    inventory = json.loads((BASE / "freeze01/inventory.json").read_bytes())
    controls = json.loads((A2 / "controls.json").read_bytes())["controls"]
    pdf = next(r for r in inventory["removed"] if r["source_type"] == "pdf")
    raw = d.EXPORT / "raw" / pdf["raw_sha256"]
    proc = subprocess.run(
        ["pdftotext", "-f", "2", "-l", "2", "-bbox-layout", str(raw), "-"],
        capture_output=True,
        check=True,
    )
    xml = ET.fromstring(proc.stdout)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page = xml.find(".//x:page", ns)
    blocks = []
    for index, block in enumerate(xml.findall(".//x:block", ns)):
        text = " ".join(w.text or "" for w in block.findall(".//x:word", ns))
        blocks.append(
            {
                "block_index": index,
                "bbox_points": block.attrib,
                "text_sha256": d.digest(text),
                "text_prefix": text[:170],
            }
        )
    paragraphs = [
        {"start_char": m.start(), "end_char": m.end(), "prefix": m.group()[:160]}
        for m in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", pdf["text"], re.S)
    ]
    contacts = [
        {"match": m.group(), "start_char": m.start(), "end_char": m.end()}
        for m in re.finditer(r"\b(?:Telefon|Tel\.)|\bFax\b|\bE-?Mail", pdf["text"], re.I)
    ]
    release = json.loads(d.RELEASE.read_bytes())
    evidence = next(c for c in release["cases"] if c["question_id"] == "SR-01")["evidence_spans"][0]
    d.require(
        pdf["text"][evidence["start_char"] : evidence["end_char"]] == evidence["quote"],
        "Original PDF grant locator mismatch",
    )
    render = Path("/tmp/klima-m4a3-pdf-audit/straubing-2.png")
    pdf_result = {
        "status": "ORIGINAL_PAGE_VISUALLY_AND_TEXTUALLY_REVIEWED",
        "raw_sha256": pdf["raw_sha256"],
        "physical_page": 2,
        "printed_page": "2",
        "original_segment_characters": pdf["characters"],
        "original_pdf_parser_threshold": {"whole_page_return_if_characters_le": 3000},
        "affected_occurrences": ["SR-01/0", "C-01/0"],
        "shared_unique_anchors": 1,
        "evidence_start_char": evidence["start_char"],
        "evidence_end_char": evidence["end_char"],
        "numbered_subsidy_paragraph_range": [237, 366],
        "section_3_range": [0, 498],
        "section_4_range": [500, 1037],
        "section_5_range": [1039, 2254],
        "contact_reference_paragraph_range": [1759, 1887],
        "application_contact_block_range": [1931, 2069],
        "contact_matches": contacts,
        "page_dimensions_points": page.attrib,
        "layout_blocks": blocks,
        "blank_line_runs_not_new_parser_segments": paragraphs,
        "cause": "2254-character page is returned whole at <=3"
        "000 threshold; two contact classes anywhere "
        "on the page trigger the A2 veto before the standard accept branch. "
        "The numbered grant paragraph itself contains neither contact class.",
        "caveat": "Application/contact information remains useful a"
        "pplication evidence; not all contacts are noise.",
        "generic_directions_only": [
            "Preserve page anchor but annotate logical r"
            "ule/paragraph and application/contact roles",
            "Evaluate negative evidence at appropriate block/"
            "type granularity rather than page-wide existence",
            "Preserve parent section and ordered list context"
            "; no isolated-number or contact blanket deletion",
        ],
        "repair_implemented": False,
        "visual_review_png_local_only": str(render),
        "visual_review_png_sha256": d.sha(render) if render.exists() else None,
    }
    c22 = next(c for c in controls if c["control_id"] == "C22")
    body = (d.EXPORT / "raw" / c22["raw_sha256"]).read_bytes()
    soup = BeautifulSoup(body, "lxml")
    target = next(n for n in soup.find_all("li") if node_path(n) == c22["dom_path"])
    element_ranges = list(d.occurrences(body, str(target).encode()))
    lineage = json.loads((BASE / "freeze01/c22_lineage.json").read_bytes())
    d.require(
        not lineage["matching_frozen_dom_ids"], "Unexpected target emission; inspect manually"
    )
    c22_result = {
        "status": "UNRESOLVED_TARGET_NOT_EMITTED",
        "original_target_dom_path": c22["dom_path"],
        "original_target_text": clean(target.get_text(" ", strip=True)),
        "original_role_heading": clean(
            target.find_previous(["h1", "h2", "h3"]).get_text(" ", strip=True)
        ),
        "original_target_parent_context": clean(target.parent.get_text(" ", strip=True)),
        "all_literal_raw_byte_ranges": list(d.occurrences(body, c22["quote"].encode())),
        "exact_target_element_raw_byte_ranges": element_ranges,
        "historical_unscoped_first_literal_range": c22["raw_byte_range"],
        "historical_range_limitation": "A2 preparation used first litera"
        "l byte match anywhere in source; "
        "that range is not scoped to the frozen DOM target. Never choose it as target proof.",
        "competing_output_origins": lineage["competing_origins"],
        "finding": "Both competing substring hits have reversible "
        "paragraph origins (initiative and categories), "
        "but neither is the intended Preisstifter list item"
        ". The short nonnumeric list item was not emitted. "
        "Original-role mapping does not justify selecting a convenient paragraph occurrence.",
        "historical_annotation_changed": False,
    }
    units = [json.loads(line) for line in (d.EXPORT / "corpus.jsonl").read_bytes().splitlines()]
    c21 = next(c for c in controls if c["control_id"] == "C21")
    related = [u for u in units if clean(u["text"]) == c21["quote"]]
    occurrences = []
    for u in related:
        rawhash = u["raw_hashes"][0]
        rawbytes = (d.EXPORT / "raw" / rawhash).read_bytes()
        d.require(d.sha(d.EXPORT / "raw" / rawhash) == rawhash, "C21 original drift")
        tree = BeautifulSoup(rawbytes, "lxml")
        origins = [
            {"dom_path": node_path(n), "tag": n.name, "text": clean(n.get_text(" ", strip=True))}
            for n in tree.find_all(["p", "li"])
            if clean(n.get_text(" ", strip=True)) == c21["quote"]
        ]
        d.require(bool(origins), "C21 original paragraph missing")
        occurrences.append(
            {
                "raw_sha256": rawhash,
                "source_url": u["source_url"],
                "historical_unit_id": u["unit_id"],
                "original_matches": origins,
            }
        )
    groups = json.loads((BASE / "freeze01/duplicates.json").read_bytes())["groups"]
    c20 = next(c for c in controls if c["control_id"] == "C20")
    c20groups = [g for g in groups if g["text"] == c20["quote"]]
    c21groups = [g for g in groups if g["text"] == c21["quote"]]
    special = {
        "C20": {
            "B01_groups": c20groups,
            "meaning": "Repeated media/cookie-consent UI in three Buttenheim sources; "
            "stale energy headings are not original policy context. Technical template repetition.",
        },
        "C21": {
            "original_corpus_occurrences": occurrences,
            "B01_exact_duplicate_groups": c21groups,
            "meaning": "Substantive bicycle-facility design para"
            "graph repeats in two URL versions of the "
            "same railway-station project, not evidence of two independent projects. "
            "Only one version is in the B01 comparison sou"
            "rce set: do not add it to the 75 B01 excesses.",
        },
    }
    out = Path(out) if out else BASE / "detailed_findings.json"
    d.require(not out.exists(), "Output must be absent")
    d.write(
        out,
        {
            "PDF": pdf_result,
            "C22": c22_result,
            "special_duplicates": special,
            "script_sha256": d.sha(Path(__file__)),
        },
    )
    print(
        json.dumps(
            {
                "pdf_page": 2,
                "c22_status": c22_result["status"],
                "c21_original_occurrences": len(occurrences),
                "c21_B01_duplicate_groups": len(c21groups),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    main(parser.parse_args().out)
