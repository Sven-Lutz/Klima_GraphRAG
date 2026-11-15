"""Fail-closed statutory scope and complete response projection; no human gold labels."""

import copy
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawler.web.app import create_app
from crawler.web.minimal_policy import content_hash, validate_minimal_policy
from crawler.web.minimal_service import MinimalResearchService
from crawler.web.pilot import PilotBoundary
from crawler.web.quick_gateway import COOKIE, QuickOriginGateway
from crawler.web.service import ImmutableResultStore

TOKEN = "synthetic-gateway-not-a-real-secret-0123456789abcdef"
HOST = "a" * 64 + ".origin.invalid"
ORIGIN = "https://synthetic-pilot.trycloudflare.com"
PRIVATE = "FORBIDDEN_SOURCE_QUOTE_PRIVATE_GOLD_AND_PATH"
ROOT = Path(__file__).resolve().parents[1]


def policy():
    now = datetime.now(timezone.utc)
    spec = json.loads(
        (ROOT / "docs/pilot/minimal_external_preview/CANDIDATE_FACTS.json").read_text()
    )
    return dict(
        schema="klima-pilot-rights-v1",
        source_presentation="minimal_external_preview",
        release_status="SCOPED_STATUTORY_ASSESSMENT",
        rights_holder_consent_claimed=False,
        data_contract="minimal_external_preview-v1",
        basis="SYNTHETIC STATUTORY ASSESSMENT",
        decision_by="SYNTHETIC OWNER DECLARATION FIXTURE",
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        minimal_facts=spec,
        facts_sha256=content_hash(spec),
        owner_statement=dict(
            confirmed_by_owner=True,
            operator_role="noncommercial_individual_researcher",
            institutional_operator=False,
            purpose="joint_scientific_testing_and_quality_review",
            confirmed_at=now.isoformat(),
        ),
        sources=[
            dict(
                sha256=sha,
                internal_processing="STATUTORY_TDM_SCOPE",
                source_metadata_display="SCOPED_FACTUAL_METADATA",
                original_url_display="SCOPED_DIRECT_LINK",
                fact_display="SCOPED_OWN_FACTS",
                derived_graph_display="SCOPED_OWN_FACTS",
                excerpt_display="DENIED",
                archive_distribution="DENIED",
                basis="SYNTHETIC FIELD SCOPE",
            )
            for sha in sorted({f["source_sha256"] for f in spec["facts"]})
        ],
    )


@pytest.mark.parametrize(
    "alter",
    [
        lambda p: p.update(rights_holder_consent_claimed=True),
        lambda p: p["owner_statement"].update(confirmed_by_owner=False),
        lambda p: p["owner_statement"].update(operator_role="commercial"),
        lambda p: p["owner_statement"].update(institutional_operator=True),
        lambda p: p.update(
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ),
        lambda p: p.update(expires_at=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat()),
        lambda p: p["sources"][0].update(excerpt_display="APPROVED"),
        lambda p: p["sources"][0].update(archive_distribution="APPROVED"),
        lambda p: p["minimal_facts"]["facts"][0].update(summary="changed"),
        lambda p: p["sources"].pop(),
    ],
)
def test_minimal_policy_cannot_expand_scope_or_fabricate_role(alter):
    p = policy()
    validate_minimal_policy(p)
    alter(p)
    with pytest.raises(ValueError):
        validate_minimal_policy(p)


def gateway():
    app = FastAPI()

    @app.get("/api/status")
    def status():
        return {"status": "fixture"}

    @app.post("/api/query")
    def query():
        return {"result_id": "synthetic-result"}

    @app.get("/api/export/synthetic-result.json")
    def export():
        return {"result": "synthetic"}

    return QuickOriginGateway(
        PilotBoundary(
            app, gateway_token=TOKEN, users=["p001", "p002", "p003"], snapshot_id="synthetic"
        ),
        origin_host=HOST,
        public_origin=ORIGIN,
        gateway_token=TOKEN,
        health_token=TOKEN,
        expires_at=time.time() + 3600,
    )


def client(g):
    return TestClient(g, base_url=ORIGIN, headers={"Host": HOST})


