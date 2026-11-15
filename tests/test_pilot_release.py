"""Synthetic security/transport checks. No real rights decisions or human judgments."""

import gc
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crawler.web.app import create_app
from crawler.web.fixture import create_fixture_service
from crawler.web.pilot import PilotBoundary, verify_bundle
from crawler.web.pilot_bundle import export_bundle
from research.bavaria.acquisition import initialize
from research.bavaria.workspace.core import make_manifest

TOKEN = "synthetic-technical-gateway-token-not-a-real-secret"


def boundary():
    app = FastAPI()

    @app.post("/api/query")
    def query():
        return {"result_id": "result-one", "evidence": []}

    @app.get("/api/export/result-one.json")
    def export():
        return {"query": "technical fixture"}

    @app.get("/api/status")
    def status():
        return {"snapshot_id": "fixture"}

    return TestClient(
        PilotBoundary(app, gateway_token=TOKEN, users=["p001", "p002"], snapshot_id="fixture")
    )


def credentials(user="p001"):
    return {"X-Pilot-Gateway": TOKEN, "X-Pilot-User": user}


def test_individual_gateway_identity_and_cross_user_export_isolation():
    client = boundary()
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers=credentials("p003")).status_code == 401
    assert client.post("/api/query", headers=credentials()).status_code == 200
    assert client.get("/api/export/result-one.json", headers=credentials()).status_code == 200
    assert client.get("/api/export/result-one.json", headers=credentials("p002")).status_code == 404
    assert client.get("/research/gold_studio", headers=credentials()).status_code == 404
    health_response = client.get("/api/pilot-health", headers=credentials())
    assert health_response.headers["cache-control"] == "no-store"
    health = health_response.json()
    assert "query" not in json.dumps(health) and "p001" not in json.dumps(health)


def test_rate_budget_is_bounded_and_separate_per_identity():
    client = boundary()
    for _ in range(6):
        assert client.post("/api/query", headers=credentials()).status_code == 200
    assert client.post("/api/query", headers=credentials()).status_code == 429
    assert client.post("/api/query", headers=credentials("p002")).status_code == 200


def test_https_origin_is_explicit_and_cross_origin_posts_are_rejected(tmp_path):
    app = create_app(
        create_fixture_service(tmp_path / "fixture"), allowed_origin="https://pilot.example.test"
    )
    c = TestClient(app, base_url="https://pilot.example.test")
    assert c.get("/api/status").status_code == 200
    assert c.get("/methodik").status_code == 200
    assert (
        c.post("/api/query", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
    assert c.get("/api/graph").status_code == 503
    assert c.get("/api/status").json()["release_id"].startswith("preview-sha256:")
    with pytest.raises(ValueError):
        create_app(allowed_origin="http://public.example")


def technical_source(tmp_path):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(
        json.dumps(
            {
                "ags": "09162111",
                "name": "TECHNICAL Teststadt",
                "official_name": "TECHNICAL Teststadt",
                "region": "TECHNICAL",
                "district": "TECHNICAL",
            }
        )
        + "\n"
    )
    ledger = tmp_path / "input.sqlite"
    initialize(ledger, registry)
    # Legacy initializer leaves a connection cycle; collect before switching this fixture to DELETE.
    gc.collect()
    data = (
        b"<html><body><h1>TECHNICAL FIXTURE</h1>"
        b"<p>Planung einer Waermeversorgung 2026. Kein Gold.</p></body></html>"
    )
    sha = hashlib.sha256(data).hexdigest()
    raw = tmp_path / "original.html"
    raw.write_bytes(data)
    with sqlite3.connect(ledger) as c:
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?)",
            (sha, str(raw), len(data), len(data), "PARSED"),
        )
        c.execute(
            "INSERT INTO associations VALUES (?,?,?,?)",
            (sha, "09162111", "https://example.test/source", "UNKNOWN"),
        )
        c.execute(
            "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
            ("fixture-unit", sha, 1, "TECHNICAL Planung Waerme 2026", None, None, "html"),
        )
        c.execute("CREATE TABLE private_gold(secret TEXT)")
        c.execute("INSERT INTO private_gold VALUES ('DO_NOT_EXPORT')")
    manifest = tmp_path / "workspace.json"
    binding = make_manifest(ledger, None, manifest)
    rights = {
        "schema": "klima-pilot-rights-v1",
        "release_status": "CLOSED_PILOT_APPROVED",
        "technical_test_only": True,
        "decision_by": "SYNTHETIC_TEST_NOT_OWNER",
        "basis": "TECHNICAL_FIXTURE_NO_LEGAL_REVIEW",
        "parent_snapshot_id": binding["snapshot_id"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sources": [
            {
                "sha256": sha,
                "source_metadata_display": "APPROVED",
                "original_url_display": "APPROVED",
                "excerpt_display": "APPROVED",
                "archive_distribution": "APPROVED",
                "derived_graph_display": "UNRESOLVED",
                "basis": "SYNTHETIC_FIXTURE",
            }
        ],
    }
    p = tmp_path / "rights.json"
    p.write_text(json.dumps(rights))
    return manifest, p


