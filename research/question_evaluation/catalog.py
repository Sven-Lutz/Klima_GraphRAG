"""Frozen 600-case technical catalog from permissible corpus metadata and exposed graph."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from research.bavaria.federation import source_connection
from research.gold_studio.retrieval_benchmark.preview_readiness import workspace

CATEGORIES = [
    "source_scope",
    "policy_search",
    "measure_status",
    "actor_roles",
    "funding",
    "targets_outcomes",
    "temporal",
    "comparison",
    "graph_paths",
    "adversarial",
]
CLIMATE = re.compile(r"klima|wärme|solar|photovolta|hochwasser|energie|förder|mobilität", re.I)
TOPICS = [
    ("Photovoltaik", r"photovolta|solar"),
    ("Wärmeplanung", r"wärme|waerme"),
    ("Klimaanpassung", r"hochwasser|hitze|starkregen"),
    ("Mobilität", r"mobilität|radverkehr"),
    ("Förderprogramme", r"förder|zuschuss"),
    ("Klimaschutzziele", r"klimaschutz|klimaneutral"),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(manifest, output):
    if output.exists():
        raise FileExistsError("Immutable catalog; use fresh output")
    if shutil.disk_usage(".").free < 15 * 1024**3 + 16 * 1024**2:
        raise RuntimeError("SSD reserve")
    w = workspace(manifest)
    # Entire historical review and 400-candidate sources are excluded from new text tasks.
    old = json.loads(
        Path(
            "research/bavaria/completion/release/independent-review-final/source_manifest.json"
        ).read_text()
    )
    cases = json.loads(Path("generated/gold-studio-bavaria-candidates-v2/cases.json").read_text())
    blocked = {r["sha256"] for r in old} | {r["focal_source_sha256"] for r in cases}
    qfreeze = Path("research/gold_studio/retrieval_benchmark/release/preview/QUERIES.json")
    quarantined = {r["ags"] for r in json.loads(qfreeze.read_text()) if r["split"] != "development"}
    selected = {}
    c = source_connection(w.db, w.source_databases)
    try:
        for r in c.execute(
            "SELECT u.*,a.ags FROM units u JOIN associations a USING(sha256) "
            "ORDER BY u.sha256,u.ordinal,a.ags"
        ):
            if r["sha256"] in blocked or r["ags"] in quarantined or r["ags"] in selected:
                continue
            text = r["text"]
            if (
                len(text) < 100
                or not CLIMATE.search(text)
                or re.search(r"cookie|datenschutzerklärung", text, re.I)
            ):
                continue
            topic = next(
                (name for name, pattern in TOPICS if re.search(pattern, text, re.I)), "Klimapolitik"
            )
            selected[r["ags"]] = {
                "ags": r["ags"],
                "source_sha256": r["sha256"],
                "unit_id": r["unit_id"],
                "topic": topic,
            }
    finally:
        c.close()
    # Round-robin all seven regions, deterministic IDs; purposive, not population sampling.
    by_region = {}
    for row in selected.values():
        region = w.municipalities[row["ags"]]["region"]
        by_region.setdefault(region, []).append(row)
    chosen = []
    for rows in by_region.values():
        rows.sort(key=lambda x: x["ags"])
    while len(chosen) < 60 and any(by_region.values()):
        for region in sorted(by_region):
            if by_region[region] and len(chosen) < 60:
                chosen.append(by_region[region].pop(0))
    if len(chosen) < 60:
        raise ValueError("Insufficient independent frame diversity")
    graph = [
        r
        for r in sorted(w.relation_rows, key=lambda r: r["relation_id"])
        if r["record"]["source_sha256"] not in {t["focal_source_sha256"] for t in cases}
    ]
    if len(graph) < 60:
        raise ValueError("Insufficient exposed graph relations")
    adjacency = {}
    for edge in graph:
        adjacency.setdefault(edge["subject_id"], []).append((edge["object_id"], edge))
        adjacency.setdefault(edge["object_id"], []).append((edge["subject_id"], edge))
    paths = {}
    for start in sorted(adjacency):
        for mid, first in adjacency[start]:
            for end, second in adjacency.get(mid, []):
                key = tuple(sorted((start, end)))
                if start != end and end not in {x for x, _ in adjacency[start]}:
                    paths.setdefault(key, (start, end, first, second))
    path_cases = [paths[key] for key in sorted(paths)]
    if len(path_cases) < 60:
        raise ValueError("Need 60 distinct two-hop endpoints")
    catalog = []
    for ci, cat in enumerate(CATEGORIES):
        for i, source in enumerate(chosen):
            ags = source["ags"]
            name = w.municipalities[ags]["name"]
            topic = source["topic"]
            other = chosen[(i + 1) % 60]
            relation = graph[i]
            sub = w.entities[relation["subject_id"]]["label"]
            obj = w.entities[relation["object_id"]]["label"]
            questions = {
                "source_scope": (
                    f"Welche archivierten Quellen zu {topic} sind {name} zugeordnet, "
                    f"und ist deren Zuständigkeit geprüft?"
                ),
                "policy_search": (
                    f"Welche Belegstellen zu {topic} finden sich im Quellenrahmen von {name}?"
                ),
                "measure_status": (
                    f"Welche Planungen oder berichteten Umsetzungen zu {topic} nennen "
                    f"Quellen aus {name}?"
                ),
                "actor_roles": f"Welche Rolle wird zwischen „{sub}“ und „{obj}“ beschrieben?",
                "funding": (
                    f"Welche Belege nennen Förderung, Kosten oder Zuständigkeiten zu "
                    f"{topic} in {name}?"
                ),
                "targets_outcomes": (
                    f"Welche Ziele oder berichteten Wirkungen zu {topic} sind für {name} belegt?"
                ),
                "temporal": (
                    f"Welche zeitlichen Angaben nennen Quellen zu {topic} in {name}, "
                    f"und belegen sie bereits Umsetzung?"
                ),
                "comparison": (
                    f"Welche gleich definierten Belege zu {topic} gibt es für {name} "
                    f"und {w.municipalities[other['ags']]['name']}?"
                ),
                "graph_paths": (
                    f"Welche evidenzgebundene Verbindung besteht zwischen „{sub}“ und „{obj}“?"
                ),
                "adversarial": (
                    f"Beweisen die archivierten Quellen zu {topic} in {name}, dass "
                    f"sämtliche Klimamaßnahmen vollständig umgesetzt sind? Korrigiere "
                    f"eine unbelegte Prämisse."
                ),
            }
            graph_case = cat in ("actor_roles", "graph_paths")
            if graph_case:
                record = relation["record"]
                frames = record.get("source_frame_ags") or []
                ags = frames[0] if frames else source["ags"]
                origin = {
                    "kind": "EXPOSED_PROVISIONAL_GRAPH",
                    "relation_id": relation["relation_id"],
                    "source_sha256": record["source_sha256"],
                    "unit_id": record["unit_id"],
                    "entity_ids": [relation["subject_id"], relation["object_id"]],
                }
            else:
                origin = {
                    "kind": "NEW_SOURCE_METADATA_NEUTRAL_QUESTION",
                    "source_sha256": source["source_sha256"],
                    "unit_id": source["unit_id"],
                }
            expected_http = 200
            override = {}
            if cat == "graph_paths":
                start, end, first, second = path_cases[i]
                path_frames = sorted(
                    set(first["record"]["source_frame_ags"] + second["record"]["source_frame_ags"])
                )
                if not path_frames:
                    raise ValueError("Two-hop path lacks actual source frames")
                ags = path_frames[0]
                origin = {
                    "kind": "EXPOSED_TWO_HOP_GRAPH",
                    "entity_ids": [start, end],
                    "relation_ids": [first["relation_id"], second["relation_id"]],
                    "source_sha256s": sorted(
                        {first["record"]["source_sha256"], second["record"]["source_sha256"]}
                    ),
                    "unit_ids": sorted({first["record"]["unit_id"], second["record"]["unit_id"]}),
                    "source_sha256": first["record"]["source_sha256"],
                    "unit_id": first["record"]["unit_id"],
                }
                questions[cat] = (
                    f"Welche Evidenzkette verbindet „{w.entities[start]['label']}“ "
                    f"mit „{w.entities[end]['label']}“ über weitere Entitäten?"
                )
            if cat == "adversarial" and i % 4 == 0:
                override = {"municipality_ids": ["00000000"]}
                expected_http = 422
            elif cat == "adversarial" and i % 4 == 1:
                override = {"question": " "}
                expected_http = 422
            catalog.append(
                {
                    "id": f"KQA1-{ci + 1:02}-{i + 1:03}",
                    "question": questions[cat],
                    "category": cat,
                    "difficulty": "advanced"
                    if cat in ("comparison", "graph_paths", "adversarial", "temporal")
                    else "standard",
                    "split": "development_diagnostic",
                    "confirmation_eligible": False,
                    "municipality_ids": path_frames
                    if cat == "graph_paths"
                    else [ags, other["ags"]]
                    if cat == "comparison"
                    else [ags],
                    "geographic_scope": "source frames, not inferred jurisdiction",
                    "temporal_scope": "UNKNOWN_SOURCE_REPORTED",
                    "region": w.municipalities[ags]["region"],
                    "origin": origin,
                    "dependency_group": origin["source_sha256"],
                    "allowed_methods": ["lexical", "lexical_expanded", "graph"]
                    if graph_case
                    else ["lexical", "lexical_expanded"],
                    "expected_technical_behavior": (
                        "RETRIEVAL_OR_EXPLICIT_EMPTY; generation disabled; no fabricated verdict"
                    ),
                    "controlled_paraphrases": [
                        f"Bitte recherchiere mit Quellenbelegen: {questions[cat]}"
                    ]
                    if i == 0
                    else [],
                    "paraphrases_are_independent_cases": False,
                    "request_override": override,
                    "expected_http": expected_http,
                    "reference_year": 2010 + i % 17 if cat == "temporal" else None,
                    "reference_answer": None,
                    "human_review_required": [
                        "authority",
                        "scope",
                        "relevance",
                        "claim_support",
                        "answerability",
                    ],
                    "snapshot_id": w.snapshot_id,
                    "model": None,
                    "prompt": None,
                    "seed": "kqa-v1",
                    "information_condition": (
                        "same question and AGS; graph additionally uses curated "
                        "provisional relations"
                    ),
                }
            )
    if len({r["question"] for r in catalog}) != 600:
        raise ValueError("Duplicate questions")
    output.mkdir(parents=True)
    raw = json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    (output / "CATALOG.json").write_text(raw)
    freeze = {
        "schema": "klima-question-qa-v1",
        "cases": 600,
        "categories": CATEGORIES,
        "catalog_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "snapshot_id": w.snapshot_id,
        "manifest_sha256": sha(manifest),
        "builder_sha256": sha(__file__),
        "selection": (
            "60 new source frames plus 60 already exposed graph relations; purposive technical QA"
        ),
        "text_frames": 60,
        "source_groups": len({r["dependency_group"] for r in catalog}),
        "all_development": True,
        "confirmation_queries_executed": 0,
        "excluded_originals": len(blocked),
        "excluded_quarantined_frames": len(quarantined),
        "human_gold": False,
    }
    (output / "FREEZE.json").write_text(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(freeze, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    build(a.manifest, a.output)
