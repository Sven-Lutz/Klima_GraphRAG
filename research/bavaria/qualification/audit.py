"""Offline source qualification and dependency audit; agent review is provisional."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from research.climate_policy.store import tags

RESERVE = 15 * 1024**3
OUTPUT_BUDGET = 12 * 1024**2
SELECTION_HASH = "b79736ab3b187be7aacfdda7f0ab09e41751125d1a518085cad2bf99ee7f0333"
GENERIC_MAIL = {"gmail.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "outlook.com"}
INSTITUTIONS = (
    "Bayernwerk Netz GmbH",
    "Institut für nachhaltige Energieversorgung",
    "Verbraucherzentrale Bayern",
    "C.A.R.M.E.N.",
    "Landshuter Energieagentur",
    "ILE Nationalparkgemeinden",
    "BfT Energieberatungs",
    "Institut für Energietechnik",
    "Verbraucher-Service Bayern",
)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def gate(root):
    if shutil.disk_usage(root).free < RESERVE + OUTPUT_BUDGET:
        raise RuntimeError("15-GiB reserve plus bounded output allowance unavailable")
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if total > OUTPUT_BUDGET:
        raise RuntimeError("qualification output allowance exceeded")


def select_sample(conn, destination):
    """Freeze only archived metadata; perform before inspecting original contents."""
    if Path(destination).exists():
        raise ValueError("Do not overwrite a sample freeze")
    records = {
        r["ags"]: json.loads(r["record"]) for r in conn.execute("SELECT * FROM municipalities")
    }
    pdf = {
        r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM units WHERE page_ref IS NOT NULL")
    }
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT a.*,d.raw_path,d.raw_bytes,d.parse_status FROM associations a "
            "JOIN documents d USING(sha256) ORDER BY a.ags,a.sha256,a.url"
        )
    ]
    selected, seen_ags, seen_sha = [], set(), set()
    for region in sorted({r["region"] for r in records.values()}):
        for stratum in ("HTML_small", "HTML_medium", "HTML_large", "PDF"):
            eligible = []
            for row in rows:
                record = records[row["ags"]]
                size = (
                    0 if record["population"] < 5000 else 1 if record["population"] < 20000 else 2
                )
                if (
                    record["region"] != region
                    or row["ags"] in seen_ags
                    or row["sha256"] in seen_sha
                ):
                    continue
                valid = (
                    row["sha256"] in pdf
                    if stratum == "PDF"
                    else (
                        row["sha256"] not in pdf
                        and size == {"HTML_small": 0, "HTML_medium": 1, "HTML_large": 2}[stratum]
                    )
                )
                if valid:
                    eligible.append(row)
            eligible.sort(
                key=lambda r: hashlib.sha256(
                    ("qualification-20261105-v1:" + r["ags"] + ":" + r["sha256"]).encode()
                ).hexdigest()
            )
            if not eligible:
                raise ValueError(f"No source available for {region}/{stratum}")
            chosen = eligible[0]
            record = records[chosen["ags"]]
            selected.append(
                {
                    **chosen,
                    "official_name": record["official_name"],
                    "name": record["name"],
                    "region": region,
                    "population": record["population"],
                    "size_class": stratum,
                    "selection_ordinal": len(selected) + 1,
                }
            )
            seen_ags.add(chosen["ags"])
            seen_sha.add(chosen["sha256"])
    result = {
        "protocol": "deterministic-seven-regions-three-size-HTML-and-PDF-v1",
        "status": "FROZEN_BEFORE_ORIGINAL_INSPECTION",
        "code_base": "e74e05e352ac3aa014b15e0111e0758e32a897d6",
        "documents_count": len(pdf),
        "corpus_versions": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
        "selection": selected,
    }
    Path(destination).write_text(json.dumps(result, ensure_ascii=False, indent=2))


def materialize_views(root):
    """Independent original DOM/page view; never use the pipeline's parsed units."""
    from bs4 import BeautifulSoup

    if sha(root / "selection.json") != SELECTION_HASH:
        raise ValueError("Preinspection source selection changed")
    folder = root / "original_views"
    folder.mkdir(exist_ok=True)
    for record in json.loads((root / "selection.json").read_text())["selection"]:
        path = Path(record["raw_path"])
        body = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        if hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise ValueError("Original integrity failure")
        if body.startswith(b"%PDF"):
            proc = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
                input=body,
                capture_output=True,
                timeout=30,
                check=True,
            )
            full = proc.stdout.decode()
            blocks = [
                {"locator": f"pdf-page:{i + 1}", "text": text.strip()}
                for i, text in enumerate(full.split("\f"))
                if text.strip()
            ]
            title, kind = blocks[0]["text"][:300] if blocks else "", "PDF"
        else:
            soup = BeautifulSoup(body, "lxml")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            for element in soup(["script", "style", "noscript", "svg"]):
                element.decompose()
            full = soup.get_text("\n", strip=True)
            blocks = []
            for i, element in enumerate(
                soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "address", "footer"])
            ):
                text = element.get_text(" ", strip=True)
                if len(text) >= 15:
                    blocks.append(
                        {"locator": f"original-DOM-{element.name}-ordinal:{i}", "text": text}
                    )
            kind = "HTML"
        view = {**record, "actual_format": kind, "title": title, "blocks": blocks}
        outputs = {
            folder / f"{record['selection_ordinal']:02d}.json": json.dumps(
                view, ensure_ascii=False, indent=2
            ),
            folder / f"{record['selection_ordinal']:02d}.txt": full,
        }
        for destination, content in outputs.items():
            if destination.exists():
                if destination.read_text() != content:
                    raise ValueError("Original view reconstruction differs; do not overwrite")
            else:
                gate(root)
                destination.write_text(content)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_csv(path, records):
    records = list(records)
    if not records:
        Path(path).write_text("")
        return
    with Path(path).open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def host(url):
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")


