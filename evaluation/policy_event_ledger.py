
"""Review-gated policy event ledger for municipal climate policies.
Usage: python -m evaluation.policy_event_ledger seed
       python -m evaluation.policy_event_ledger adjudicate
       python -m evaluation.policy_event_ledger selftest
"""
import argparse
import hashlib
import json
import re
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "evaluation/policy_evidence_review_v1/cases"
OUT = ROOT / "evaluation/policy_events_v1"

DOMAINS = dict(zip(
    ("09161000", "09162000", "09362000", "09462000",
     "09562000", "09563000", "09564000", "09663000", "09761000"),
    ("ingolstadt.de", "muenchen.de", "regensburg.de", "bayreuth.de",
     "erlangen.de", "fuerth.de", "nuernberg.de", "wuerzburg.de",
     "augsburg.de")
))

MEASURES = {
    "balkonkraftwerk_subsidy": r"balkonkraftwerk|stecker.?solar|mini.?solar",
    "rooftop_pv_subsidy": r"photovoltaik|pv.anlag|solaranlag",
    "heat_pump_heating_subsidy": r"w[aä]rmepump|heizungstausch",
    "building_energy_renovation": r"d[aä]mm|fenstertausch|geb[aä]udeh[uü]ll",
    "ev_charging_emobility": r"wallbox|ladeinfrastruktur|ladestation|ladepunkt"
}