def test_portable_subset_hashes_canonical_access_and_no_private_tables(tmp_path):
    manifest, rights = technical_source(tmp_path)
    output = tmp_path / "bundle"
    export_bundle(manifest, rights, output, deployment_root=str(output), technical=True)
    with pytest.raises(ValueError, match="production-approved"):
        verify_bundle(output)
    bundle, _ = verify_bundle(output, allow_technical_fixture=True)
    with sqlite3.connect(output / "ledger.sqlite") as c:
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='private_gold'").fetchall()
    from crawler.core.retrieval import RetrievalMode
    from crawler.web.bavaria import create_bavaria_service
    from crawler.web.service import QueryOptions

    service = create_bavaria_service(
        output / "workspace.json", original_roots=[output / "originals"]
    )
    result = service.execute(
        QueryOptions("Waerme", RetrievalMode.LEXICAL, 5, 5, 10000, False, ("09162111",))
    )
    assert result["evidence"] and service.database_snapshot_id == bundle["snapshot_id"]
    data, _, _ = service.original(result["result_id"], result["evidence"][0]["evidence_id"])
    assert b"passive" in data or "passive" in str(data)
    (output / "rights.json").write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        verify_bundle(output, allow_technical_fixture=True)


def test_unapproved_production_export_is_blocked_before_output(tmp_path):
    manifest, rights = technical_source(tmp_path)
    with pytest.raises(ValueError, match="rights approval"):
        export_bundle(manifest, rights, tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("tamper", ["external_ledger", "federation", "artifact_hash", "snapshot"])
def test_self_consistent_file_hashes_do_not_allow_external_workspace_references(tmp_path, tamper):
    manifest, rights = technical_source(tmp_path)
    output = tmp_path / "bundle"
    export_bundle(manifest, rights, output, deployment_root=str(output), technical=True)
    workspace_path = output / "workspace.json"
    workspace = json.loads(workspace_path.read_text())
    if tamper == "external_ledger":
        workspace["artifacts"]["ledger"]["path"] = str(tmp_path / "private-gold.sqlite")
    elif tamper == "federation":
        workspace["source_ledger_order"] = [str(tmp_path / "private-gold.sqlite")]
    elif tamper == "artifact_hash":
        workspace["artifacts"]["ledger"]["sha256"] = "0" * 64
    else:
        workspace["snapshot_id"] = "forged-snapshot"
    workspace_path.write_text(json.dumps(workspace))
    bundle_path = output / "BUNDLE.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["files"]["workspace.json"] = hashlib.sha256(workspace_path.read_bytes()).hexdigest()
    bundle_path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="Pilot"):
        verify_bundle(output, allow_technical_fixture=True)


def test_expired_pilot_and_incomplete_secret_fail_closed():
    app = FastAPI()
    with pytest.raises(ValueError):
        PilotBoundary(app, gateway_token="short", users=["p001"], snapshot_id="fixture")
    client = TestClient(
        PilotBoundary(app, gateway_token=TOKEN, users=["p001"], snapshot_id="fixture", expires_at=0)
    )
    assert client.get("/api/status", headers=credentials()).status_code == 503


def test_shared_sanitizer_keeps_gold_transport_compatible():
    from crawler.core.source_presentation import sanitized_html
    from research.gold_studio.portable import sanitized_html as gold_sanitizer

    assert sanitized_html is gold_sanitizer
    html = sanitized_html("<table><tr><td>Wert</td></tr></table><script>alert(1)</script>")
    assert "<td>Wert</td>" in html and "<script" not in html