def email_corroboration(record):
    candidate = host(record.get("homepage_candidate"))
    domains = sorted(
        {
            contact["email"].strip().rsplit("@", 1)[-1].lower()
            for contact in record["official_contacts"]
            if contact.get("email") and "@" in contact["email"]
        }
    )
    matches = [
        domain
        for domain in domains
        if domain not in GENERIC_MAIL
        and candidate
        and (
            candidate == domain
            or candidate.endswith("." + domain)
            or domain.endswith("." + candidate)
        )
    ]
    return {
        "candidate_host": candidate,
        "official_email_domains": "|".join(domains),
        "matching_email_domains": "|".join(matches),
        "email_host_corroboration": (
            "OFFICIAL_CONTACT_HOST_OR_SUBDOMAIN_MATCH" if matches else "NO_MATCH_NOT_DISPROOF"
        ),
        "website_authority_verified": False,
    }


def review_originals(root):
    selection_path = root / "selection.json"
    if sha(selection_path) != SELECTION_HASH:
        raise ValueError("Preinspection sample freeze changed")
    selection = json.loads(selection_path.read_text())["selection"]
    decisions_path = Path(__file__).with_name("review_decisions.jsonl")
    decisions = [json.loads(line) for line in decisions_path.read_text().splitlines()]
    if {r["ordinal"] for r in decisions} != set(range(1, 29)):
        raise ValueError("All 28 selected originals require explicit decisions")
    output = []
    for decision in decisions:
        sample = selection[decision["ordinal"] - 1]
        view = json.loads((root / "original_views" / f"{decision['ordinal']:02d}.json").read_text())
        p = Path(sample["raw_path"])
        body = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
        if hashlib.sha256(body).hexdigest() != sample["sha256"]:
            raise ValueError("Original integrity violation")
        blocks = [b for b in view["blocks"] if b["locator"] == decision["locator"]]
        if len(blocks) != 1:
            raise ValueError("Ambiguous original DOM/page locator")
        text = re.sub(r"\s+", " ", blocks[0]["text"]).strip()
        cue = re.sub(r"\s+", " ", decision["cue"]).strip()
        spans = [(m.start(), m.end()) for m in re.finditer(re.escape(cue), text)]
        if not spans:
            raise ValueError(f"Original quote unavailable for {decision['ordinal']}: {cue}")
        output.append(
            {
                **decision,
                "ags": sample["ags"],
                "official_name": sample["official_name"],
                "region": sample["region"],
                "population": sample["population"],
                "stratum": sample["size_class"],
                "source_sha256": sample["sha256"],
                "source_url": sample["url"],
                "original_path": sample["raw_path"],
                "actual_format": view["actual_format"],
                "normalization": "whitespace_only",
                "quote": cue,
                "quote_spans_in_normalized_original_block": spans,
                "quote_occurrences": len(spans),
                "assessment": "PROVISIONAL_AGENT_ORIGINAL_REVIEW_NOT_HUMAN_GOLD",
                "authority_legal_verification": "NOT_ESTABLISHED",
                "publication_date": None,
                "implementation_independently_verified": False,
            }
        )
    (root / "original_reviews.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in output)
    )
    report = {
        "sample_count": len(output),
        "municipalities": len({r["ags"] for r in output}),
        "regions": dict(Counter(r["region"] for r in output)),
        "formats": dict(Counter(r["actual_format"] for r in output)),
        "relevance": dict(Counter(r["relevance"] for r in output)),
        "substantive_sources": sum(r["relevance"].startswith("SUBSTANTIVE") for r in output),
        "review_kind": "PROVISIONAL_AGENT_ORIGINAL_REVIEW",
        "independent_human_labels": 0,
        "sampling": (
            "Fixed 4 per region: three HTML size strata and one PDF; not probability-weighted"
        ),
        "population_precision_or_coverage_inference": "UNSUPPORTED",
        "all_quotes_located": True,
        "selection_sha256": SELECTION_HASH,
        "annotation_sha256": sha(decisions_path),
    }
    write_json(root / "original_review_report.json", report)
    return output, report


