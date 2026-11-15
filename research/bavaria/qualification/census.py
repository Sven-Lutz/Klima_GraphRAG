"""Streaming SQL census of the frozen Bavaria baseline plus the completed delta.

All text categories are lexical proxies. Source-frame associations are not claim
jurisdiction, and implementation/measurement words do not verify actions/effects.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import re
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .audit import email_corroboration

RESERVE = 15 * 1024**3
OUTPUT_LIMIT = 12 * 1024**2
RULES_VERSION = "bavaria-census-lexical-proxies-v1"
TOPICS = {
    "energy": r"energie|photovolta|solar|windkraft|windenergie|erneuerbar|stromspar|stromverbrauch|treibhausgas|co2",  # noqa: E501 — frozen text/regex literal
    "heat": r"wärme|waerme|heizung|heizungs|fernwärme|nahwärme|wärmepumpe|geotherm",
    "mobility": r"mobilität|mobilitaet|radverkehr|fahrrad|radweg|stadtradeln|öpnv|carsharing|ladepunkt|ladesäul|elektromobil",  # noqa: E501 — frozen text/regex literal
    "adaptation_water_heat": r"klimaanpass|hochwasser|starkregen|überflutung|hitzeschutz|hitzebelast|hitzeaktions|dürre|trockenheit|schwammstadt",  # noqa: E501 — frozen text/regex literal
    "green_biodiversity": r"begrün|grünfläch|entsiegel|baumpflanz|biodivers|artenvielfalt|regenwasserrückhalt|naturbasiert",  # noqa: E501 — frozen text/regex literal
    "finance": r"förder|foerder|zuschuss|zuschüsse|bewilligung|investition|\d[\d.,]*\s*(?:€|Euro\b|EUR\b)",  # noqa: E501 — frozen text/regex literal
}
STATUS_CUES = {
    "planning_cue": r"\b(?:geplant|vorgesehen|beabsichtigt|soll|sollen|Entwurf|Zieljahr|Planung)\b",
    "implementation_cue": r"\b(?:umgesetzt|realisiert|installiert|errichtet|fertiggestellt|abgeschlossen|Inbetriebnahme)\b|in Betrieb genommen",  # noqa: E501 — frozen text/regex literal
    "measurement_cue": r"\b(?:gemessen|Messung|Messungen|Messwert|Messwerte|Messdaten|Monitoring)\b|Ist[- ]Wert",  # noqa: E501 — frozen text/regex literal
}
YEAR = re.compile(r"\b(?:19\d{2}|20\d{2}|2100)\b")
REGION_ORDER = [
    "Oberbayern",
    "Niederbayern",
    "Oberpfalz",
    "Oberfranken",
    "Mittelfranken",
    "Unterfranken",
    "Schwaben",
]
LABELS = {
    "energy": "Energie",
    "heat": "Wärme",
    "mobility": "Mobilität",
    "adaptation_water_heat": "Anpassung: Wasser/Hitze",
    "green_biodiversity": "Grün/Biodiversität",
    "finance": "Finanzierung",
}
STATUS_LABELS = {
    "planning_cue": "Planungswörter",
    "implementation_cue": "Umsetzungswörter",
    "measurement_cue": "Mess-/Monitoringwörter",
}


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def reserve_gate(root, planned=OUTPUT_LIMIT):
    if shutil.disk_usage(root).free < RESERVE + planned:
        raise RuntimeError("Storage reserve plus planned census output allowance unavailable")
    if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > OUTPUT_LIMIT:
        raise RuntimeError("Census output limit exceeded")


def write_csv(path, rows):
    with Path(path).open("w") as stream:
        writer = None
        for row in rows:
            if writer is None:
                writer = csv.DictWriter(stream, fieldnames=list(row), lineterminator="\n")
                writer.writeheader()
            writer.writerow(row)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def connect(path):
    p = Path(path).resolve(strict=True)
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA cache_size=-2048")
    conn.execute("BEGIN")
    return conn


def failure_class(status, error):
    error = error or ""
    if error.startswith("UNVERIFIED_REDIRECT_DOMAIN"):
        return "UNVERIFIED_REDIRECT_AUTHORITY_BOUNDARY"
    if error.startswith("BODY_LIMIT"):
        return "BODY_SIZE_BOUND_DEFERRED"
    if error.startswith("CRAWL_DELAY") or error.startswith("DEFER_RETRY_AFTER"):
        return "POLITENESS_OR_RETRY_DEFERRED"
    if error.startswith("ROBOTS_UNAVAILABLE"):
        return "ROBOTS_UNAVAILABLE_NOT_DISALLOW"
    if error.startswith("REDIRECT_LIMIT"):
        return "REDIRECT_LIMIT"
    if "SSLError" in error or "CERTIFICATE" in error:
        return "TLS_ERROR"
    if "NameResolution" in error or "resolve" in error.lower():
        return "DNS_OR_NAME_RESOLUTION"
    if "Timeout" in error or "timed out" in error:
        return "TIMEOUT"
    if status == "ACCESS_RULES_BLOCKED":
        return "ACCESS_OR_ROBOTS_BLOCKED"
    if status in ("RESOURCE_DEFERRED", "DEFERRED_STORAGE"):
        return "STORAGE_DEFERRED"
    if status == "TECHNICALLY_UNREACHABLE":
        return "HTTP_OR_CONNECTION_OTHER"
    if status == "NOT_UNAMBIGUOUSLY_ASSIGNED":
        return "AMBIGUOUS_ASSIGNMENT"
    return status


def format_probe(path):
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rb") as stream:
        prefix = stream.read(1024).lstrip()
    if prefix.startswith(b"%PDF"):
        return "PDF"
    if re.search(rb"<(?:!doctype|html|head|body)", prefix, re.I):
        return "HTML"
    if prefix.startswith(b"<"):
        return "XML_OR_OTHER_MARKUP"
    return "OTHER_OR_UNKNOWN"


def unit_stream(conn, origin):
    cursor = conn.execute(
        "SELECT sha256,ordinal,unit_id,text,heading,page_ref,kind "
        "FROM units ORDER BY sha256,ordinal,unit_id"
    )
    for row in cursor:
        yield row["sha256"], row["ordinal"] or 0, row["unit_id"], origin, row


def aggregate_text(connections, documents):
    rules = {key: re.compile(pattern, re.I) for key, pattern in {**TOPICS, **STATUS_CUES}.items()}
    unique_units = 0
    input_rows = Counter()
    active_sha = None
    seen = set()
    max_source_units = 0
    streams = [unit_stream(conn, origin) for origin, conn in connections.items()]
    for source, ordinal, _, origin, row in heapq.merge(*streams, key=lambda x: x[:3]):
        input_rows[origin] += 1
        if source != active_sha:
            max_source_units = max(max_source_units, len(seen))
            active_sha, seen = source, set()
        # Preserve context/type distinctions; equal text in another source is not deduplicated.
        identity = (ordinal, row["text"], row["heading"], row["page_ref"], row["kind"])
        if identity in seen:
            continue
        seen.add(identity)
        unique_units += 1
        document = documents[source]
        document["canonical_units"] += 1
        text = row["text"] + "\n" + (row["heading"] or "")
        for key, pattern in rules.items():
            if pattern.search(text):
                document["tag_units"][key] += 1
        years = YEAR.findall(text)
        if years:
            document["units_with_yearlike_tokens"] += 1
            document["yearlike_occurrences"] += len(years)
            document["years"].update(years)
    max_source_units = max(max_source_units, len(seen))
    return {
        "input_unit_rows": dict(input_rows),
        "input_rows_total": sum(input_rows.values()),
        "unique_source_order_text_context_units": unique_units,
        "duplicate_parse_unit_rows_removed": sum(input_rows.values()) - unique_units,
        "maximum_one_source_dedup_set_units": max_source_units,
        "dedup_identity": "source_sha256 + ordinal + exact text + heading + page_ref + kind",
        "streaming": "Two indexed SQL cursors merged by source; dedup set bounded to one source.",
    }


def group_rows(municipalities, field):
    groups = defaultdict(list)
    for row in municipalities:
        groups[row[field]].append(row)
    output = []
    for group, rows in sorted(groups.items()):
        record = {
            "group": group,
            "municipalities": len(rows),
            "frames_with_sources": sum(r["current_source_versions"] > 0 for r in rows),
            "frames_with_text_sources": sum(r["current_text_source_versions"] > 0 for r in rows),
        }
        for key in list(TOPICS) + list(STATUS_CUES):
            record[key + "_frames"] = sum(r[key + "_source_versions"] > 0 for r in rows)
        for status, count in sorted(Counter(r["current_disposition"] for r in rows).items()):
            record["disposition_" + status] = count
        output.append(record)
    return output


def census(baseline, delta, root):
    started = time.perf_counter()
    roots = {
        "baseline": Path(baseline).resolve(strict=True),
        "delta": Path(delta).resolve(strict=True),
    }
    identities = {key: digest_file(path) for key, path in roots.items()}
    connections = {key: connect(path) for key, path in roots.items()}
    registries = {
        key: {row["ags"]: dict(row) for row in conn.execute("SELECT * FROM municipalities")}
        for key, conn in connections.items()
    }
    if len(registries["baseline"]) != 2056 or not set(registries["delta"]) <= set(
        registries["baseline"]
    ):
        raise ValueError("Official municipality denominator or delta identity mismatch")
    if any(
        row["status"] in ("NOT_CHECKED", "RESOURCE_DEFERRED", "DEFERRED_STORAGE")
        for row in registries["delta"].values()
    ):
        raise ValueError("Delta is not complete")
    documents = {}
    source_sets, text_sets = {}, {}
    associations = defaultdict(set)
    origin_counts = {}
    for origin, conn in connections.items():
        own_sources = set()
        for row in conn.execute("SELECT * FROM documents"):
            source = row["sha256"]
            own_sources.add(source)
            if source not in documents:
                documents[source] = {
                    "sha256": source,
                    "origins": set(),
                    "raw_bytes": row["raw_bytes"],
                    "raw_path": row["raw_path"],
                    "canonical_units": 0,
                    "tag_units": Counter(),
                    "years": set(),
                    "units_with_yearlike_tokens": 0,
                    "yearlike_occurrences": 0,
                }
            elif documents[source]["raw_bytes"] != row["raw_bytes"]:
                raise ValueError("Same original SHA has inconsistent byte size")
            documents[source]["origins"].add(origin)
        source_sets[origin] = own_sources
        text_sets[origin] = {r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM units")}
        own_assoc = defaultdict(set)
        for row in conn.execute("SELECT * FROM associations"):
            associations[row["ags"]].add((row["sha256"], row["url"]))
            own_assoc[row["ags"]].add(row["sha256"])
        origin_counts[origin] = own_assoc
    if len(documents) != len(source_sets["baseline"] | source_sets["delta"]):
        raise ValueError("Original union integrity failure")
    text_report = aggregate_text(connections, documents)
    format_counts = Counter()
    source_assigned_ags = defaultdict(set)
    for ags, group in associations.items():
        for source, _ in group:
            source_assigned_ags[source].add(ags)
    for doc in documents.values():
        doc["format"] = format_probe(doc["raw_path"])
        format_counts[doc["format"]] += 1
    municipality_rows = []
    failures = Counter()
    for ags, baseline_row in sorted(registries["baseline"].items()):
        official = json.loads(baseline_row["record"])
        delta_row = registries["delta"].get(ags)
        current = delta_row or baseline_row
        if delta_row and json.loads(delta_row["record"])["ags"] != ags:
            raise ValueError("Delta AGS mismatch")
        sources = {source for source, _ in associations[ags]}
        failure = failure_class(current["status"], current["error"])
        failures[failure] += 1
        row = {
            "ags": ags,
            "official_name": official["official_name"],
            "region": official["region"],
            "population": official["population"],
            "population_date": official["population_date"],
            "size_class": "small_lt5000"
            if official["population"] < 5000
            else "medium_5000_19999"
            if official["population"] < 20000
            else "large_ge20000",
            "baseline_disposition": baseline_row["status"],
            "delta_disposition": delta_row["status"] if delta_row else "",
            "current_disposition": current["status"],
            "current_checked_at": current["checked_at"],
            "current_error": current["error"] or "",
            "failure_class": failure,
            "baseline_source_versions": len(origin_counts["baseline"].get(ags, ())),
            "delta_source_versions": len(origin_counts["delta"].get(ags, ())),
            "current_source_versions": len(sources),
            "baseline_text_source_versions": len(
                set(origin_counts["baseline"].get(ags, ())) & text_sets["baseline"]
            ),
            "delta_text_source_versions": len(
                set(origin_counts["delta"].get(ags, ())) & text_sets["delta"]
            ),
            "current_text_source_versions": sum(
                documents[s]["canonical_units"] > 0 for s in sources
            ),
            **email_corroboration(official),
            "claim_scope_status": "NOT_INFERRED_FROM_FRAME",
            "policy_coverage_qualified": "NOT_ESTABLISHED",
        }
        for key in list(TOPICS) + list(STATUS_CUES):
            row[key + "_source_versions"] = sum(documents[s]["tag_units"][key] > 0 for s in sources)
        row["source_versions_with_yearlike_tokens"] = sum(
            bool(documents[s]["years"]) for s in sources
        )
        municipality_rows.append(row)
    source_rows = []
    for source, doc in sorted(documents.items()):
        row = {
            "sha256": source,
            "origins": "|".join(sorted(doc["origins"])),
            "format": doc["format"],
            "raw_bytes": doc["raw_bytes"],
            "source_frame_count": len(source_assigned_ags[source]),
            "canonical_parse_units": doc["canonical_units"],
            "units_with_yearlike_tokens": doc["units_with_yearlike_tokens"],
            "yearlike_occurrences": doc["yearlike_occurrences"],
            "yearlike_values": "|".join(sorted(doc["years"])),
        }
        for key in list(TOPICS) + list(STATUS_CUES):
            row[key + "_units"] = doc["tag_units"][key]
            row[key + "_present"] = bool(doc["tag_units"][key])
        source_rows.append(row)
    region_rows = group_rows(municipality_rows, "region")
    size_rows = group_rows(municipality_rows, "size_class")
    # CSV rows require a common column union because status classes vary by stratum.
    for rows in (region_rows, size_rows):
        keys = list(dict.fromkeys(key for row in rows for key in row))
        for i, row in enumerate(rows):
            rows[i] = {key: row.get(key, 0) for key in keys}
    years = []
    for year in sorted({year for doc in documents.values() for year in doc["years"]}):
        matching_sources = {source for source, doc in documents.items() if year in doc["years"]}
        frames = set().union(*(source_assigned_ags[s] for s in matching_sources))
        years.append(
            {
                "yearlike_token": year,
                "source_versions": len(matching_sources),
                "source_frames": len(frames),
                "date_role": "UNQUALIFIED_TOKEN_NOT_PUBLICATION_DATE",
            }
        )
    write_csv(root / "municipality_census.csv", municipality_rows)
    write_csv(root / "source_proxy_tags.csv", source_rows)
    write_csv(root / "region_proxy_coverage.csv", region_rows)
    write_csv(root / "size_proxy_coverage.csv", size_rows)
    write_csv(root / "yearlike_proxy_counts.csv", years)
    write_csv(
        root / "failure_types.csv",
        (
            {"failure_class": key, "municipalities": value}
            for key, value in sorted(failures.items())
        ),
    )
    report = {
        "schema": "bavaria-corpus-census-v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "rules_version": RULES_VERSION,
        "database_sha256": identities,
        "databases": {key: str(path) for key, path in roots.items()},
        "municipalities": len(municipality_rows),
        "delta_municipalities": len(registries["delta"]),
        "baseline_dispositions": dict(
            Counter(r["baseline_disposition"] for r in municipality_rows)
        ),
        "delta_dispositions": dict(
            Counter(r["delta_disposition"] for r in municipality_rows if r["delta_disposition"])
        ),
        "current_dispositions": dict(Counter(r["current_disposition"] for r in municipality_rows)),
        "failure_types": dict(failures),
        "baseline_versions": len(source_sets["baseline"]),
        "delta_versions": len(source_sets["delta"]),
        "overlapping_source_hashes": len(source_sets["baseline"] & source_sets["delta"]),
        "unique_source_versions": len(documents),
        "unique_original_bytes": sum(doc["raw_bytes"] for doc in documents.values()),
        "source_formats": dict(format_counts),
        "text_census": text_report,
        "versions_with_text": sum(doc["canonical_units"] > 0 for doc in documents.values()),
        "frames_with_sources": sum(row["current_source_versions"] > 0 for row in municipality_rows),
        "frames_with_text": sum(
            row["current_text_source_versions"] > 0 for row in municipality_rows
        ),
        "source_tag_presence": {
            key: sum(doc["tag_units"][key] > 0 for doc in documents.values())
            for key in list(TOPICS) + list(STATUS_CUES)
        },
        "frame_tag_presence": {
            key: sum(r[key + "_source_versions"] > 0 for r in municipality_rows)
            for key in list(TOPICS) + list(STATUS_CUES)
        },
        "email_host_corroboration": dict(
            Counter(r["email_host_corroboration"] for r in municipality_rows)
        ),
        "independent_authority_approvals": 0,
        "verified_policy_measure_counts": None,
        "regions": region_rows,
        "sizes": size_rows,
        "rules": {"topics": TOPICS, "status_cues": STATUS_CUES, "yearlike": YEAR.pattern},
        "limitations": [
            "All tags are text-presence proxies, not verified policy/measure/effect counts.",
            "One source may belong to several acquisition frames; this is not claim scope.",
            "Negation, modality, legal examples, navigation and boilerplate may trigger cues.",
            "Four-digit yearlike tokens may be targets, copyright, history or parcel numbers.",
            "Missing text/no cue is unknown, not evidence of absent policy.",
            "Financial words are not award/disbursement evidence; no monetary totals computed.",
        ],
        "wall_seconds": time.perf_counter() - started,
        "network_calls": 0,
        "model_calls": 0,
    }
    for conn in connections.values():
        conn.close()
    if {key: digest_file(path) for key, path in roots.items()} != identities:
        raise ValueError("Input database changed during census")
    report["input_databases_unmodified"] = True
    write_json(root / "report.json", report)
    return report


def render_figures(root, report):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "savefig.facecolor": "white",
        }
    )
    regions = {row["group"]: row for row in report["regions"]}
    order = [name for name in REGION_ORDER if name in regions]
    statuses = [
        ("RELEVANT_CANDIDATES_FOUND", "Relevante Kandidaten", "#2166ac"),
        ("CHECKED_NO_RELEVANT_FOUND", "Kein Treffer im begrenzten Lauf", "#92c5de"),
        ("TECHNICALLY_UNREACHABLE", "Technik-/Allowlist-Grenze", "#f4a582"),
        ("ACCESS_RULES_BLOCKED", "Zugriff blockiert", "#b2182b"),
        ("ROBOTS_UNAVAILABLE", "robots.txt nicht verfügbar", "#9970ab"),
        ("NOT_UNAMBIGUOUSLY_ASSIGNED", "Zuordnung ungeklärt", "#999999"),
    ]
    fig, ax = plt.subplots(figsize=(10.7, 6.3))
    left = np.zeros(len(order))
    for status, label, color in statuses:
        values = np.array([regions[r].get("disposition_" + status, 0) for r in order])
        denominators = np.array([regions[r]["municipalities"] for r in order])
        fraction = values / denominators
        ax.barh(order, fraction, left=left, label=label, color=color)
        for i, value in enumerate(values):
            if fraction[i] >= 0.045:
                ax.text(
                    left[i] + fraction[i] / 2,
                    i,
                    str(value),
                    ha="center",
                    va="center",
                    color="white" if status == "RELEVANT_CANDIDATES_FOUND" else "#202020",
                    fontsize=9,
                )
        left += fraction
    ax.set_yticks(range(len(order)), [f"{r} (n={regions[r]['municipalities']})" for r in order])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    ax.set_xlabel("Anteil am amtlichen Gemeinderahmen des Regierungsbezirks")
    ax.set_title(
        "Crawldispositionen nach Basis + Delta: 2.056 Gemeinden", loc="left", fontweight="bold"
    )
    ax.legend(ncol=2, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), frameon=False)
    fig.text(
        0.01,
        0.015,
        "Begrenzte Abrufversuche; keine Aussage über vollständige Quellen- oder Klimapolitikabdeckung.",  # noqa: E501 — frozen text/regex literal
        fontsize=9,
    )
    fig.subplots_adjust(left=0.25, right=0.98, top=0.90, bottom=0.27)
    for ext in ("png", "svg"):
        fig.savefig(root / f"figure1_dispositions.{ext}", dpi=170)
    plt.close(fig)

    keys = list(TOPICS)
    matrix = np.array(
        [
            [regions[r][key + "_frames"] / regions[r]["municipalities"] for key in keys]
            for r in order
        ]
    )
    fig, ax = plt.subplots(figsize=(11.3, 6.3))
    image = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_yticks(range(len(order)), [f"{r} (n={regions[r]['municipalities']})" for r in order])
    ax.set_xticks(range(len(keys)), [LABELS[key] for key in keys], rotation=24, ha="right")
    for y, region in enumerate(order):
        n = regions[region]["municipalities"]
        for x, key in enumerate(keys):
            value = regions[region][key + "_frames"]
            ax.text(
                x,
                y,
                f"{value}/{n}\n{value / n:.0%}",
                ha="center",
                va="center",
                color="white" if matrix[y, x] > 0.52 else "#202020",
                fontsize=8,
            )
    ax.set_title(
        "Themen-Cues in gespeicherten Textquellen je Gemeinde-Erfassungsrahmen",
        loc="left",
        fontweight="bold",
        pad=15,
    )
    bar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.025)
    bar.set_label("Anteil der Gemeinde-Erfassungsrahmen")
    fig.text(
        0.01,
        0.025,
        "Lexikalische Proxies, Mehrfach-Tags möglich. Keine Claim-Geltung, Maßnahmenzahlen oder Qualitätsurteile.\n"  # noqa: E501 — frozen text/regex literal
        "Kein Text/kein Cue bedeutet nicht: keine Klimapolitik. Nenner: alle Gemeinden je Regierungsbezirk.",  # noqa: E501 — frozen text/regex literal
        fontsize=9,
    )
    fig.subplots_adjust(left=0.25, right=0.91, top=0.90, bottom=0.27)
    for ext in ("png", "svg"):
        fig.savefig(root / f"figure2_topic_proxies.{ext}", dpi=170)
    plt.close(fig)

    sizes = {row["group"]: row for row in report["sizes"]}
    groups = ["small_lt5000", "medium_5000_19999", "large_ge20000"]
    size_labels = ["< 5.000", "5.000–19.999", "≥ 20.000"]
    fig, axes = plt.subplots(1, 2, figsize=(11.3, 5.7), gridspec_kw={"width_ratios": [1.05, 1]})
    values = [sizes[g]["frames_with_text_sources"] / sizes[g]["municipalities"] for g in groups]
    axes[0].bar(size_labels, values, color="#2166ac", width=0.6)
    for x, group in enumerate(groups):
        row = sizes[group]
        axes[0].text(
            x,
            values[x] + 0.025,
            f"{row['frames_with_text_sources']}/{row['municipalities']}\n{values[x]:.1%}",
            ha="center",
            fontsize=9,
        )
    axes[0].set_ylim(0, 1.17)
    axes[0].set_ylabel("Gemeinde-Erfassungsrahmen mit mindestens einer Textquelle")
    axes[0].set_yticks([0, 0.25, 0.5, 0.75, 1], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    axes[0].set_xlabel("Einwohnerklasse (amtlicher Stand 31.12.2024)")
    axes[0].set_title(
        "A  Textverfügbarkeit nach Gemeindegröße", loc="left", fontweight="bold", fontsize=11
    )
    keys = list(STATUS_CUES)
    counts = [report["source_tag_presence"][key] for key in keys]
    n = report["unique_source_versions"]
    axes[1].barh(
        [STATUS_LABELS[key] for key in keys], [count / n for count in counts], color="#67a9cf"
    )
    for y, count in enumerate(counts):
        axes[1].text(count / n + 0.015, y, f"{count}/{n}", va="center", fontsize=9)
    axes[1].set_xlim(0, 1.12)
    axes[1].set_xticks([0, 0.25, 0.5, 0.75, 1], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    axes[1].set_xlabel("Anteil einzigartiger Originalversionen")
    axes[1].set_title(
        "B  Statuswörter in extrahiertem Text", loc="left", fontweight="bold", fontsize=11
    )
    fig.text(
        0.01,
        0.025,
        "A: Textverfügbarkeit ist keine fachliche Quellenabdeckung. B: Wortmuster, keine verifizierten Zustände/Wirkungen;\n"  # noqa: E501 — frozen text/regex literal
        "Planung, Negation und Boilerplate können Cues auslösen. Keine Förder- oder Maßnahmensummen.",  # noqa: E501 — frozen text/regex literal
        fontsize=9,
    )
    fig.subplots_adjust(left=0.075, right=0.96, top=0.88, bottom=0.22, wspace=0.48)
    for ext in ("png", "svg"):
        fig.savefig(root / f"figure3_size_and_status_proxies.{ext}", dpi=170)
    plt.close(fig)
    return {
        "matplotlib_version": matplotlib.__version__,
        "backend": matplotlib.get_backend(),
        "figures": [
            "figure1_dispositions",
            "figure2_topic_proxies",
            "figure3_size_and_status_proxies",
        ],
        "formats": ["png", "svg"],
        "scientific_status": "DESCRIPTIVE_TEXT_AND_ACQUISITION_PROXIES_NOT_POLICY_ESTIMATES",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--delta", required=True)
    parser.add_argument("--output", default="generated/bavaria/continuation/census")
    parser.add_argument("--figures-only", action="store_true")
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    reserve_gate(root)
    report = (
        json.loads((root / "report.json").read_text())
        if args.figures_only
        else census(args.baseline, args.delta, root)
    )
    if not args.no_figures:
        try:
            figures = render_figures(root, report)
        except ModuleNotFoundError as exc:
            figures = {"status": "DEPENDENCY_MISSING", "module": exc.name}
        write_json(root / "figure_receipt.json", figures)
    reserve_gate(root, planned=0)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
