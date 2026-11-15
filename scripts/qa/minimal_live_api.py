"""Exercise the real minimal bundle through HTTP ASGI routes, without publishing it."""

import argparse
import hashlib
import json
import resource
import time
from pathlib import Path

from fastapi.testclient import TestClient

from crawler.web.app import create_app
from crawler.web.pilot import PilotBoundary, load_pilot_service


def run(bundle, output):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    digest = hashlib.sha256((bundle / "BUNDLE.json").read_bytes()).hexdigest()
    service, rights = load_pilot_service(bundle, digest)
    token = "local-qa-only-gateway-token-never-used-in-a-tunnel-123456789"
    headers = {"X-Pilot-Gateway": token, "X-Pilot-User": "p001"}
    boundary = PilotBoundary(
        create_app(service, allowed_origin="https://local-qa.example.test"),
        gateway_token=token,
        users=["p001", "p002", "p003"],
        snapshot_id=service.database_snapshot_id,
    )
    queries, responses = [], []
    with TestClient(boundary, base_url="https://local-qa.example.test") as c:
        assert c.get("/api/status").status_code == 401
        status = c.get("/api/status", headers=headers).json()
        assert status["research_preview"]["source_presentation"] == "minimal_external_preview"
        for q in (
            "Wer fördert die Wärmeplanung in Nennslingen?",
            "Welche Rolle hat Ørsted beim Windpark Brunn-Laaber?",
            "Was berichtet Amberg für 2022 zum Strombedarf kommunaler Liegenschaften?",
        ):
            payload = dict(question=q, mode="lexical", generate_answer=False)
            r = c.post("/api/query", headers=headers, json=payload)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["evidence"] and data["answer_result"] is None
            ids = [e["source_id"] for e in data["evidence"]]
            repeated = c.post("/api/query", headers=headers, json=payload)
            assert repeated.status_code == 200
            assert ids == [e["source_id"] for e in repeated.json()["evidence"]]
            for fmt in ("json", "md"):
                exported = c.get(f"/api/export/{data['result_id']}.{fmt}", headers=headers)
                assert exported.status_code == 200
                responses.append(exported.text)
            foreign = {**headers, "X-Pilot-User": "p002"}
            assert (
                c.get(f"/api/export/{data['result_id']}.json", headers=foreign).status_code == 404
            )
            e = data["evidence"][0]
            assert e["source_metadata"]["archive_url"] is None
            assert (
                c.get(
                    f"/api/source/{data['result_id']}/{e['evidence_id']}?raw=true",
                    headers={**headers, "X-Pilot-User": "p003"},
                ).status_code
                == 404
            )
            queries.append(
                dict(
                    question=q,
                    returned_source_ids=ids,
                    facts=len(data["evidence"]),
                    request=data["request"],
                    repeat_ranking_equal=True,
                )
            )
            responses.append(r.text)
        overview = c.get("/api/graph", headers=headers)
        assert overview.status_code == 200
        g = overview.json()
        assert g["relations"] == 4 and g["entities"] == 8
        graph = c.get("/api/graph", params={"root": g["default_entity"]}, headers=headers)
        assert graph.status_code == 200 and graph.json()["edges"]
        # Six queries already used this identity's one-minute costly budget.
        edge = c.get(
            "/api/graph/evidence/" + graph.json()["edges"][0]["id"],
            headers={**headers, "X-Pilot-User": "p002"},
        )
        assert edge.status_code == 200
        assert edge.json()["evidence"]
        assert edge.json()["request"]["kind"] == "stored_relation"
        assert (
            c.post("/api/query", json={"question": "Energie"}, headers=headers).status_code == 429
        )
        for path in (
            "/research/gold_studio",
            "/.git/config",
            "/openapi.json",
            "/workspace.json",
            "/rights.json",
            "/api/export/unknown.json",
        ):
            assert c.get(path, headers=headers).status_code == 404
        responses += [overview.text, graph.text, edge.text, json.dumps(status)]
        rendered = "\n".join(responses)
        for forbidden in (
            '"quote":',
            '"evidence_excerpt":',
            '"raw_path":',
            '"required_source_ids":',
            "/Users/",
            "private_gold",
            "confirmation",
        ):
            assert (
                forbidden not in rendered.lower()
                if forbidden == "confirmation"
                else forbidden not in rendered
            )
        # Compare actual internal raw passages with ALL response/export strings.
        from research.bavaria.workspace.core import read_only

        with read_only(service._inner.workspace.db) as db:
            raw_passages = [row[0] for row in db.execute("SELECT text FROM units")]
        for text in raw_passages:
            if len(text) >= 100:
                assert (
                    text not in rendered
                    and json.dumps(text, ensure_ascii=False)[1:-1] not in rendered
                )
        (output / "RESPONSES.txt").write_text(rendered)
    report = dict(
        schema="minimal-live-api-v1",
        external_access=False,
        authenticated_email_login="NOT_TESTED",
        bundle_sha256=digest,
        snapshot_id=service.database_snapshot_id,
        source_presentation=rights["source_presentation"],
        expiry=rights["expires_at"],
        sources=4,
        graph_entities=8,
        graph_relations=4,
        queries=queries,
        routes_exports_and_isolation="PASS",
        source_copy_routes="DENIED",
        rate_limit="PASS_429",
        unexpected_private_fields="ABSENT",
        checked_raw_passages=len(raw_passages),
        wall_seconds=time.monotonic() - started,
        max_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        independent_scientific_validation=False,
    )
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    run(args.bundle.resolve(), args.output.resolve())