def coverage_layer(conn, root, reviews):
    sampled = defaultdict(list)
    for review in reviews:
        sampled[review["ags"]].append(review)
    source_counts = dict(
        conn.execute("SELECT ags,count(DISTINCT sha256) FROM associations GROUP BY ags")
    )
    text_counts = dict(
        conn.execute(
            "SELECT a.ags,count(DISTINCT a.sha256) FROM associations a "
            "JOIN units u USING(sha256) GROUP BY a.ags"
        )
    )
    records = []
    for row in conn.execute("SELECT * FROM municipalities ORDER BY ags"):
        r = json.loads(row["record"])
        sampled_sources = sampled.get(row["ags"], ())
        record = {
            "ags": row["ags"],
            "official_name": r["official_name"],
            "region": r["region"],
            "population": r["population"],
            "population_date": r["population_date"],
            "size_class": "small"
            if r["population"] < 5000
            else "medium"
            if r["population"] < 20000
            else "large",
            "crawl_status_unmodified": row["status"],
            "crawl_completed_at": row["checked_at"],
            "original_versions": source_counts.get(row["ags"], 0),
            "versions_with_text": text_counts.get(row["ags"], 0),
            **email_corroboration(r),
            "sampled_originals": len(sampled_sources),
            "sampled_policy_relevance": "|".join(x["relevance"] for x in sampled_sources),
            "sampled_claim_scope": "|".join(x["claim_scope"] for x in sampled_sources),
            "municipal_policy_coverage_qualified": "NOT_ESTABLISHED",
            "no_source_interpretation": "UNKNOWN_NOT_NO_POLICY",
        }
        records.append(record)
    write_csv(root / "municipality_quality_layer.csv", records)
    aliases = conn.execute(
        "SELECT count(*),count(DISTINCT sha256),count(DISTINCT url) FROM associations"
    ).fetchone()
    multi = conn.execute(
        "SELECT count(*) FROM (SELECT sha256 FROM associations "
        "GROUP BY sha256 HAVING count(DISTINCT ags)>1)"
    ).fetchone()[0]
    report = {
        "municipalities": len(records),
        "email_corroboration": dict(Counter(r["email_host_corroboration"] for r in records)),
        "independent_website_authority_verified": 0,
        "sampled_municipalities": len(sampled),
        "qualified_population_policy_coverage": None,
        "source_associations": aliases[0],
        "unique_source_bodies": aliases[1],
        "unique_source_urls": aliases[2],
        "bodies_associated_with_multiple_municipalities": multi,
        "source_body_association_is_not_claim_scope": True,
        "crawl_statuses_unmodified": dict(Counter(r["crawl_status_unmodified"] for r in records)),
        "interpretation": (
            "Official-contact host consistency is corroboration only, "
            "not a domain/claim authority verdict."
        ),
    }
    write_json(root / "coverage_quality_report.json", report)
    return records, report


class Components:
    def __init__(self, identifiers):
        self.parent = {identifier: identifier for identifier in identifiers}

    def find(self, identifier):
        while self.parent[identifier] != identifier:
            self.parent[identifier] = self.parent[self.parent[identifier]]
            identifier = self.parent[identifier]
        return identifier

    def join(self, identifiers):
        identifiers = sorted(set(identifiers))
        if not identifiers:
            return
        first = self.find(identifiers[0])
        for identifier in identifiers[1:]:
            self.parent[self.find(identifier)] = first