POSITIVE = {"program_open", "program_extended"}
NEGATIVE = {"program_closed", "explicit_none"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def as_date(value):
    return date.fromisoformat(value) if value else None


def normalize(text):
    return " ".join(text.split()).casefold()


def official(url, city_id):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    domain = DOMAINS.get(city_id, "")
    return (
        parsed.scheme == "https"
        and not parsed.username
        and bool(domain)
        and (host == domain or host.endswith("." + domain))
    )


def load_packets():
    packets = {}
    for path in sorted(SRC.glob("MPB-*.json")):
        packet = json.loads(path.read_text(encoding="utf-8"))
        case = packet["benchmark_id"]
        require(case not in packets, "Duplicate case: " + case)

        for candidate in packet["candidates"]:
            actual = hashlib.sha256(
                candidate["text"].encode("utf-8")
            ).hexdigest()
            require(actual == candidate["text_sha256"],
                    "Evidence hash mismatch: " + case)
            require(candidate["source_snapshot_id"] == packet["snapshot_id"],
                    "Snapshot mismatch: " + case)

        packets[case] = packet

    require(bool(packets), "No evidence packets found at " + str(SRC))
    return packets


def priority(candidate, family):
    """Review ordering only; this never decides Yes or No."""
    text = candidate["text"].casefold()
    pattern = MEASURES.get(family, re.escape(family))
    score = 5 * bool(re.search(pattern, text, re.I))
    score += 4 * bool(re.search(
        r"f[oö]rder|zuschuss|bezuschuss", text, re.I
    ))
    score += 5 * bool(re.search(
        r"gef[oö]rdert wird|gef[oö]rdert werden|nicht mehr|eingestellt|beendet",
        text, re.I
    ))
    score -= 7 * bool(re.search(
        r"tagesordnung|vorberatung|stellt folgenden antrag|fraktion.{0,40}antrag",
        text, re.I
    ))
    score -= 6 * bool(re.search(
        r"stadion|schulgeb[aä]ude|[oö]ffentliche ladeinfrastruktur",
        text, re.I
    ))
    return score


def seed(packets):
    OUT.mkdir(parents=True, exist_ok=True)
    drafts = []

    page = [
        '<!doctype html><meta charset="utf-8">',
        "<title>Municipal policy review queue</title>",
        '<style>body{font:15px/1.5 system-ui;max-width:1000px;'
        'margin:30px auto}pre{white-space:pre-wrap;word-break:break-word}'
        'article{border-top:1px solid #ccc;padding:12px}</style>',
        "<h1>Policy event verification queue</h1>",
        "<p>All entries require manual source verification.</p>"
    ]

    for case, packet in packets.items():
        page.append(
            "<h2>" + escape(case + " — " + packet["municipality"]) +
            "</h2><p>" + escape(packet["question"]) + "</p>"
        )

        candidates = sorted(
            packet["candidates"],
            key=lambda c: (-priority(c, packet["policy_family"]), c["chunk_id"])
        )[:6]

        for c in candidates:
            draft = {
                "case": case,
                "policy_family": packet["policy_family"],
                "scheme_id": "",
                "measure": "",
                "beneficiary": "",
                "event": "",
                "effective_from": "",
                "effective_until": "",
                "published_on": "",
                "coverage_scope": "scheme_only",
                "as_of_checked": "",
                "primary_checked": False,
                "reviewed_by": "",
                "reviewed_on": "",
                "verification": "pending",
                "quote": "",
                "evidence_id": c["evidence_id"],
                "chunk_id": c["chunk_id"],
                "source_url": c["source_url"],
                "source_text_sha256": c["text_sha256"]
            }
            drafts.append(draft)

            url = escape(c["source_url"], quote=True)
            excerpt = escape(" ".join(c["text"].split())[:700])
            page.append(
                '<article><a href="' + url +
                '" target="_blank" rel="noopener">' + url +
                "</a><pre>" + excerpt + "</pre></article>"
            )

    for filename, contents in (
        ("draft_events.json", drafts),
        ("verified_events.json", [])
    ):
        path = OUT / filename
        if not path.exists():
            path.write_text(
                json.dumps(contents, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    (OUT / "review_queue.html").write_text(
        "\n".join(page), encoding="utf-8"
    )

    print("SEED:", len(packets), "cases;", len(drafts), "draft candidates")
    print("REVIEW:", OUT / "review_queue.html")
    print("LEDGER:", OUT / "verified_events.json")


def validate(record, packet):
    """Check provenance and human verification before accepting an event."""
    case = packet["benchmark_id"]
    matches = [
        c for c in packet["candidates"]
        if c["chunk_id"] == record.get("chunk_id")
        and c["evidence_id"] == record.get("evidence_id")
    ]
    require(len(matches) == 1, "Evidence not found: " + case)
    c = matches[0]

    require(record.get("source_url") == c["source_url"],
            "Source URL mismatch: " + case)
    require(record.get("source_text_sha256") == c["text_sha256"],
            "Source hash mismatch: " + case)
    require(record.get("policy_family") == packet["policy_family"],
            "Wrong policy family: " + case)
    require(official(c["source_url"], packet["municipality_id"]),
            "Source not on official municipal domain: " + case)

    require(
        record.get("primary_checked") is True
        and bool(record.get("reviewed_by", "").strip()),
        "Manual verification missing: " + case
    )

    quote = record.get("quote", "")
    require(
        len(quote.strip()) >= 28
        and normalize(quote) in normalize(c["text"]),
        "Quote not found in captured evidence: " + case
    )

    require(record.get("scheme_id", "").strip(),
            "Funding scheme ID missing: " + case)
    require(record.get("measure", "").strip(),
            "Policy measure missing: " + case)

    valid_beneficiaries = {
        "private", "housing_association", "business", "mixed", "public"
    }
    require(record.get("beneficiary") in valid_beneficiaries,
            "Invalid beneficiary: " + case)

    event = record.get("event")
    require(event in POSITIVE | NEGATIVE | {"proposal", "discussion"},
            "Invalid event type: " + case)

    coverage = record.get("coverage_scope")
    require(coverage in {"scheme_only", "entire_family"},
            "Invalid policy coverage: " + case)

    dates = {
        key: as_date(record.get(key))
        for key in (
            "effective_from", "effective_until", "published_on",
            "reviewed_on", "as_of_checked"
        )
    }

    for key in ("effective_from", "published_on",
                "reviewed_on", "as_of_checked"):
        require(dates[key] is not None,
                "Missing required date " + key + ": " + case)

    end = dates["effective_until"]
    start = dates["effective_from"]
    as_of = as_date(packet["as_of"])

    require(end is None or end >= start,
            "Invalid date range: " + case)
    require(dates["reviewed_on"] >= dates["as_of_checked"],
            "Review date precedes source check: " + case)
    require(dates["as_of_checked"] >= as_of,
            "Status not checked through benchmark date: " + case)

    if coverage == "entire_family":
        require(event == "explicit_none",
                "Entire-family absence requires explicit_none: " + case)
        require(bool(re.search(
            r"keine.{0,90}(?:f[oö]rderung|zusch[uü]sse|zuschuss)"
            r"|(?:bietet|gibt).{0,70}keine",
            quote, re.I
        )), "No explicit comprehensive non-offering quote: " + case)

    if record["beneficiary"] == "public":
        return None
    if dates["published_on"] > as_of or start > as_of:
        return None
    if end and end < as_of and event in POSITIVE:
        return None

    return record


def decide(events):
    """Resolve individual schemes without assuming one closure means no aid."""
    schemes = {}
    for event in events:
        if event["event"] in POSITIVE | NEGATIVE:
            schemes.setdefault(event["scheme_id"], []).append(event)

    latest_events = []
    for group in schemes.values():
        latest_date = max(as_date(e["effective_from"]) for e in group)
        latest = [
            e for e in group
            if as_date(e["effective_from"]) == latest_date
        ]

        statuses = {e["event"] in POSITIVE for e in latest}
        if statuses == {True, False}:
            return "Unknown", "Conflicting policy events", latest

        latest_events.extend(latest)

    active = [
        e for e in latest_events if e["event"] in POSITIVE
    ]
    if active:
        return "Yes", "Verified active municipal scheme", active

    comprehensive = [
        e for e in latest_events
        if e["event"] == "explicit_none"
        and e["coverage_scope"] == "entire_family"
    ]
    if comprehensive:
        return "No", "Verified comprehensive non-offering", comprehensive

    return "Unknown", "Insufficient verified evidence", latest_events


def adjudicate(packets):
    ledger = OUT / "verified_events.json"
    require(ledger.exists(), "Run seed first")

    events = json.loads(ledger.read_text(encoding="utf-8"))
    require(isinstance(events, list), "Ledger must be a JSON list")

    accepted = {case: [] for case in packets}

    for event in events:
        if event.get("verification") != "verified":
            continue

        case = event.get("case")
        require(case in packets, "Unknown case: " + str(case))

        checked = validate(event, packets[case])
        if checked is not None:
            accepted[case].append(checked)

    results = []
    for case, packet in packets.items():
        label, reason, citations = decide(accepted[case])
        results.append({
            "case": case,
            "city": packet["municipality"],
            "label": label,
            "reason": reason,
            "citations": [
                {
                    "url": e["source_url"],
                    "quote": e["quote"],
                    "evidence_id": e["evidence_id"]
                }
                for e in citations
            ]
        })

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "decisions.json"
    target.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        "DECISIONS:", len(results),
        "| Yes:", sum(r["label"] == "Yes" for r in results),
        "| No:", sum(r["label"] == "No" for r in results),
        "| Unknown:", sum(r["label"] == "Unknown" for r in results)
    )
    print("OUTPUT:", target)


def selftest():
    sample = {
        "scheme_id": "sample_scheme",
        "event": "program_open",
        "effective_from": "2026-01-01",
        "coverage_scope": "scheme_only"
    }

    assert decide([])[0] == "Unknown"
    assert decide([sample])[0] == "Yes"
    assert decide([{**sample, "event": "program_closed"}])[0] == "Unknown"
    assert decide([{
        **sample, "event": "explicit_none",
        "coverage_scope": "entire_family"
    }])[0] == "No"
    assert decide([sample, {
        **sample, "event": "program_closed"
    }])[0] == "Unknown"
    assert official("https://www.wuerzburg.de/test", "09663000")
    assert not official("https://fake-wuerzburg.de/test", "09663000")

    print("SELFTEST: 7/7 passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("selftest", "seed", "adjudicate")
    )
    args = parser.parse_args()

    if args.command == "selftest":
        selftest()
    else:
        packets = load_packets()
        if args.command == "seed":
            seed(packets)
        else:
            adjudicate(packets)


if __name__ == "__main__":
    main()
