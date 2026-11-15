"""Pre-candidate deterministic authentic control selection from frozen originals."""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from research.experiments.m4a_parsing_admission import diagnose as d

BASE = Path(__file__).resolve().parent


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def node_path(node):
    parts = []
    while getattr(node, "name", None) and node.name != "[document]":
        siblings = node.parent.find_all(node.name, recursive=False) if node.parent else [node]
        index = next(i for i, n in enumerate(siblings) if n is node)
        parts.append(f"{node.name}[{index}]")
        node = node.parent
    return "/".join(reversed(parts))


def main():
    sys.addaudithook(d.guard)
    release = json.loads(d.RELEASE.read_bytes())
    pilot = {s["raw_sha256"] for s in release["sources"]}
    catalogs = [json.loads(x) for x in (d.EXPORT / "catalog.jsonl").read_bytes().splitlines()]
    units = [json.loads(x) for x in (d.EXPORT / "corpus.jsonl").read_bytes().splitlines()]
    pools = {
        k: []
        for k in [
            "positive_short",
            "positive_numeric",
            "positive_context",
            "negative_navigation",
            "negative_boilerplate",
            "duplicate",
            "context_unknown",
            "scope_guard",
        ]
    }
    exclusions = []
    texts = Counter(clean(u["text"]) for u in units)
    for cat in catalogs:
        raw = cat["body_sha256"]
        body = (d.EXPORT / "raw" / raw).read_bytes()
        d.require(hashlib.sha256(body).hexdigest() == raw, "Original mismatch")
        if cat["source_type"] != "html":
            exclusions.append(
                {
                    "raw_sha256": raw,
                    "reason": "HTML control frame only; PDF retained in development",
                }
            )
            continue
        if raw in pilot:
            exclusions.append(
                {
                    "raw_sha256": raw,
                    "reason": "Prefer non-pilot controls; enough non-pilot candidates required",
                }
            )
            continue
        soup = BeautifulSoup(body, "lxml")
        tags = list(soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "a"]))
        for tag in tags:
            text = clean(tag.get_text(" ", strip=True))
            if not 12 <= len(text) <= 600 or tag.find(["p", "li"]):
                continue
            path = node_path(tag)
            in_nav = bool(
                tag.find_parent(["nav", "footer", "aside"])
                or any(
                    "navigation" in str(p.get("role", ""))
                    for p in [tag, *tag.parents]
                    if getattr(p, "attrs", None) is not None
                )
            )
            title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
            heading = tag.find_previous(["h1", "h2", "h3", "h4"])
            context = clean(heading.get_text(" ", strip=True)) if heading else None
            origin = {
                "raw_sha256": raw,
                "source_url": cat["url"],
                "acquisition_ags": cat["ags"],
                "dom_path": path,
                "tag": tag.name,
                "quote": text,
                "quote_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "context_heading": context,
                "document_title": title,
                "source_type": "html",
                "pilot_source_overlap": False,
                "review_status": "PROVISIONAL_SAME_AGENT_ORIGINAL_DOM_CHECK",
                "raw_byte_range": next(iter(d.occurrences(body, text.encode())), None),
                "dependency_key": raw,
            }
            unit_matches = [
                u for u in units if u["raw_hashes"] == [raw] and text in clean(u["text"])
            ]
            origin["historical_unit_ids"] = [u["unit_id"] for u in unit_matches]
            stable = d.digest({"raw": raw, "path": path, "quote": text})
            origin["selection_key"] = stable
            kind = None
            topical_url = bool(re.search("windkraft|energie|klima|fahrrad", cat["url"], re.I))
            if in_nav and tag.name == "a":
                kind = "negative_navigation"
            elif (
                "impressum" in cat["url"].lower()
                and tag.name in {"p", "li"}
                and not in_nav
                and re.search(
                    r"Telefon|Fax|E-Mail|Copyright|Haftung|Datenschutz|TMG|Umsatz|verantwort",
                    text,
                    re.I,
                )
            ):
                kind = "negative_boilerplate"
            elif not in_nav and tag.name in {"p", "li"} and not tag.find_parent(["header"]):
                if texts[text] > 1 and unit_matches and topical_url:
                    kind = "duplicate"
                elif "Stadt Rosenheim" in text and "zuständig" in text and len(text) < 300:
                    kind = "scope_guard"
                elif (
                    topical_url
                    and re.search(r"\d", text)
                    and re.search(
                        r"kWh|\bkW\b|%|Stellplätz|Inbetriebnahme|CO2|€|\bDM\b", text, re.I
                    )
                ):
                    kind = "positive_short" if len(text) < 80 else "positive_numeric"
                elif (
                    topical_url
                    and re.search(r"Förder|Energie|Klimaschutz|Strom|Fahrr", text)
                    and len(text) >= 80
                ):
                    kind = "positive_context"
                elif len(text) < 30 and not re.search(r"\d|[.!?]", text):
                    kind = "context_unknown"
            if kind:
                # Keep repeated locators in the pool; deduplicate selected quotes.
                pools[kind].append(origin)
    quotas = {
        "positive_short": 4,
        "positive_numeric": 4,
        "positive_context": 4,
        "negative_navigation": 4,
        "negative_boilerplate": 4,
        "duplicate": 2,
        "context_unknown": 1,
        "scope_guard": 1,
    }
    selected, seen = [], set()
    for kind, quota in quotas.items():
        candidates = sorted(pools[kind], key=lambda x: x["selection_key"])
        count = 0
        for row in candidates:
            if row["quote_sha256"] in seen:
                continue
            chosen = dict(row, category=kind, control_id=f"C{len(selected) + 1:02d}")
            chosen["expected"] = (
                "POSITIVE"
                if kind.startswith("positive")
                else "NEGATIVE"
                if kind.startswith("negative")
                else "REDUNDANCY"
                if kind == "duplicate"
                else "UNASSESSED"
                if kind == "context_unknown"
                else "SCOPE_GUARD"
            )
            chosen["task_scope"] = (
                "What concrete municipal climate/energy/sustainable-mobility information "
                "does this source report, with its original scope retained?"
            )
            chosen["rationale"] = {
                "positive_short": (
                    "Numeric/typed statement in topical source; local heading retained"
                ),
                "positive_numeric": (
                    "Numeric/typed statement in topical source; provisional relevance"
                ),
                "positive_context": (
                    "Topical source assertion; context required and provisional relevance"
                ),
                "negative_navigation": (
                    "Original DOM ancestor is navigation/footer/aside or navigation role"
                ),
                "negative_boilerplate": (
                    "Legal-notice source paragraph, not policy evidence for task"
                ),
                "duplicate": (
                    "Authentic content repeats in frozen corpus; not inherently irrelevant"
                ),
                "context_unknown": (
                    "Short original fragment; relevance unresolved, never counted negative"
                ),
                "scope_guard": (
                    "Original explicitly specifies Rosenheim jurisdiction; not a Straubing claim"
                ),
            }[kind]
            selected.append(chosen)
            seen.add(row["quote_sha256"])
            count += 1
            if count == quota:
                break
    result = {
        "status": "PRE_CODE_PROVISIONAL_CONTROL_FREEZE",
        "maximum_controls": 24,
        "selection": (
            "Non-pilot HTML originals; fixed structural/te"
            "xt classes then ascending SHA selection_key; "
            "deduplicate selected quote hashes across categories; no candidate outputs"
        ),
        "quota": quotas,
        "pool_counts": {k: len(v) for k, v in pools.items()},
        "controls": selected,
        "exclusions": exclusions,
        "candidate_pool": {
            k: [
                {a: r[a] for a in ["selection_key", "raw_sha256", "dom_path", "quote_sha256"]}
                for r in sorted(v, key=lambda x: x["selection_key"])
            ]
            for k, v in pools.items()
        },
        "independent_human_validation": False,
        "research_gold": "PENDING_EXTERNAL_VALIDATION",
        "preparation_script_sha256": d.sha(Path(__file__)),
    }
    out = BASE / "selection_draft02.json"
    d.require(not out.exists(), "No overwrite")
    d.write(out, result)
    for row in selected:
        print(row["control_id"], row["category"], row["source_url"], repr(row["quote"]))
    print("POOLS", result["pool_counts"])


if __name__ == "__main__":
    main()
