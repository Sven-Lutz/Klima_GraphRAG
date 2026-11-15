"""Read-only DS007 snapshot adapter; writes ONLY the separate DS008 analysis."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from crawler.core.attribution import EvidenceText, attribute

ROOT = Path(__file__).resolve().parents[3]
NAMES = ("Burggen", "Bernbeuren", "Nürnberg")
CASES = {"09190118": "Burggen", "09564000": "Nürnberg"}
ARMS = ("burggen-heuristic", "burggen-structured", "nuernberg-heuristic", "nuernberg-structured")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def evaluate(base=ROOT / "generated/ds007"):
    results = []
    for arm in ARMS:
        folder = base / arm
        run, transport = read(folder / "run.json"), read(folder / "transport.json")
        acquisition = dict(ags=run["ags"], name=CASES[run["ags"]])
        successful = {}
        for event in transport["requests"]:
            if event["status"] == 200 and event["complete"] and not event["error"]:
                data = (folder / event["body_path"]).read_bytes()
                if sha(data) != event["sha256"]:
                    raise ValueError(f"Snapshot integrity failure: {arm} {event['number']}")
                successful.setdefault(event["url"], []).append(event)
        with sqlite3.connect(f"file:{folder / 'pilot.sqlite'}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            docs = con.execute("SELECT * FROM documents_raw ORDER BY document_id").fetchall()
            for doc in docs:
                # A source event is not uniquely recoverable for duplicate final
                # URLs; preserve all candidates instead of inventing a mapping.
                events = successful[doc["url_final"]]
                segments = con.execute(
                    "SELECT * FROM segments WHERE document_id=? ORDER BY order_index",
                    (doc["document_id"],),
                ).fetchall()
                first_heading = next((s["order_index"] for s in segments
                                      if s["segment_type"] == "heading"), None)
                inputs = []
                for segment in segments:
                    locator = dict(source_id=f"{arm}:{doc['document_id']}",
                        url=doc["url_final"], document_id=doc["document_id"],
                        database=f"generated/ds007/{arm}/pilot.sqlite",
                        order_index=segment["order_index"], page_ref=segment["page_ref"],
                        segment_text_sha256=sha(segment["text"].encode()),
                        raw_response_candidates=[dict(number=e["number"], sha256=e["sha256"],
                            body_path=f"generated/ds007/{arm}/{e['body_path']}") for e in events])
                    kind = "heading" if segment["segment_type"] == "heading" else (
                        "pdf_page" if segment["page_ref"] is not None else "body")
                    primary = (kind == "heading" and segment["order_index"] == first_heading) or (
                        kind == "pdf_page" and segment["order_index"] == 0)
                    inputs.append(EvidenceText(segment["text"], kind, locator, primary))
                result = attribute(acquisition, inputs, municipality_names=NAMES)
                results.append(dict(analysis_version="ds008-v1-posthoc",
                    row_id=f"{arm}:{doc['document_id']}", arm=arm, url=doc["url_final"],
                    document_id=doc["document_id"], data_class="real_ds007_snapshot",
                    baseline_assumed_scope=acquisition["name"],
                    baseline_is_counterfactual="acquisition_ags_interpreted_as_scope; not a measured emitted claim",
                    **result))
                if arm == "burggen-structured" and doc["url_final"].endswith("/gemeinde/gemeinderat"):
                    # Private occupational role is a separate analysis unit.
                    # Council membership does not document the network's territory.
                    profile = [i for i in inputs if i.locator["order_index"] in (19, 20, 21)]
                    assert len(profile) == 3 and "Betreiber Nahwäremenetz" in profile[-1].text
                    claim_result = attribute(acquisition, profile, municipality_names=NAMES, unit="claim")
                    results.append(dict(analysis_version="ds008-v1-posthoc",
                        row_id=f"{arm}:{doc['document_id']}:private_operator", arm=arm,
                        url=doc["url_final"], document_id=doc["document_id"],
                        data_class="real_ds007_snapshot", baseline_assumed_scope=acquisition["name"],
                        baseline_is_counterfactual="acquisition_ags_interpreted_as_scope; not a measured emitted claim",
                        inspection_locator=[dict(i.locator, quote=i.text) for i in profile if i.locator["order_index"] != 20],
                        **claim_result))
    return results


def metrics(rows):
    documents = [r for r in rows if r["attribution_unit"] == "document_primary_scope"]
    return dict(analysis="posthoc_not_gold", document_entries=len(documents),
        unique_final_urls=len({r["url"] for r in documents}),
        statuses=dict(Counter(r["attribution_status"] for r in documents)),
        by_arm={a: dict(Counter(r["attribution_status"] for r in documents if r["arm"] == a)) for a in ARMS},
        private_operator_claims=[dict(row_id=r["row_id"], status=r["attribution_status"])
                                for r in rows if r["attribution_unit"] == "claim"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", type=Path, default=ROOT / "generated/ds007")
    ap.add_argument("--output", type=Path, default=Path(__file__).parent / "attribution.csv")
    args = ap.parse_args()
    rows = evaluate(args.artifacts)
    # Only DS008 output may be written; do not overwrite an input snapshot.
    if args.output.resolve().parent != Path(__file__).resolve().parent:
        raise ValueError("Output must be in research/experiments/ds008/")
    fields = sorted({k for row in rows for k in row})
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})
    print(json.dumps(metrics(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
