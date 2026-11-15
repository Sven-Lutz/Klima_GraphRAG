"""Bounded acquisition fixtures, no network and no research snapshot mutation."""

import hashlib
import json

import pytest
from test_pilot_release import technical_source

from scripts.research import targeted_sources


def test_append_only_targeted_source_to_federated_retrieval(tmp_path, monkeypatch):
    source = tmp_path / "parent"
    source.mkdir()
    manifest, _ = technical_source(source)
    original = json.loads(manifest.read_text())
    ledger = original["artifacts"]["ledger"]["path"]
    before = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    config = tmp_path / "targets.json"
    config.write_text(
        json.dumps(
            dict(
                allowed_hosts=["official.example.test"],
                sources=[
                    dict(
                        ags="09162111",
                        url="https://official.example.test/hitze",
                        official_source_basis="SYNTHETIC_TEST",
                    )
                ],
            )
        )
    )

    class Policy:
        def can_fetch(self, *args):
            return True

        def crawl_delay(self, *args):
            return 0

    monkeypatch.setattr(
        targeted_sources, "robots", lambda *args: (Policy(), (200, "url", {}, b"User-agent: *"))
    )
    monkeypatch.setattr(
        targeted_sources,
        "fetch",
        lambda session, url, hosts: (
            200,
            url,
            {"Content-Type": "text/html"},
            (
                b"<html><body><h1>Hitzeschutz</h1><p>Die TECHNICAL "
                b"Teststadt meldet ein Hitzewarnsystem. Kein Human Gold.</p></body></html>"
            ),
        ),
    )
    result = targeted_sources.acquire(manifest, config, tmp_path / "overlay")
    assert result["sources"][0]["status"] == "succeeded"
    assert result["units"] > 0
    assert hashlib.sha256(open(ledger, "rb").read()).hexdigest() == before
    from crawler.web.bavaria import create_bavaria_service
    from crawler.web.local_index import LocalIndex

    service = create_bavaria_service(tmp_path / "overlay/workspace.json")
    service.workspace.discovery_index = LocalIndex(service.workspace, tmp_path / "new-terms.sqlite")
    hits = service.workspace.discover("Hitzewarnsystem", ["09162111"])
    assert hits["results"]
    assert hits["snapshot_id"] != original["snapshot_id"]
    assert result["external_distribution"] == "BLOCKED"
    with pytest.raises(FileExistsError):
        targeted_sources.acquire(manifest, config, tmp_path / "overlay")


def test_target_url_bounds_fail_before_network(tmp_path):
    config = tmp_path / "targets.json"
    config.write_text(
        json.dumps(
            dict(
                allowed_hosts=["example.test"],
                sources=[
                    dict(
                        ags="09162111",
                        url="http://example.test/source",
                        official_source_basis="SYNTHETIC_TEST",
                    )
                ],
            )
        )
    )
    with pytest.raises(ValueError, match="HTTPS"):
        targeted_sources.acquire(tmp_path / "not-needed", config, tmp_path / "overlay")