def url_family(url):
    p = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, urlencode(sorted(query)), ""))


def benchmark_audit(conn, root, quality_records, reviews):
    packet = Path("generated/bavaria/reviewer-packet/A/tasks.jsonl")
    selected = [json.loads(line) for line in packet.read_text().splitlines()]
    pool = {
        r["id"]: r
        for r in (
            json.loads(line)
            for line in Path("generated/bavaria/review-final/candidates.jsonl")
            .read_text()
            .splitlines()
        )
    }
    by_ags = {r["ags"]: r for r in quality_records}
    by_source = {r["source_sha256"]: r for r in reviews}
    tasks = []
    for task in selected:
        candidate = pool[task["id"]]
        unit = conn.execute(
            "SELECT * FROM units WHERE unit_id=?", (candidate["candidate_evidence"],)
        ).fetchone()
        if unit is None or unit["sha256"] != task["candidate_source"]:
            raise ValueError("Benchmark source/evidence mismatch")
        unit_text = re.sub(r"\s+", " ", unit["text"]).strip()
        q = by_ags[task["ags"]]
        institutions = [name for name in INSTITUTIONS if name.casefold() in unit_text.casefold()]
        tasks.append(
            {
                "id": task["id"],
                "ags": task["ags"],
                "region": q["region"],
                "population": q["population"],
                "size_class": q["size_class"],
                "family": candidate["family"],
                "source_sha256": task["candidate_source"],
                "source_url": task["original_url"],
                "source_url_family": url_family(task["original_url"]),
                "publisher_host": host(task["original_url"]),
                "unit_id": unit["unit_id"],
                "normalized_evidence_sha256": canonical(unit_text),
                "format": "PDF" if unit["page_ref"] else "HTML",
                "topics": "|".join(tags(unit_text + " " + (unit["heading"] or ""))),
                "date_mentions": "|".join(
                    sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", unit_text)))
                ),
                "temporal_role": "UNQUALIFIED_CUE_NOT_A_DATE_LABEL",
                "institution_mentions": "|".join(institutions),
                "resolved_project_id": None,
                "project_dependencies": "UNASSESSED",
                "task_complexity": "SINGLE_SOURCE_TEMPLATE_CANDIDATE",
                "source_quality_review": by_source.get(task["candidate_source"], {}).get(
                    "relevance", "UNASSESSED"
                ),
                "email_corroboration": q["email_host_corroboration"],
                "human_gold": False,
            }
        )
    components = Components([t["id"] for t in tasks])
    dimension_groups = {}
    overlap = {}
    for dimension in [
        "ags",
        "source_sha256",
        "source_url_family",
        "publisher_host",
        "normalized_evidence_sha256",
    ]:
        groups = defaultdict(list)
        for task in tasks:
            groups[task[dimension]].append(task["id"])
        dimension_groups[dimension] = groups
        overlap[dimension] = {
            "groups": len(groups),
            "shared_groups": sum(len(g) > 1 for g in groups.values()),
            "shared_tasks": sum(len(g) for g in groups.values() if len(g) > 1),
        }
        for group in groups.values():
            components.join(group)
    core_roots = {components.find(t["id"]) for t in tasks}
    institutions = defaultdict(list)
    for task in tasks:
        for name in task["institution_mentions"].split("|"):
            if name:
                institutions[name].append(task["id"])
    for group in institutions.values():
        components.join(group)
    clusters = defaultdict(list)
    for task in tasks:
        clusters[components.find(task["id"])].append(task)
    exposed_ags = {r["ags"] for r in reviews} | {"09263000", "09163000", "09471123", "09780147"}
    assignments = []
    for group in clusters.values():
        group_id = canonical(sorted(t["id"] for t in group))
        exposed = any(t["ags"] in exposed_ags for t in group)
        planned = "development" if exposed or int(group_id[:8], 16) % 10 < 3 else "evaluation"
        for task in group:
            assignments.append(
                {
                    "task_id": task["id"],
                    "ags": task["ags"],
                    "component_id": group_id,
                    "component_tasks": len(group),
                    "planned_split": planned,
                    "exposed_original_review_or_E2": exposed,
                    "effective_split": "QUARANTINE_PENDING_PROJECT_AND_REFERENCE_QUALIFICATION",
                }
            )
    write_csv(root / "benchmark_audit.csv", tasks)
    write_csv(root / "dependency_split.csv", sorted(assignments, key=lambda a: a["task_id"]))
    crossing = {}
    split_by_id = {a["task_id"]: a["planned_split"] for a in assignments}
    for dimension, groups in {**dimension_groups, "known_institution": institutions}.items():
        crossing[dimension] = sum(
            len({split_by_id[i] for i in group}) > 1 for group in groups.values()
        )
    if any(crossing.values()):
        raise ValueError("Known dependency crosses planned split")
    report = {
        "tasks": len(tasks),
        "municipalities": len({t["ags"] for t in tasks}),
        "regions": dict(Counter(t["region"] for t in tasks)),
        "size_classes": dict(Counter(t["size_class"] for t in tasks)),
        "formats": dict(Counter(t["format"] for t in tasks)),
        "families": dict(Counter(t["family"] for t in tasks)),
        "topic_tags": dict(Counter(tag for t in tasks for tag in t["topics"].split("|") if tag)),
        "complexity": dict(Counter(t["task_complexity"] for t in tasks)),
        "date_mention_present": sum(bool(t["date_mentions"]) for t in tasks),
        "date_role_qualified": 0,
        "project_resolution_qualified": 0,
        "independent_gold": 0,
        "overlap": overlap,
        "core_source_components": len(core_roots),
        "strict_known_institution_components": len(clusters),
        "known_institution_mentions": {k: len(v) for k, v in institutions.items()},
        "planned_split": dict(Counter(a["planned_split"] for a in assignments)),
        "known_dependency_split_crossings": crossing,
        "effective_evaluation_status": "QUARANTINE_ALL_PROJECT_AND_REFERENCE_QUALIFICATION_PENDING",
        "independence_claim": (
            "No full independence claim: unknown cross-document projects/institutions remain."
        ),
        "packet_sha256": sha(packet),
        "assignment_sha256": sha(root / "dependency_split.csv"),
    }
    write_json(root / "benchmark_audit_report.json", report)
    return report


