# ruff: noqa: E501
"""Read-only original screening and deterministic prospective selection (not Gold)."""

import argparse
import gzip
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from research.bavaria.federation import source_connection

SEED = "bavaria-human-400-v1"
RESERVE = 15 * 1024**3
CLIMATE = re.compile(
    r"klima|energiewende|wärmeplan|wärmenetz|photovoltaik|hochwasser|hitzeschutz|entsiegel|treibhaus|co2|erneuerbar|radverkehr",
    re.I,
)
RELATION = re.compile(
    r"gefördert|förderung|finanziert|zuständig|verantwortlich|zusammenarbeit|projektträger|betreiber|beschlossen|umgesetzt|fertiggestellt",
    re.I,
)
FIELD = re.compile(
    r"\b(?:19|20)\d{2}\b|\d[\d.,]*\s*(?:Euro|EUR|%|kWh|MW|Tonnen)|geplant|beschlossen|umgesetzt",
    re.I,
)
NOISE = re.compile(
    r"cookie|javascript|datenschutzerklärung|alle akzeptieren|suche starten|passwort", re.I
)
FAMILIES = {"source": 100, "fields": 110, "relation": 100, "retrieval": 90}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def rank(value):
    return digest([SEED, value])


def gate(path, allowance=32 * 1024**2):
    if shutil.disk_usage(path).free < RESERVE + allowance:
        raise RuntimeError("15-GiB reserve: inventory checkpoint retained; no further hydration")


def population_class(n):
    if not isinstance(n, (int, float)):
        return "UNKNOWN"
    return (
        "<5000"
        if n < 5000
        else "5000–19999"
        if n < 20000
        else "20000–99999"
        if n < 100000
        else ">=100000"
    )


def normalized_url(url):
    u = urlsplit(url)
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), "", ""))


def select(inventory, frame, deduplicate_focus=False):
    """Deterministic, source-unique, first diversify municipalities then fill deficits."""
    eligible = [r for r in inventory if r["status"] == "CANDIDATE" and not r["historical_exposure"]]
    region_frame = Counter(r["region"] for r in frame.values())
    selected, hashes, municipalities = [], set(), set()
    focus_keys = set()
    for family, target in FAMILIES.items():
        pool = sorted(
            [r for r in eligible if family in r["families"]],
            key=lambda r: rank([family, r["sha256"]]),
        )
        quotas = {k: int(target * v / sum(region_frame.values())) for k, v in region_frame.items()}
        for k in sorted(
            quotas,
            key=lambda k: (-(target * region_frame[k] / sum(region_frame.values()) - quotas[k]), k),
        )[: target - sum(quotas.values())]:
            quotas[k] += 1
        counts: Counter[str] = Counter()
        chosen: list[dict] = []
        for diversify, respect_quota in [
            (True, True),
            (False, True),
            (True, False),
            (False, False),
        ]:
            for row in pool:
                if len(chosen) == target:
                    break
                if row["sha256"] in hashes or (diversify and row["ags"] in municipalities):
                    continue
                if respect_quota and counts[row["region"]] >= quotas[row["region"]]:
                    continue
                focus = (row.get("family_focus", {}).get(family) or row.get("focus") or {}).get(
                    "text", row["sha256"]
                )
                focus_key = (family, " ".join(focus.casefold().split()))
                if deduplicate_focus and focus_key in focus_keys:
                    continue
                focus_keys.add(focus_key)
                hashes.add(row["sha256"])
                municipalities.add(row["ags"])
                counts[row["region"]] += 1
                chosen.append({**row, "primary_purpose": family})
        selected.extend(chosen)
    return selected


