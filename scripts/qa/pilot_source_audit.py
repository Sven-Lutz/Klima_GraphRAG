"""Bounded original-first development diagnostics, never independent research labels."""

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

from crawler.web.bavaria import create_bavaria_service

PREDICATES = ["FUNDS", "PART_OF", "CREDITS", "PLANS", "HAS_REPORTED_STATUS", "HAS_REPORTED_VALUE"]


def audit(manifest, output):
    output.mkdir(parents=True, exist_ok=False)
    service = create_bavaria_service(manifest)
    w = service.workspace
    chosen = [next(r for r in w.relation_rows if r["predicate"] == p) for p in PREDICATES]
    selection = {
        "schema": "pilot-development-selection-v1",
        "rule": "First stored relation ID per six predeclared predicates",
        "predicates": PREDICATES,
        "relations": [r["relation_id"] for r in chosen],
        "snapshot_id": w.snapshot_id,
        "independent_human_review": False,
        "human_gold": False,
    }
    (output / "SELECTION.json").write_text(json.dumps(selection, indent=2) + "\n")
    rows = []
    for relation in chosen:
        r = relation["record"]
        source = w.source_rows[r["source_sha256"]]
        path = Path(source["raw_path"])
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            data = stream.read(16 * 1024**2 + 1)
        assert hashlib.sha256(data).hexdigest() == r["source_sha256"]
        image = None
        if data.startswith(b"%PDF"):
            page = int(r["page_ref"])
            with tempfile.TemporaryDirectory(prefix="klima-pilot-original-") as temp:
                pdf = Path(temp) / "original.pdf"
                pdf.write_bytes(data)
                text = subprocess.run(
                    ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                    capture_output=True,
                    check=True,
                    timeout=15,
                ).stdout.decode()
                # Only two original pages, not a corpus render.
                image = f"{r['source_sha256'][:12]}-page-{page}"
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-scale-to",
                        "1600",
                        "-singlefile",
                        "-png",
                        str(pdf),
                        str(output / image),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=15,
                )
        else:
            soup = BeautifulSoup(data, "html.parser")
            for n in soup(["script", "style", "noscript"]):
                n.decompose()
            text = soup.get_text(" ", strip=True)
        def normalize(x):
            return re.sub(r"\s+", " ", x).strip()
        quote = normalize(r["quote"])
        original = normalize(text)
        at = original.find(quote)
        rows.append(
            {
                "relation_id": relation["relation_id"],
                "predicate": relation["predicate"],
                "subject": w.entities[relation["subject_id"]]["label"],
                "object": w.entities[relation["object_id"]]["label"],
                "original_sha256": r["source_sha256"],
                "original_url": r["source_url"],
                "page": r["page_ref"],
                "locator": r["original_locator"],
                "reference_time": r["reference_time"],
                "claim_scope_ags": r["claim_scope_ags"],
                "original_bytes_rehashed": True,
                "normalized_quote_in_original": at >= 0,
                "quote": r["quote"],
                "original_context": original[max(0, at - 150) : at + len(quote) + 250]
                if at >= 0
                else original[:800],
                "original_page_image": image + ".png" if image else None,
                "assessment_status": "PROVISIONAL_AGENT_DEVELOPMENT_DIAGNOSTIC",
                "independent_human_validation": False,
            }
        )
    (output / "ORIGINAL_CHECKS.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "sample": len(rows),
                "quote_matches": sum(r["normalized_quote_in_original"] for r in rows),
                "source_versions": len({r["original_sha256"] for r in rows}),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    audit(a.manifest, a.output)