def test_origin_rejects_anonymous_forged_identity_and_duplicate_host():
    g = gateway()
    c = TestClient(g, base_url=ORIGIN)
    assert (
        c.get(
            "/api/status",
            headers={
                "X-Pilot-Gateway": TOKEN,
                "X-Pilot-User": "p001",
                "Cf-Access-Authenticated-User-Email": "forged@example.test",
            },
        ).status_code
        == 401
    )
    assert c.get("/api/status", headers=[("Host", HOST), ("Host", HOST)]).status_code == 401
    assert g.forwarded == 0
    good = client(g)
    assert good.get("/api/status").status_code == 200
    assert good.cookies.get(COOKIE)
    assert good.post("/api/query").status_code == 200
    assert good.get("/api/export/synthetic-result.json").status_code == 200
    other = client(g)
    assert other.get("/api/status").status_code == 200
    assert (
        other.get("/api/export/synthetic-result.json", headers={"X-Pilot-User": "p001"}).status_code
        == 404
    )
    assert client(g).post("/api/query").status_code == 401
    assert client(g).get("/api/status").status_code == 200
    assert client(g).get("/api/status").status_code == 503
    assert good.get("/__private_health").status_code == 404
    assert (
        good.get("/__private_health", headers={"X-Local-Health": TOKEN}).json()["forwarded"]
        == g.forwarded
    )
    assert good.get("/research/gold_studio").status_code == 404
    g.expires_at = 0
    assert good.get("/api/status").status_code == 503


def test_whole_group_costly_budget_cannot_be_reset_by_new_browser_cookie():
    g = gateway()
    c = client(g)
    response = c.get("/api/status")
    assert "Secure; HttpOnly; SameSite=Lax" in response.headers["set-cookie"]
    for _ in range(6):
        assert c.post("/api/query").status_code == 200
    c2 = client(g)
    assert c2.get("/api/status").status_code == 200
    assert c2.post("/api/query").status_code == 429


class Inner:
    def __init__(self, p):
        self.database_snapshot_id = "synthetic-snapshot"
        self.projection_id = "synthetic-projection"
        self.manifest_path = Path("/private/NOT_PUBLIC")
        self.retriever = SimpleNamespace()
        self.store = ImmutableResultStore(capacity=2, ttl_seconds=1800)
        self.retrieved_at = {}
        self.records = {}
        relations, entities, evidence = [], {}, {}
        for f in p["minimal_facts"]["facts"]:
            rid = f["relation_id"]
            rec = dict(
                source_sha256=f["source_sha256"],
                reference_time=f["reference_time"],
                quote=PRIVATE,
                extraction_model="synthetic-model",
                prompt_version="synthetic-prompt",
                status="PROVISIONAL",
            )
            relations.append(
                dict(
                    relation_id=rid,
                    subject_id=f["subject_id"],
                    object_id=f["object_id"],
                    predicate=f["predicate"],
                    record=rec,
                    evidence_id=rid,
                )
            )
            self.records[rid] = dict(
                source_id="unit-" + rid,
                evidence_text=PRIVATE,
                document=dict(
                    document_id="source-version:" + f["source_sha256"],
                    municipality_ids=["synthetic"],
                    heading_context=PRIVATE,
                ),
                future_field={"private_gold": PRIVATE},
            )
            evidence[rid] = dict(
                unit_id="unit-" + rid, page_ref=f["page"], ordinal=1, heading=PRIVATE
            )
            for side in ("subject", "object"):
                entities[f[side + "_id"]] = dict(
                    label=PRIVATE, entity_type="synthetic", identity_basis=PRIVATE
                )
        self.workspace = SimpleNamespace(
            relation_rows=relations,
            entities=entities,
            evidence_rows=evidence,
            snapshot_id=self.database_snapshot_id,
            manifest={"artifacts": {"relations": {"sha256": "synthetic"}}},
            relations_db=Path("/private/NOT_PUBLIC"),
            relation_payload=lambda r: {"required_source_ids": [PRIVATE]},
        )

    def provider_gate_payload(self):
        return {"availability": "retrieval_only_ready"}

    def status(self):
        return dict(coverage={}, municipalities=[], examples=[])

    def execute(self, options):
        result = dict(
            result_id="synthetic-result",
            retrieval_result=dict(
                database_snapshot_id=self.database_snapshot_id, mode="lexical", private=PRIVATE
            ),
            evidence=list(self.records.values()),
            citation_registry={"private": PRIVATE},
            future_field=PRIVATE,
        )
        # Adapter uses the canonical store only for expiry/existence, not its serialized data.
        self.store.get = lambda rid: result if rid == "synthetic-result" else None
        return result

    def graph_evidence(self, relation_id):
        return self.execute(None)

    def close(self):
        pass


