"""Original-context packet strictly for an already frozen sample; no ranking/IE."""

import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from research.experiments.m4a2_segmentation_admission.prepare_controls import clean, node_path
from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def main():
    sys.addaudithook(d.guard)
    frozen = BASE / "freeze01"
    sample = json.loads((frozen / "sample_freeze.json").read_bytes())
    inventory = json.loads((frozen / "inventory.json").read_bytes())
    records = {r["id"]: r for r in inventory["added"]}
    cache = {}
    packet = []
    for number, identity in enumerate(sample["selected_ids"], 1):
        r = records[identity]
        raw = r["raw_sha256"]
        if raw not in cache:
            body = (d.EXPORT / "raw" / raw).read_bytes()
            d.require(d.sha(d.EXPORT / "raw" / raw) == raw, "Original mismatch")
            soup = BeautifulSoup(body, "lxml")
            cache[raw] = {node_path(n): n for n in soup.find_all(True)}
        node = cache[raw].get(r["origin"]["dom_path"])
        d.require(node is not None, "Frozen original DOM path missing")
        own = clean(node.get_text(" ", strip=True))
        parent = clean(node.parent.get_text(" ", strip=True)) if node.parent else own
        occurrences = list(d.occurrences(parent, own))
        if occurrences:
            a, b = occurrences[0]
            parent = parent[max(0, a - 300) : min(len(parent), b + 300)]
        previous = node.find_previous_sibling()
        following = node.find_next_sibling()
        row = dict(
            r,
            review_object=f"S{number:02d}",
            original_parent_context=parent[:1800],
            original_previous_sibling=clean(previous.get_text(" ", strip=True))[:700]
            if previous
            else None,
            original_next_sibling=clean(following.get_text(" ", strip=True))[:700]
            if following
            else None,
            source_title=clean(node.find_parent("html").title.get_text(" ", strip=True))
            if node.find_parent("html") and node.find_parent("html").title
            else None,
            review_status="UNASSESSED",
        )
        packet.append(row)
    out = BASE / "sample_original_context.json"
    d.require(not out.exists(), "No packet overwrite")
    d.write(
        out,
        {
            "sample_sha256": d.sha(frozen / "sample_freeze.json"),
            "objects": packet,
            "script_sha256": d.sha(Path(__file__)),
        },
    )
    print("Prepared 48 original-bound review objects; no semantic labels generated.")


if __name__ == "__main__":
    main()