def test_feedback_validator_rejects_context_without_opt_in(tmp_path):
    from crawler.web.pilot_feedback import validate

    path = tmp_path / "feedback.json"
    data = {
        "schema": "klima-pilot-feedback-v1",
        "category": "science",
        "message": "TECHNICAL TEST",
        "research_gold": False,
        "transfer": "MANUAL_ONLY",
        "context_included": False,
        "context": None,
    }
    path.write_text(json.dumps(data))
    assert validate(path)["research_gold"] is False
    data["context"] = {"question": "Not opted in"}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="opt-in"):
        validate(path)


def test_optional_pilot_result_retention_preserves_default_and_expires_payload(monkeypatch):
    import crawler.web.service as service_module
    from crawler.core.retrieval import RetrievalResult
    from crawler.web.service import ImmutableResultStore

    clock = [100.0]
    monkeypatch.setattr(service_module.time, "monotonic", lambda: clock[0])
    result = RetrievalResult("TECHNICAL", "lexical", "fixture", None, ())
    store = ImmutableResultStore(ttl_seconds=1800)
    local = ImmutableResultStore()
    pilot_id = store.put(result)
    local_id = local.put(result)
    assert store.get(pilot_id).canonical is result
    clock[0] = 1900.0
    store.purge_expired()
    assert store.get(pilot_id) is None
    assert local.get(local_id).canonical is result


@pytest.mark.parametrize(
    "permission",
    ["source_metadata_display", "original_url_display", "excerpt_display", "archive_distribution"],
)
def test_each_exposed_source_class_needs_explicit_approval(tmp_path, permission):
    manifest, path = technical_source(tmp_path)
    rights = json.loads(path.read_text())
    rights["sources"][0].pop(permission)
    path.write_text(json.dumps(rights))
    with pytest.raises(ValueError, match="unapproved"):
        export_bundle(manifest, path, tmp_path / "denied", technical=True)
    assert not (tmp_path / "denied").exists()


def test_export_removes_unrelated_frames_crawl_requests_and_registry_extras(tmp_path):
    manifest, path = technical_source(tmp_path)
    ledger = tmp_path / "input.sqlite"
    rights = json.loads(path.read_text())
    sha = rights["sources"][0]["sha256"]
    with sqlite3.connect(ledger) as c:
        record = json.loads(c.execute("SELECT record FROM municipalities").fetchone()[0])
        record["private_extra"] = "DO_NOT_EXPORT"
        c.execute("UPDATE municipalities SET record=?", (json.dumps(record),))
        c.execute("INSERT INTO municipalities(ags,record) VALUES (?,?)", ("09999999", "{}"))
        c.execute(
            "INSERT INTO requests(ags,url,sha256,at) VALUES (?,?,?,?)",
            ("09162111", "https://example.test/internal-crawl", sha, "fixture"),
        )
    manifest = tmp_path / "minimal-input.json"
    binding = make_manifest(ledger, None, manifest)
    rights["parent_snapshot_id"] = binding["snapshot_id"]
    path.write_text(json.dumps(rights))
    output = tmp_path / "minimal"
    export_bundle(manifest, path, output, deployment_root=str(output), technical=True)
    with sqlite3.connect(output / "ledger.sqlite") as c:
        assert c.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
        assert c.execute("SELECT ags FROM municipalities").fetchall() == [("09162111",)]
        assert "private_extra" not in c.execute("SELECT record FROM municipalities").fetchone()[0]
    verify_bundle(output, allow_technical_fixture=True)

    from crawler.web.bavaria import create_bavaria_service

    service = create_bavaria_service(
        output / "workspace.json", original_roots=[output / "originals"]
    )
    assert service.retrieved_at[sha] == "fixture"


def linked_bundle(tmp_path):
    manifest, path = technical_source(tmp_path)
    rights = json.loads(path.read_text())
    rights["source_presentation"] = "linked_originals"
    rights["sources"][0]["archive_distribution"] = "DENIED"
    rights["sources"][0]["internal_processing"] = "APPROVED"
    path.write_text(json.dumps(rights))
    output = tmp_path / "linked"
    export_bundle(manifest, path, output, deployment_root=str(output), technical=True)
    return output


