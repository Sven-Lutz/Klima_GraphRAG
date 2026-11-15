"""Exercise candidate public DTOs on real retrieval, without exposing an HTTP service."""

import argparse
import json
import resource
import time
from pathlib import Path

from fastapi.testclient import TestClient

from crawler.core.retrieval import RetrievalMode
from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service
from crawler.web.graph_explorer import GraphExplorer
from crawler.web.minimal_external import MinimalExternalPreview
from crawler.web.service import QueryOptions


def paths(value, prefix=""):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = prefix + "." + key
            if key in {"evidence_text", "evidence_excerpt", "quote", "heading_context", "raw_path"}:
                found.append(here)
            found.extend(paths(item, here))
    elif isinstance(value, list):
        for item in value:
            found.extend(paths(item, prefix + "[]"))
    return found


def run(manifest, candidate, output):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    service = create_bavaria_service(manifest, source_presentation="linked_originals")
    view = MinimalExternalPreview(json.loads(candidate.read_text()))
    reports = []
    for index, question in enumerate(
        (
            "Wer fördert die kommunale Wärmeplanung?",
            "Wie wird der Stromverbrauch kommunaler Liegenschaften beschrieben?",
            "Welche Quellen beschreiben den geplanten Windpark Brunn-Laaber?",
        )
    ):
        response = service.execute(
            QueryOptions(question, RetrievalMode.LEXICAL, 10, 10, 16000, False)
        )
        dto = view.query(response, question)
        raw_export = json.loads(service.export(response["result_id"], "json"))
        markdown = service.export(response["result_id"], "markdown")
        assert dto["source_leads"]
        for fmt in ("json", "markdown"):
            (output / f"query-{index}.{'json' if fmt == 'json' else 'md'}").write_text(
                view.export(dto, fmt)
            )
        reports.append(
            {
                "question": question,
                "raw_hits": len(response["evidence"]),
                "minimal_source_leads": len(dto["source_leads"]),
                "raw_sensitive_field_paths": sorted(set(paths(response))),
                "minimal_sensitive_field_paths": paths(dto),
                "canonical_json_sensitive_field_paths": sorted(set(paths(raw_export))),
                "canonical_markdown_bytes": len(markdown.encode()),
                "canonical_markdown_is_used_by_minimal_view": False,
            }
        )
    explorer = GraphExplorer(service.workspace)
    edges = {}
    for fact in view.facts.values():
        raw = explorer.subgraph(fact["subject_id"], depth=1)
        edges.update({e["id"]: e for e in raw["edges"]})
    raw_graph = {"snapshot_id": service.database_snapshot_id, "edges": list(edges.values())}
    graph = view.graph(raw_graph)
    graph_evidence_paths = set()
    for relation_id in view.facts:
        evidence = service.graph_evidence(relation_id)
        graph_evidence_paths.update(paths(evidence))
        # Own source-level summaries remain a separate DTO, not replacement quotes.
        view.export(view.query(evidence, "Prüfung einer gespeicherten Relation"), "json")
    for fmt in ("json", "markdown"):
        (output / ("graph.json" if fmt == "json" else "graph.md")).write_text(
            view.export(graph, fmt)
        )
    report = {
        "schema": "minimal-external-review-receipt-v1",
        "deployment_approved": False,
        "scope": "Real local retrieval to separate review DTOs; no live API or rights approval",
        "questions": reports,
        "raw_graph_sensitive_fields": sorted(set(paths(raw_graph))),
        "public_graph_sensitive_fields": paths(graph),
        "graph_evidence_raw_sensitive_fields": sorted(graph_evidence_paths),
        "public_edges": len(graph["edges"]),
        "public_nodes": len(graph["nodes"]),
        "seconds": round(time.monotonic() - started, 3),
        "maxrss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "model_calls": 0,
        "external_exposure": False,
    }
    with TestClient(create_app(service)) as client:
        report["status_response_keys"] = sorted(client.get("/api/status").json())
        report["invalid_graph_response"] = client.get("/api/graph?root=unknown").json()
        report["archive_route_status"] = client.get("/api/source/unknown/unknown").status_code
        report["private_route_status"] = client.get("/research/gold_studio").status_code
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"questions": len(reports), "edges": len(graph["edges"]), "deployment_approved": False}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("docs/pilot/minimal_external_preview/CANDIDATE_FACTS.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.candidate, args.output)
