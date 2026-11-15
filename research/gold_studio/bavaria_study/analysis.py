# ruff: noqa: E501
"""Dependency-aware study summaries; never promote technical judgments to Gold."""

import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def dependency_graph(cases, host_proxy=False):
    parent = {t["task_id"]: t["task_id"] for t in cases}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen: dict[str, str] = {}
    edges = []
    for t in cases:
        links = list(t["components"])
        if host_proxy:
            links.append("host:" + t["source_record"]["host_proxy"])
        for key in links:
            if key in seen:
                a, b = find(t["task_id"]), find(seen[key])
                parent[max(a, b)] = min(a, b)
                edges.append({"from": t["task_id"], "to": seen[key], "basis": key})
            else:
                seen[key] = t["task_id"]
    groups = defaultdict(list)
    for tid in parent:
        groups[find(tid)].append(tid)
    return {
        "components": dict(groups),
        "edges": edges,
        "component_count": len(groups),
        "qualification": "KNOWN_LINKS_ONLY_PROJECT_IDENTITY_UNRESOLVED",
        "host_is_only_proxy": host_proxy,
    }


def paired_cluster_interval(rows, cluster_key="component", repeats=2000, seed=400):
    """Paired task-weighted mean difference; cluster resample retains whole groups.

    Caller supplies actual per-task metric values for the SAME reference population.
    This function does not construct labels or establish independence.
    """
    groups = defaultdict(list)
    missing = 0
    for row in rows:
        if row.get("a") is None or row.get("b") is None:
            missing += 1
            continue
        if not row.get(cluster_key):
            raise ValueError("Explicit dependency cluster required")
        groups[row[cluster_key]].append(float(row["a"]) - float(row["b"]))
    if len(groups) < 2:
        return {
            "status": "INSUFFICIENT_CLUSTERS",
            "clusters": len(groups),
            "missing_pairs": missing,
            "interval": None,
        }
    rng = random.Random(seed)
    values = list(groups.values())
    boot = []
    for _ in range(repeats):
        sample = [x for group in rng.choices(values, k=len(values)) for x in group]
        boot.append(statistics.mean(sample))
    boot.sort()
    return {
        "status": "EXPLORATORY_UNLESS_REFERENCE_AND_DEPENDENCIES_VALIDATED",
        "mean_difference": statistics.mean(x for g in values for x in g),
        "interval": [boot[int(0.025 * (repeats - 1))], boot[int(0.975 * (repeats - 1))]],
        "clusters": len(groups),
        "paired_tasks": sum(map(len, values)),
        "missing_pairs": missing,
        "seed": seed,
        "repeats": repeats,
        "estimand": "task-weighted paired mean; whole-cluster percentile bootstrap",
    }


def workload(store):
    if store.simulation or not all((store.profile(r) or {}).get("human") for r in ("A", "B")):
        return {
            "status": "PENDING_REAL_HUMAN_CALIBRATION",
            "hours": None,
            "reason": "Synthetic timing is excluded; case-type reading and extra atomic judgments must be measured.",
        }
    durations = defaultdict(list)
    calibration = set(store.bundle["selection"]["calibration"])
    for event in store.events(kind="lock"):
        if event["task"] in calibration and event["role"] in ("A", "B"):
            durations[event["stage"]].append(event["payload"].get("seconds", 0))
    return {
        "status": "MEASURED_FORM_TIMES_NOT_AUTOMATIC_MAIN_WORKLOAD",
        "per_stage": {
            k: {"n": len(v), "median_seconds": statistics.median(v), "total_seconds": sum(v)}
            for k, v in durations.items()
        },
        "projection_gate": "Need case-type calibration incl. original intake, atomic candidate counts, breaks and adjudication; no arbitrary target times.",
    }


def frozen_summary(path, bundle):
    data = json.loads(Path(path).read_text())
    expected = data.pop("content_sha256")
    from ..store import digest

    if digest(data) != expected or data["report"]["simulation"]:
        raise ValueError("Untrusted or simulated review snapshot")
    if data.get("release") != "OWNER_ATTESTED_HUMAN_REVIEW_VERSION_NOT_AUTOMATIC_RESEARCH_GOLD":
        raise ValueError("Owner-reviewed version required")
    cases = {t["task_id"]: t for t in bundle["tasks"]}
    refs = data["adjudication"]
    return {
        "status": "OWNER_REVIEWED_REFERENCE_SUMMARY_NOT_AUTOMATIC_RESEARCH_GOLD",
        "snapshot_sha256": expected,
        "agreement": data["report"]["metrics"],
        "cases": len(refs),
        "unresolved": sum(r["final_reference"] is None for r in refs),
        "primary_purposes": dict(
            Counter(cases[r["task_id"]].get("primary_purpose", "calibration") for r in refs)
        ),
        "references": refs,
        "dependency_audit": data["dependency_audit"],
        "method_metrics": None,
        "reason": "Supply frozen method outputs keyed by exact task/evidence IDs; focal-only relevance is not a multi-system ranking benchmark.",
    }


def write_report(folder):
    folder = Path(folder)
    cases = json.loads((folder / "cases.json").read_text())
    graph = dependency_graph(cases)
    proxy = dependency_graph(cases, True)
    (folder / "DEPENDENCIES.json").write_text(
        json.dumps(
            {"source_url_span": graph, "host_proxy_sensitivity": proxy},
            ensure_ascii=False,
            indent=2,
        )
    )
    cross = Counter(
        (t["region"], t["primary_purpose"], t["population_class"], t["source_record"]["format"])
        for t in cases
    )
    lines = [
        "# Sampling und Coverage — tatsächlich erzeugte Kandidaten",
        "",
        "Status: automatische Quellenkandidaten; keine unabhängige fachliche Qualifikation, keine Human-Gold-Labels.",
        f"{len(cases)} Fälle; {len({t['ags'] for t in cases})} Gemeinden; {len({t['source_record']['sha256'] for t in cases})} Originalversionen.",
        f"Bekannte Source-/URL-/Span-Komponenten: {graph['component_count']}; konservative Host-Proxys: {proxy['component_count']}.",
        "Diese Zahlen sind keine nachgewiesene Anzahl unabhängiger Projekte. Projekt-/Institutionsidentität und weitere historische Exposition sind noch zu klären.",
        "",
        "| Regierungsbezirk | Primärzweck | Einwohnerklasse | Format | Fälle |",
        "|---|---|---|---|---:|",
    ]
    lines += ["| " + " | ".join(map(str, k)) + f" | {v} |" for k, v in sorted(cross.items())]
    lines += [
        "",
        "Die Fallauswahl ist eine gezielte Methodenbenchmark; keine Inklusionswahrscheinlichkeiten oder populationsbezogenen Klimapolitikquoten.",
        "Alle Quellenrechte UNRESOLVED. Hauptpakete gesperrt bis tatsächlicher Kalibrierung, Abhängigkeitsklärung, Rechteentscheidung und Freeze.",
        "Relationsanker sind keine bestätigten Kanten. Retrievalfälle enthalten zunächst nur den fokalen Kandidaten; Multi-System-Pooling und Antwortbewertung sind damit noch nicht abgeschlossen.",
    ]
    (folder / "SAMPLING_COVERAGE.md").write_text("\n".join(lines) + "\n")
    return graph["component_count"], proxy["component_count"]