def test_linked_preview_real_service_no_archive_and_five_isolated_users(tmp_path, monkeypatch):
    import crawler.web.pilot as pilot

    output = linked_bundle(tmp_path)
    assert not list((output / "originals").iterdir())
    original_verify = pilot.verify_bundle
    monkeypatch.setattr(
        pilot, "verify_bundle", lambda root: original_verify(root, allow_technical_fixture=True)
    )
    users = tmp_path / "users.json"
    users.write_text(json.dumps([f"p{i:03}" for i in range(1, 6)]))
    gateway = tmp_path / "gateway"
    gateway.write_text(TOKEN)
    for key, value in {
        "PILOT_SNAPSHOT_ROOT": str(output),
        "PILOT_BUNDLE_SHA256": hashlib.sha256((output / "BUNDLE.json").read_bytes()).hexdigest(),
        "PILOT_GATEWAY_TOKEN_FILE": str(gateway),
        "PILOT_USERS_FILE": str(users),
        "PILOT_ORIGIN": "https://pilot.example.test",
    }.items():
        monkeypatch.setenv(key, value)
    client = TestClient(pilot.create_pilot_app(), base_url="https://pilot.example.test")
    assert client.get("/api/status").status_code == 401
    status = client.get("/api/status", headers=credentials()).json()
    assert status["research_preview"]["results"] == "REAL_PROVISIONAL"
    for i in range(1, 6):
        assert client.get("/api/status", headers=credentials(f"p{i:03}")).status_code == 200
    result = client.post(
        "/api/query",
        headers=credentials(),
        json={"question": "Waerme", "mode": "lexical", "generate_answer": False},
    ).json()
    entry = result["evidence"][0]
    assert entry["source_metadata"]["archive_url"] is None
    assert entry["source_metadata"]["identity_status"] == "EXPORT_VERIFIED_NO_RUNTIME_ORIGINAL"
    assert entry["document"]["source_url"] == "https://example.test/source"
    url = f"/api/source/{result['result_id']}/{entry['evidence_id']}"
    assert client.get(url, headers=credentials()).status_code == 404
    assert client.get(url + "?raw=true", headers=credentials()).status_code == 404
    export = f"/api/export/{result['result_id']}.json"
    assert client.get(export, headers=credentials()).status_code == 200
    assert client.get(export, headers=credentials("p005")).status_code == 404
    for internal in (
        "/research/gold_studio",
        "/.git/config",
        "/research/confirmation",
        "/snapshot/ledger.sqlite",
        "/docs",
    ):
        assert client.get(internal, headers=credentials()).status_code == 404


def test_linked_preview_rejects_mode_tampering_and_missing_verification(tmp_path):
    output = linked_bundle(tmp_path)
    from crawler.web.pilot_bundle import valid_rights

    unapproved = json.loads((output / "rights.json").read_text())
    unapproved["sources"][0].pop("internal_processing")
    with pytest.raises(ValueError, match="unapproved"):
        valid_rights(unapproved, technical=True)
    bundle_path = output / "BUNDLE.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["source_presentation"] = "archived"
    bundle_path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="presentation"):
        verify_bundle(output, allow_technical_fixture=True)
    bundle["source_presentation"] = "linked_originals"
    archive = output / "originals" / ("0" * 64 + ".bin")
    archive.write_bytes(b"TECHNICAL_ARCHIVE_MUST_NOT_BE_DISTRIBUTED")
    key = str(archive.relative_to(output))
    bundle["files"][key] = hashlib.sha256(archive.read_bytes()).hexdigest()
    bundle_path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="must not contain archives"):
        verify_bundle(output, allow_technical_fixture=True)
    archive.unlink()  # Only the synthetic attack payload created in this test.
    del bundle["files"][key]
    with sqlite3.connect(output / "ledger.sqlite") as c:
        c.execute("DELETE FROM metadata WHERE key LIKE 'pilot_verified_original:%'")
    ledger = output / "ledger.sqlite"
    bundle["files"]["ledger.sqlite"] = hashlib.sha256(ledger.read_bytes()).hexdigest()
    bundle_path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="receipt missing"):
        verify_bundle(output, allow_technical_fixture=True)
