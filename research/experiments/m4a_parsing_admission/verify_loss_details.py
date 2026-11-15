"""Post-result mechanical inspection of substrings inside an existing reference.

Does not change the primary matcher, denominator, protocol or gold annotations.
"""

import argparse
import json
import sys
from pathlib import Path

from research.experiments.m4a_parsing_admission import diagnose as d


def run(out):
    d.require(not out.exists(), "Output must be absent")
    protocol = json.loads((d.BASE / "protocol.json").read_bytes())
    d.require(
        all(d.sha(d.ROOT / p) == h for p, h in protocol["protected_sha256"].items()), "Input drift"
    )
    sys.addaudithook(d.guard)
    from crawler.core.models import FetchResult
    from crawler.core.parsers.html_parser import parse_html

    manifest = json.loads(d.RELEASE.read_bytes())
    case = next(c for c in manifest["cases"] if c["question_id"] == "SR-04")
    evidence = case["evidence_spans"][0]
    body = (d.EXPORT / "raw" / evidence["raw_sha256"]).read_bytes()
    parsed = parse_html(
        FetchResult(evidence["source_url"], 200, "text/html", body, {}), evidence["source_url"]
    )
    snapshot = d.parsed_snapshot(parsed)
    findings = []
    for phrase in ["Barrierefreie Ladebuchten", "LKW Ladespur"]:
        d.require(phrase in evidence["quote"], "Inspection outside frozen evidence prohibited")
        raw_locations = list(d.occurrences(body, phrase.encode()))
        full_locations = list(d.occurrences(parsed.text, phrase))
        segment_locations = d.matches(snapshot, phrase)
        d.require(
            raw_locations and full_locations and not segment_locations,
            "Post-result segmentation-loss explanation not reproduced",
        )
        findings.append(
            {
                "phrase_within_existing_reference": phrase,
                "raw_byte_ranges": raw_locations,
                "parse_text_character_ranges": full_locations,
                "segment_locations": segment_locations,
                "raw_sha256": evidence["raw_sha256"],
                "reference_character_ranges": list(d.occurrences(evidence["quote"], phrase)),
            }
        )
    d.require(
        all(d.sha(d.ROOT / p) == h for p, h in protocol["protected_sha256"].items()),
        "Protected input changed",
    )
    d.write(
        out,
        {
            "status": "POST_RESULT_MECHANICAL_EXPLANATION_NOT_NEW_GOLD",
            "affected_occurrences": ["SR-04/0", "C-02/0"],
            "primary_results_unchanged": True,
            "additional_denominator_items": 0,
            "findings": findings,
            "driver_sha256": d.sha(Path(__file__)),
            "parser_sha256": d.sha(d.ROOT / "crawler/core/parsers/html_parser.py"),
        },
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    run(ap.parse_args().out)