def cue_recovery(conn, root, reviews):
    """Conditional preservation diagnostic, not complete parser/semantic recall."""
    rows = []
    for review in reviews:
        matches = []
        for unit in conn.execute(
            "SELECT unit_id,text FROM units WHERE sha256=?", (review["source_sha256"],)
        ):
            if review["quote"] in re.sub(r"\s+", " ", unit["text"]).strip():
                matches.append(unit["unit_id"])
        rows.append(
            {
                "ordinal": review["ordinal"],
                "source_sha256": review["source_sha256"],
                "actual_format": review["actual_format"],
                "original_locator": review["locator"],
                "cue_recovered_in_stored_units": bool(matches),
                "unit_matches": matches,
                "interpretation": "Selected original cue preservation only; not parser recall.",
            }
        )
    (root / "cue_recovery.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return {
        "selected_cues": len(rows),
        "recovered": sum(bool(r["unit_matches"]) for r in rows),
        "missing_ordinals": [r["ordinal"] for r in rows if not r["unit_matches"]],
        "metric_kind": "CONDITIONAL_SELECTED_ORIGINAL_CUE_RECOVERY_NOT_FULL_RECALL",
    }


def prepare_source_review_packet(root):
    """Blank source-quality judgments; omit agent decisions and candidate cue spans."""
    selection = json.loads((root / "selection.json").read_text())["selection"]
    packet = root / "source_review_packet"
    packet.mkdir(exist_ok=True)
    fields = [
        "source_id",
        "reviewer_identity",
        "original_seen_at",
        "publisher_authority",
        "claim_scope",
        "policy_relevance",
        "temporal_status",
        "source_evidence",
        "uncertainty",
        "exposure",
    ]
    for reviewer in ("A", "B"):
        ordered = sorted(selection, key=lambda r: canonical([reviewer, r["sha256"]]))
        with (packet / f"{reviewer}_judgments.csv").open("w") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(fields)
            writer.writerows([r["sha256"]] + [""] * (len(fields) - 1) for r in ordered)
        references = [
            {
                "source_id": r["sha256"],
                "ordinal": r["selection_ordinal"],
                "source_frame_ags": r["ags"],
                "source_frame_name": r["official_name"],
                "source_url": r["url"],
                "raw_path": r["raw_path"],
                "question": (
                    "Wer veröffentlicht diese Originalfassung? Für welches Gebiet "
                    "gelten die konkreten Aussagen? Enthält sie substantielle "
                    "Klimapolitik oder nur Links/Teaser? Welche Zeit- und "
                    "Planungs-/Umsetzungsrollen sind tatsächlich belegt?"
                ),
            }
            for r in ordered
        ]
        write_json(packet / f"{reviewer}_sources.json", references)
    with (packet / "adjudication.csv").open("w") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "source_id",
                "reviewer_A",
                "reviewer_B",
                "disagreement",
                "decision",
                "original_locator",
                "rationale",
                "adjudicator",
                "at",
            ]
        )
        writer.writerows([r["sha256"]] + [""] * 8 for r in selection)
    (packet / "README.md").write_text(
        "# Independent original-first source qualification\n\n"
        "All judgments and identities are blank. Each reviewer receives their own order, "
        "sources and form only; keep original_reviews.jsonl and agent decision files separate. "
        "Open the archived original, not only the parser text, before recording a decision. "
        "Source-frame municipality and website host are not claim scope or proof of authority. "
        "For gzip originals, the supplied command exports one exact, hash-verified original "
        "without network access or new extraction work:\n\n"
        "```sh\n.venv/bin/python -m research.bavaria.qualification.audit "
        "--db generated/bavaria/acquisition.sqlite --export-original 4\n```\n\n"
        "It writes to the bounded local review_originals directory and refuses to overwrite "
        "an existing file or cross the system reserve. Open the resulting PDF in a PDF viewer "
        "or inspect HTML with remote resources/scripts disabled. Export is optional; original "
        "CAS paths and hashes are already supplied. No source transfer is authorized by "
        "this packet. Record actual original-inspection times and exposure; no independent "
        "review has been performed. Adjudication follows real A/B judgments only.\n"
    )


