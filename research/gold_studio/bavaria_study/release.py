"""Version source-free release metadata for a prepared (never automatically Gold) study."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from ..store import digest
from .analysis import write_report
from .bundle import load_bundle
from .coverage import write_coverage


def release(study, output, historical_sources, federation):
    root, dst = Path(study), Path(output)
    if dst.exists():
        raise ValueError("Choose a fresh release version")
    dst.mkdir(parents=True)
    cases = json.loads((root / "cases.json").read_text())
    report = json.loads((root / "REPORT.json").read_text())
    bundle = load_bundle(root)
    component_counts = write_report(root)
    (dst / "BATCHES.json").write_bytes((root / "batches.json").read_bytes())
    (dst / "SAMPLING_COVERAGE.md").write_bytes((root / "SAMPLING_COVERAGE.md").read_bytes())
    write_coverage(root, federation, dst / "MUNICIPALITY_COVERAGE.csv")
    old = json.loads(Path(historical_sources).read_text())
    old_ags = {a for s in old for a in s.get("frame_ags", [])}
    old_urls = {s["url"].split("?")[0].rstrip("/") for s in old}
    links = [
        {
            "task_id": t["task_id"],
            "prior_municipality_frame_overlap": t["ags"] in old_ags,
            "prior_url_overlap": t["source_record"]["url"].split("?")[0].rstrip("/") in old_urls,
            "same_project": "UNRESOLVED",
        }
        for t in cases
    ]
    (dst / "HISTORICAL_EXPOSURE.json").write_text(
        json.dumps(
            {
                "classification": "FRAME_OVERLAP_NOT_CLAIM_SCOPE_OR_PROJECT_IDENTITY",
                "historical_split_changed": False,
                "links": links,
            },
            indent=2,
        )
    )
    with (dst / "RIGHTS_TRANSFER.csv").open("w") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "sha256",
                "ags",
                "url",
                "status",
                "owner",
                "decision_date",
                "legal_basis",
                "external_transfer",
            ],
        )
        w.writeheader()
        for t in cases:
            w.writerow(
                {
                    "sha256": t["source_record"]["sha256"],
                    "ags": t["ags"],
                    "url": t["source_record"]["url"],
                    "status": "UNRESOLVED",
                    "owner": "",
                    "decision_date": "",
                    "legal_basis": "",
                    "external_transfer": "false",
                }
            )
    topics = {
        "Wärme/Energie": r"wärme|heizung|energiewende",
        "Erneuerbare": r"photovoltaik|solar|windkraft|erneuerbar",
        "Mobilität": r"radverkehr|mobilität|ladestation|öffentlichen verkehr",
        "Gebäude/Effizienz": r"sanier|gebäude|effizienz",
        "Anpassung": r"hochwasser|hitze|entsiegel|begrün|klimaanpass",
        "Förderung/Finanzierung": r"förder|finanz|euro|haushalt",
        "Ziele/Bilanz": r"treibhaus|co2|bilanz|klimaneutral",
        "Beteiligung/Beratung": r"beratung|beteilig|bürger",
    }
    topic_counts: Counter[str] = Counter()
    with (dst / "CONTENT_STRATA.csv").open("w") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "task_id",
                "source_sha256",
                "topic_cues",
                "reference_year_cues_not_publication_date",
                "focus_kind",
                "classification_status",
            ],
        )
        w.writeheader()
        for t in cases:
            text = t["focus"]["text"]
            tags = [k for k, v in topics.items() if re.search(v, text, re.I)]
            topic_counts.update(tags)
            w.writerow(
                {
                    "task_id": t["task_id"],
                    "source_sha256": t["source_record"]["sha256"],
                    "topic_cues": " | ".join(tags),
                    "reference_year_cues_not_publication_date": " | ".join(
                        sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text)))
                    ),
                    "focus_kind": t["focus"]["kind"],
                    "classification_status": (
                        "RULE_CUES_ONLY_NOT_HUMAN_POLICY_OR_DOCUMENT_CLASSIFICATION"
                    ),
                }
            )
    manifest = {
        "schema": "bavaria-human-candidate-selection-v2",
        "status": "PREPARED_NOT_HUMAN_CALIBRATED_NOT_CONFIRMATORY_FROZEN",
        "seed": report["seed"],
        "deduplicate_same_purpose_focus": True,
        "counts": {
            k: report[k]
            for k in [
                "cases",
                "municipalities",
                "selected_originals",
                "families",
                "regions",
                "formats",
                "population_classes",
            ]
        },
        "known_component_counts": {
            "source_url_span": component_counts[0],
            "host_proxy": component_counts[1],
        },
        "prior_frame_overlaps": sum(t["prior_municipality_frame_overlap"] for t in links),
        "prior_url_overlaps": sum(t["prior_url_overlap"] for t in links),
        "rule_topic_cues_not_policy_prevalence": dict(topic_counts),
        "studio_selection_sha256": digest(bundle["selection"]),
        "review_contract": bundle["selection"]["review_contract"],
        "private_artifacts": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.iterdir()
            if p.is_file()
        },
        "cases": [
            {
                k: t[k]
                for k in [
                    "task_id",
                    "primary_purpose",
                    "ags",
                    "region",
                    "required_stages",
                    "allowed_original_sha256",
                    "effective_split",
                    "components",
                ]
            }
            for t in cases
        ],
        "metadata_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dst.iterdir() if p.is_file()
        },
    }
    (dst / "SELECTION.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                k: v
                for k, v in manifest.items()
                if k not in ["cases", "private_artifacts", "metadata_sha256"]
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["study", "output", "historical-sources", "federation"]:
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    release(a.study, a.output, a.historical_sources, a.federation)