def test_all_api_export_graph_and_error_paths_use_only_explicit_fields():
    p = policy()
    inner = Inner(p)
    service = MinimalResearchService(inner, p)
    with TestClient(create_app(service)) as c:
        r = c.post("/api/query", json={"question": "Freie Frage zum Klima"})
        assert r.status_code == 200
        out = r.json()
        assert len(out["evidence"]) == 4
        assert PRIVATE not in r.text
        for fmt in ("json", "md"):
            exported = c.get("/api/export/synthetic-result." + fmt)
            assert exported.status_code == 200
            assert PRIVATE not in exported.text
        overview = c.get("/api/graph")
        assert overview.status_code == 200 and PRIVATE not in overview.text
        root = overview.json()["default_entity"]
        graph = c.get("/api/graph", params={"root": root})
        assert graph.status_code == 200 and PRIVATE not in graph.text
        assert graph.json()["edges"][0]["extraction_method"] == "synthetic-model"
        assert c.get("/api/graph/evidence/S01R02").status_code == 200
        assert PRIVATE not in c.get("/api/graph/evidence/S01R02").text
        assert (
            c.get("/api/source/synthetic-result/fact-evidence:S01R02?raw=true").status_code == 404
        )
        assert c.get("/api/graph/evidence/UNKNOWN").status_code == 422
        inner.store.get = lambda rid: None
        assert c.get("/api/export/synthetic-result.json").status_code == 404


def test_extra_relation_is_rejected_before_serving():
    p = policy()
    inner = Inner(p)
    extra = copy.deepcopy(inner.workspace.relation_rows[0])
    extra["relation_id"] = "UNAPPROVED"
    inner.workspace.relation_rows.append(extra)
    with pytest.raises(ValueError, match="extra or missing"):
        MinimalResearchService(inner, p)


def test_public_projection_expires_with_the_canonical_store():
    from crawler.web.minimal_service import ProjectedStore

    canonical = ImmutableResultStore(capacity=2, ttl_seconds=1800)
    canonical.put(None)  # synthetic sentinel, never serialized or interpreted as gold
    key = next(iter(canonical._results))
    public = {key: {"question": "must expire"}}
    projected = ProjectedStore(canonical, public)
    canonical._expires[key] = 0
    projected.purge_expired()
    assert public == {} and projected.get(key) is None


def test_quick_factory_requires_real_provider_evidence_before_bundle_loading(tmp_path, monkeypatch):
    from crawler.web.quick_gateway import create_quick_app

    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    runtime.chmod(0o600)
    monkeypatch.setenv("KLIMA_PRIVATE_RUNTIME", str(runtime))
    with pytest.raises(ValueError, match="provider privacy agreement evidence"):
        create_quick_app()
    runtime.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        create_quick_app()


def test_origin_rewrite_integrates_with_existing_host_and_csrf_boundary(tmp_path):
    from crawler.web.fixture import create_fixture_service

    service = create_fixture_service(tmp_path / "fixture")
    app = create_app(service, allowed_origin=ORIGIN)
    boundary = PilotBoundary(
        app, gateway_token=TOKEN, users=["p001", "p002", "p003"], snapshot_id="synthetic"
    )
    g = QuickOriginGateway(
        boundary,
        origin_host=HOST,
        public_origin=ORIGIN,
        gateway_token=TOKEN,
        health_token=TOKEN,
        expires_at=time.time() + 3600,
    )
    with client(g) as c:
        assert c.get("/api/status").status_code == 200
        payload = {"question": "Welche Quellen nennen Klimaschutz?"}
        assert c.post("/api/query", json=payload, headers={"Origin": ORIGIN}).status_code == 200
        assert (
            c.post(
                "/api/query", json=payload, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
