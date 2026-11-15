"""Local-only real-data transport simulation; never a legal approval or pilot deployment."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler.web.bavaria import create_bavaria_service
from crawler.web.graph_explorer import GraphExplorer
from crawler.web.pilot import verify_bundle
from crawler.web.pilot_bundle import export_bundle


def run(manifest, selection, output, presentation="archived"):
    rows = json.loads(selection.read_text())
    shas = sorted({r["original_sha256"] for r in rows})
    parent = create_bavaria_service(manifest).database_snapshot_id
    output.mkdir(parents=True, exist_ok=False)
    rights = {
        "schema": "klima-pilot-rights-v1",
        "technical_test_only": True,
        "source_presentation": presentation,
        "release_status": "CLOSED_PILOT_APPROVED",
        "decision_by": "TECHNICAL_SIMULATION_NOT_A_RIGHTS_OWNER",
        "basis": "LOCAL_TRANSPORT_TEST_ONLY_NO_EXTERNAL_RIGHTS_GRANTED",
        "parent_snapshot_id": parent,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sources": [
            {
                "sha256": s,
                "internal_processing": "APPROVED",
                "source_metadata_display": "APPROVED",
                "original_url_display": "APPROVED",
                "excerpt_display": "APPROVED",
                "archive_distribution": "APPROVED",
                "derived_graph_display": "APPROVED",
                "basis": "SYNTHETIC_TECHNICAL_PERMISSION_VALUE",
            }
            for s in shas
        ],
    }
    r = output / "TECHNICAL_RIGHTS_SIMULATION.json"
    r.write_text(json.dumps(rights, indent=2) + "\n")
    bundle = output / "bundle"
    receipt = export_bundle(
        manifest, r, bundle, deployment_root=str(bundle.resolve()), technical=True
    )
    verify_bundle(bundle, allow_technical_fixture=True)
    production_rejected = False
    try:
        verify_bundle(bundle)
    except ValueError:
        production_rejected = True
    assert production_rejected
    service = create_bavaria_service(
        bundle / "workspace.json",
        original_roots=[bundle / "originals"],
        source_presentation=presentation,
    )
    graph = GraphExplorer(service.workspace).overview()
    first = service.workspace.relation_rows[0]["relation_id"]
    result = service.graph_evidence(first)
    if presentation == "archived":
        source = service.original(result["result_id"], result["evidence"][0]["evidence_id"])
        assert source[0]
    else:
        assert not list((bundle / "originals").iterdir())
        assert all(e["source_metadata"]["archive_url"] is None for e in result["evidence"])
    report = {
        "schema": "pilot-real-source-transport-test-v1",
        "technical_test_only": True,
        "independent_human_reviews": 0,
        "external_rights_granted": False,
        "production_factory_rejects_fixture": True,
        "parent_snapshot_id": parent,
        "subset_snapshot_id": receipt["snapshot_id"],
        "original_versions": len(shas),
        "graph_entities": graph["entities"],
        "graph_relations": graph["relations"],
        "canonical_edge_inspection": "PASS",
        "original_read": "PASS" if presentation == "archived" else "DISABLED_NO_ARCHIVE_FILES",
        "source_presentation": presentation,
        "bundle_bytes": sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()),
    }
    (output / "RECEIPT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--selection", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--source-presentation", choices=["archived", "linked_originals"], default="archived"
    )
    a = p.parse_args()
    run(a.manifest, a.selection, a.output, a.source_presentation)
