"""Explicit local full-corpus composition; never used by external pilot factories."""

import json
from pathlib import Path

from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service
from crawler.web.coverage_analysis import attach_coverage_analysis
from crawler.web.evaluation_dashboard import attach_evaluation_dashboard
from crawler.web.local_index import LocalIndex
from crawler.web.product_research import attach_research_product
from crawler.web.semantic_index import SemanticIndex
from crawler.web.validation import attach_validation


def create_local_product(
    manifest, directory, *, technical=False, evaluation_catalog=None, generation_config=None
):
    service = create_bavaria_service(manifest)
    if len(service.workspace.municipalities) != 2056:
        raise ValueError("Local research product requires the full 2056-municipality Bavaria frame")
    index = LocalIndex(service.workspace, directory / "terms-v1.sqlite")
    service.workspace.discovery_index = index
    app = create_app(service)
    attach_validation(
        app,
        service,
        index,
        directory / ("validation-technical" if technical else "validation"),
        technical,
    )
    semantic = SemanticIndex(service.workspace, directory / "semantic-v1")
    attach_research_product(
        app,
        service,
        directory / ("research-technical" if technical else "research"),
        semantic,
        generation_config,
    )
    root = Path(__file__).resolve().parents[2]
    runtime = directory / "product-runtime.json"
    config = json.loads(runtime.read_text()) if runtime.exists() else {}
    catalog = Path(
        evaluation_catalog
        or config.get("evaluation_catalog")
        or root / "generated/product-evaluation-v3"
    )
    from crawler.web.review_feedback import ReviewFeedback

    app.state.review_feedback = ReviewFeedback(
        app.state.review_protocol, catalog if (catalog / "manifest.json").exists() else None
    )
    attach_evaluation_dashboard(app, service, catalog)
    attach_coverage_analysis(app, service, directory / "coverage")
    return app