def build(manifest, output, historic, metadata_only=False, deduplicate_focus=False):
    output = Path(output)
    if (output / "REPORT.json").exists():
        raise ValueError("Refuse overwrite of completed study")
    gate(output.parent)
    output.mkdir(exist_ok=True)
    binding = {
        "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        "historical_sha256": hashlib.sha256(Path(historic).read_bytes()).hexdigest(),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    checkpoint = output / "CHECKPOINT.json"
    if checkpoint.exists() and json.loads(checkpoint.read_text()) != binding:
        previous = json.loads(checkpoint.read_text())
        if not metadata_only or any(
            previous[k] != binding[k] for k in ["manifest_sha256", "historical_sha256"]
        ):
            raise ValueError("Checkpoint inputs/implementation changed")
        (output / "METADATA_CONTINUATION.json").write_text(
            json.dumps(
                {
                    "previous": previous,
                    "current": binding,
                    "mode": "NO_ADDITIONAL_ORIGINAL_HYDRATION",
                    "reason": "Storage gate stopped original verification",
                },
                indent=2,
            )
        )
    else:
        checkpoint.write_text(json.dumps(binding, indent=2))
    m = json.loads(Path(manifest).read_text())
    paths = [m["artifacts"]["ledger"]["path"], *m["source_ledger_order"]]
    c = source_connection(paths[0], paths[1:])
    frame = {r["ags"]: json.loads(r["record"]) for r in c.execute("SELECT * FROM municipalities")}
    prior = {r["sha256"] for r in json.loads(Path(historic).read_text())}
    associations = defaultdict(list)
    for row in c.execute("SELECT * FROM associations"):
        associations[row["sha256"]].append(dict(row))
    focuses = defaultdict(list)
    unit_counts: Counter[str] = Counter()
    for row in c.execute("SELECT * FROM units"):
        unit_counts[row["sha256"]] += 1
        text = row["text"].strip()
        if 100 <= len(text) <= 12000 and CLIMATE.search(text) and not NOISE.search(text):
            candidate = dict(row)
            focuses[row["sha256"]].append(candidate)
    inventory_path = output / "inventory.jsonl"
    rows = (
        [json.loads(line) for line in inventory_path.read_text().splitlines()]
        if inventory_path.exists()
        else []
    )
    completed = {r["sha256"] for r in rows}
    with inventory_path.open("a") as stream:
        for doc in c.execute("SELECT * FROM documents ORDER BY sha256"):
            h, raw = doc["sha256"], Path(doc["raw_path"])
            if h in completed:
                continue
            refs = sorted(associations[h], key=lambda r: (r["ags"], r["url"]))
            ref = refs[0] if refs else {}
            meta = frame.get(ref.get("ags"), {})
            choices = sorted(focuses[h], key=lambda u: rank(u["unit_id"]))
            focus = choices[0] if choices else None
            status = "NO_QUALIFIED_TEXT_CANDIDATE"
            actual_format, verified = "UNKNOWN", False
            if choices and metadata_only:
                status = "DEFERRED_STORAGE_ORIGINAL_NOT_VERIFIED"
            elif choices:
                gate(output, doc["raw_bytes"] + doc["compressed_bytes"] + 32 * 1024**2)
                try:
                    body = (
                        gzip.decompress(raw.read_bytes())
                        if raw.suffix == ".gz"
                        else raw.read_bytes()
                    )
                    verified = hashlib.sha256(body).hexdigest() == h
                    status = "CANDIDATE" if verified else "HASH_MISMATCH"
                    actual_format = "PDF" if body.startswith(b"%PDF") else "HTML"
                except (OSError, EOFError, gzip.BadGzipFile) as e:
                    status = "ORIGINAL_UNAVAILABLE:" + type(e).__name__
            families = ["source", "retrieval"] if choices else []
            for family, pattern in [("fields", FIELD), ("relation", RELATION)]:
                found = next((u for u in choices if pattern.search(u["text"])), None)
                if found:
                    families.append(family)
            record = {
                "sha256": h,
                "raw_path": str(raw),
                "raw_bytes": doc["raw_bytes"],
                "status": status,
                "original_sha256_verified": verified,
                "format": actual_format,
                "ags": ref.get("ags"),
                "all_frame_ags": sorted({r["ags"] for r in refs}),
                "name": meta.get("name"),
                "region": meta.get("region", "UNKNOWN"),
                "district": meta.get("district"),
                "population": meta.get("population"),
                "population_date": meta.get("population_date"),
                "population_class": population_class(meta.get("population")),
                "municipality_type": meta.get("municipality_type", "UNKNOWN"),
                "urban_rural": "UNKNOWN_NOT_IN_OFFICIAL_FRAME",
                "url": ref.get("url", ""),
                "source_group": normalized_url(ref.get("url", "")),
                "host_proxy": urlsplit(ref.get("url", "")).netloc.lower(),
                "association_authority": ref.get("authority", "UNKNOWN"),
                "publisher": "REVIEW_REQUIRED",
                "authority": "UNQUALIFIED",
                "claim_scope": "UNKNOWN_DO_NOT_INHERIT_FRAME",
                "document_type": "REVIEW_REQUIRED",
                "publication_date": "UNKNOWN",
                "text_units": unit_counts[h],
                "text_quality": "LEXICAL_SCREEN_ONLY_NOT_FIDELITY_REVIEW",
                "families": families,
                "focus": focus,
                "family_focus": {
                    f: next((u for u in choices if p.search(u["text"])), None)
                    for f, p in [("fields", FIELD), ("relation", RELATION)]
                },
                "historical_exposure": h in prior,
                "other_development_exposure": "UNASSESSED",
                "project_identity": "UNRESOLVED",
                "institution_identity": "UNRESOLVED",
                "rights": "UNRESOLVED",
                "external_transfer": False,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            rows.append(record)
    c.close()
    exposure_sources = [
        Path("research/bavaria/qualification/results/original_reviews.jsonl"),
        Path("generated/bavaria/continuation/qualification/selection.json"),
    ]
    extra_exposure = set()
    for exposure in exposure_sources:
        if exposure.exists():
            extra_exposure.update(
                re.findall(
                    r'"(?:source_sha256|sha256)"\s*:\s*"([a-f0-9]{64})"', exposure.read_text()
                )
            )
    (output / "EXPOSURE_AUDIT.json").write_text(
        json.dumps(
            {
                "additional_exposed_sources": sorted(extra_exposure),
                "inputs": [str(p) for p in exposure_sources],
                "other_exposure_status": "UNASSESSED_NOT_CLEARED",
            },
            indent=2,
        )
    )
    for row in rows:
        if row["sha256"] in extra_exposure:
            row["other_development_exposure"] = "KNOWN_DEVELOPMENT_EXPOSED"
    selected = select(rows, frame, deduplicate_focus)
    cases = []
    prompts = {
        "source": "Prüfen Sie Herausgeber, kommunale Aussagegeltung, Klimarelevanz und Originaltreue dieser Passage.",
        "fields": "Welche Akteure, Maßnahme, Zahlen und zeitliche Aussageart sind in dieser Passage ausdrücklich belegt? Lassen Sie fehlende Angaben UNKNOWN.",
        "relation": "Welche Zuständigkeits-, Förder- oder zeitliche Maßnahmenrelation ist in dieser Passage tatsächlich belegt? Nicht belegte Relationen bleiben UNKNOWN.",
        "retrieval": "Welche Kandidaten belegen die Klimapolitikaussage der markierten Originalstelle, und fehlt notwendiger Kontext?",
    }
    for r in selected:
        family = r["primary_purpose"]
        focus = r["family_focus"].get(family) or r["focus"]
        tid = "bavaria400:" + digest([r["sha256"], family, focus["unit_id"]])[:24]
        cases.append(
            {
                "task_id": tid,
                "primary_purpose": family,
                "question": f"{r['name']} · {prompts[family]}",
                "allowed_original_sha256": [r["sha256"]],
                "focal_source_sha256": r["sha256"],
                "focus": focus,
                "required_stages": ["fields" if family == "relation" else family],
                "pool": [
                    {
                        "id": digest([tid, focus["unit_id"]])[:20],
                        "evidence_id": focus["unit_id"],
                        "source_sha256": r["sha256"],
                        "text": focus["text"],
                        "page": focus["page_ref"],
                        "heading": focus["heading"],
                    }
                ]
                if family == "retrieval"
                else [],
                "pool_status": "FOCAL_UNIT_ONLY_NOT_MULTI_SYSTEM_POOL"
                if family == "retrieval"
                else "NOT_APPLICABLE",
                "effective_split": "PROSPECTIVE_CANDIDATE_NOT_CONFIRMATORY",
                "ags": r["ags"],
                "region": r["region"],
                "population_class": r["population_class"],
                "components": [
                    "sha:" + r["sha256"],
                    "url:" + digest(r["source_group"]),
                    "span:" + digest(focus["text"]),
                ],
                "dependency_clearance": False,
                "reference_status": "NO_HUMAN_LABELS",
                "source_record": {k: v for k, v in r.items() if k not in ("focus", "family_focus")},
            }
        )
    for name, data in [
        ("cases.json", cases),
        (
            "rights.json",
            [
                dict(
                    sha256=r["sha256"],
                    url=r["url"],
                    status="UNRESOLVED",
                    owner=None,
                    decision_date=None,
                    external_transfer=False,
                )
                for r in selected
            ],
        ),
    ]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    batches = [
        {
            "batch_id": f"B{i // 40 + 1:02}",
            "task_ids": [t["task_id"] for t in cases[i : i + 40]],
            "reviewers": ["A", "B"],
            "release": "BLOCKED_HUMAN_CALIBRATION_RIGHTS_DEPENDENCY_FREEZE",
        }
        for i in range(0, len(cases), 40)
    ]
    (output / "batches.json").write_text(json.dumps(batches, indent=2))
    report = {
        "schema": "bavaria-human-study-candidates-v1",
        "seed": SEED,
        "deduplicate_same_purpose_focus": deduplicate_focus,
        "snapshot_id": m["snapshot_id"],
        "frame_municipalities": len(frame),
        "originals": len(rows),
        "inventory_status": dict(Counter(r["status"] for r in rows)),
        "eligible_unexposed_originals": sum(
            r["status"] == "CANDIDATE" and not r["historical_exposure"] for r in rows
        ),
        "cases": len(cases),
        "municipalities": len({t["ags"] for t in cases}),
        "selected_originals": len(selected),
        "families": dict(Counter(t["primary_purpose"] for t in cases)),
        "regions": dict(Counter(t["region"] for t in cases)),
        "population_classes": dict(Counter(t["population_class"] for t in cases)),
        "formats": dict(Counter(r["format"] for r in selected)),
        "known_source_groups": len({r["source_group"] for r in selected}),
        "host_proxy_groups": len({r["host_proxy"] for r in selected}),
        "independent_project_components": "NOT_ESTABLISHED",
        "historical_originals_excluded": len(prior),
        "gold_released": False,
        "human_calibration": "MISSING",
        "external_distribution": False,
        "limitations": [
            "Automatic candidate screening, not source qualification",
            "Other historical exposure and project identity require clearance",
            "No multi-system retrieval pool or generated-answer tasks created",
            "No population inference",
        ],
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir()
            if p.is_file()
        },
    }
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--historical-sources", required=True)
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Resume metadata inventory without further original hydration",
    )
    p.add_argument("--deduplicate-focus", action="store_true")
    a = p.parse_args()
    build(a.manifest, a.output, a.historical_sources, a.metadata_only, a.deduplicate_focus)