def export_original(root, ordinal):
    if sha(root / "selection.json") != SELECTION_HASH:
        raise ValueError("Preinspection sample freeze changed")
    records = json.loads((root / "selection.json").read_text())["selection"]
    record = next((r for r in records if r["selection_ordinal"] == ordinal), None)
    if record is None:
        raise ValueError("Original ordinal outside the source review packet")
    source = Path(record["raw_path"])
    body = gzip.decompress(source.read_bytes()) if source.suffix == ".gz" else source.read_bytes()
    if hashlib.sha256(body).hexdigest() != record["sha256"]:
        raise ValueError("Original integrity violation")
    suffix = ".pdf" if body.startswith(b"%PDF") else ".html"
    destination = root / "review_originals" / f"{ordinal:02d}{suffix}"
    if destination.exists():
        raise ValueError("Refuse to overwrite an existing original export")
    gate(root)
    existing = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if existing + len(body) > OUTPUT_BUDGET:
        raise RuntimeError("Original export would exceed the qualification output allowance")
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(body)
    if sha(destination) != record["sha256"]:
        raise ValueError("Original export checksum mismatch")
    print(
        json.dumps(
            {
                "original_path": str(destination),
                "sha256": record["sha256"],
                "bytes": len(body),
                "source_id": record["sha256"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--root", default="generated/bavaria/continuation/qualification")
    parser.add_argument("--selection-output")
    parser.add_argument("--materialize-only", action="store_true")
    parser.add_argument("--export-original", type=int)
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    gate(root)
    database = Path(args.db).resolve(strict=True)
    initial_hash = sha(database)
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if args.export_original is not None:
        export_original(root, args.export_original)
        conn.close()
        return
    if args.selection_output:
        select_sample(conn, args.selection_output)
        conn.close()
        return
    if args.materialize_only:
        materialize_views(root)
        conn.close()
        return
    reviews, sample_report = review_originals(root)
    records, coverage_report = coverage_layer(conn, root, reviews)
    benchmark_report = benchmark_audit(conn, root, records, reviews)
    recovery_report = cue_recovery(conn, root, reviews)
    prepare_source_review_packet(root)
    conn.close()
    if sha(database) != initial_hash:
        raise ValueError("Source ledger changed during offline qualification")
    gate(root)
    report = {
        "schema": "bavaria-source-qualification-v1",
        "database_sha256": initial_hash,
        "source_database_unmodified": True,
        "new_downloads": 0,
        "model_calls": 0,
        "independent_human_labels": 0,
        "sample": sample_report,
        "coverage": coverage_report,
        "benchmark": benchmark_report,
        "cue_recovery": recovery_report,
    }
    write_json(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
