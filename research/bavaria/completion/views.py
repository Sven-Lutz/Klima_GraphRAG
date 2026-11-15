"""Verified, regenerable original-first views; never substitute parsed pipeline text."""

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup


def build(selection, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(selection).read_text())
    summaries = []
    for s in manifest["selection"]:
        p = Path(s["raw_path"])
        data = gzip.open(p, "rb").read() if p.suffix == ".gz" else p.read_bytes()
        assert hashlib.sha256(data).hexdigest() == s["sha256"]
        pages = []
        if data.lstrip().startswith(b"%PDF"):
            text = subprocess.run(
                ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True
            ).stdout.decode("utf-8")
            pages = [
                {"physical_page": i + 1, "text": page}
                for i, page in enumerate(text.split("\f"))
                if page.strip()
            ]
            kind = "PDF"
        else:
            soup = BeautifulSoup(data, "html.parser")
            for node in soup(["script", "style", "noscript"]):
                node.decompose()
            main = soup.find("main") or soup.find("article") or soup.body or soup
            pages = [{"physical_page": None, "text": main.get_text(" ", strip=True)}]
            kind = "HTML"
        (root / (s["id"] + ".json")).write_text(
            json.dumps({"source": s, "format": kind, "pages": pages}, ensure_ascii=False, indent=2)
            + "\n"
        )
        snippets = []
        for page in pages:
            text = " ".join(page["text"].split())
            matches = list(
                re.finditer(
                    r"beauftragt|beschloss|Förderprogramm|Stadtwerke|installiert|fertiggestellt|Maßnahme|Bayernwerk|Förderung",
                    text,
                    re.I,
                )
            )
            for m in matches[:4]:
                snippet = text[max(0, m.start() - 120) : m.start() + 700]
                if snippet not in snippets:
                    snippets.append(f"p{page['physical_page']}: " + snippet)
            if len(snippets) > 6:
                break
        summaries.append(
            f"\n### {s['id']} {s['name']} {kind} {s['url']}\n" + "\n".join(snippets[:6])
        )
    (root / "preview.txt").write_text("\n".join(summaries))
    print("Verified original views", len(summaries))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--selection", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    build(a.selection, a.output)
