"""Coverage proxies cannot become policy scores or unchecked publication dates."""

import json

import pytest
from fastapi.testclient import TestClient
from test_local_validation import product

from crawler.web.coverage_analysis import CoverageAnalysis, attach_coverage_analysis


def test_coverage_cache_proxies_and_csv_remain_source_bound(tmp_path):
    app, index = product(tmp_path)
    service = app.state.research_service
    coverage = CoverageAnalysis(service, tmp_path / "coverage")
    report = coverage.report()
    assert report["summary"]["municipalities"] == 1
    assert report["summary"]["text_units"] == 1
    row = report["municipalities"][0]
    assert row["policy_activity"] == "NOT_MEASURED"
    assert row["scientific_validation"] == "NOT_JUDGED"
    assert "UNKNOWN" in row["publication_freshness"]
    assert "NOT_PUBLICATION_DATES" in row["publication_freshness"]
    assert report["summary"]["orphan_associations"] == 0
    again = CoverageAnalysis(service, tmp_path / "coverage")
    assert again.report()["content_sha256"] == report["content_sha256"]
    assert service.database_snapshot_id in coverage.csv()
    assert coverage.query(topic="heat")["total"] == 0
    with pytest.raises(ValueError):
        coverage.query(limit=201)
    changed = json.loads(coverage.path.read_text())
    changed["summary"]["municipalities"] = 99
    coverage.path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="integrity"):
        CoverageAnalysis(service, tmp_path / "coverage").report()


def test_readonly_http_coverage_and_unknown_municipality(tmp_path):
    app, index = product(tmp_path)
    attach_coverage_analysis(app, app.state.research_service, tmp_path / "coverage")
    c = TestClient(app)
    assert c.get("/coverage").status_code == 200
    r = c.get("/api/coverage-analysis")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert c.get("/api/coverage-analysis/municipality/00000000").status_code == 422
    assert (
        "bavaria-coverage.csv"
        in c.get("/api/coverage-analysis/export.csv").headers["content-disposition"]
    )
