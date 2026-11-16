"""Check public dossier references, hashes and declared study boundaries."""

import csv
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "docs/paper"


def main():
    required = [
        "RESEARCH_DOSSIER.md",
        "CLAIM_EVIDENCE_REGISTER.csv",
        "EXPERIMENT_MATRIX.csv",
        "ARTIFACT_MANIFEST.tsv",
        "LITERATURE.bib",
        "PAPER_OUTLINE.md",
        "HUMAN_REVIEW_PROTOCOL.md",
        "REPRODUCTION_GUIDE.md",
        "UX_EVALUATION_PROTOCOL.md",
        "OPEN_GATES.md",
    ]
    assert all((PAPER / p).is_file() for p in required)
    text = (PAPER / "RESEARCH_DOSSIER.md").read_text()
    assert len(re.findall(r"^## \d+\.", text, re.M)) == 22
    for row in csv.DictReader((PAPER / "ARTIFACT_MANIFEST.tsv").open(), delimiter="\t"):
        p = (ROOT / row["path"]).resolve()
        assert p.is_relative_to(ROOT) and p.is_file()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"], row["path"]
    claims = list(csv.DictReader((PAPER / "CLAIM_EVIDENCE_REGISTER.csv").open()))
    assert len({r["claim_id"] for r in claims}) == len(claims)
    for row in claims:
        assert row["status"] in {"VERIFIED", "PROVISIONAL", "NOT_EVALUATED", "CONTRADICTED"}
        assert row["independent_human_validation"] == "false"
        assert hashlib.sha256((ROOT / row["source"]).read_bytes()).hexdigest() == row["sha256"]
    for row in csv.DictReader((PAPER / "EXPERIMENT_MATRIX.csv").open()):
        if row["stage"] == "FUTURE EXPERIMENT":
            assert row["result"] == "NOT_EVALUATED" and row["reproduction_status"] == "NOT_EXECUTED"
    bib = (PAPER / "LITERATURE.bib").read_text()
    keys = re.findall(r"@\w+\{([^,]+),", bib)
    assert len(keys) == len(set(keys)) == 6
    for p in PAPER.glob("*.md"):
        for link in re.findall(r"\]\(([^)]+)\)", p.read_text()):
            if not link.startswith(("https:", "http:", "#")):
                assert (p.parent / link.split("#")[0]).is_file(), (p.name, link)
    print("PASS: dossier structure, artifact hashes, claim provenance, future/result boundary")


if __name__ == "__main__":
    main()
