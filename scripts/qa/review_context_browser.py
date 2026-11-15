"""Focused context-tab UX using existing sources and an explicit read-only UI fixture."""

import argparse
import json
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import sync_playwright


def run(base, credentials, output):
    if urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Loopback QA only")
    output.mkdir(parents=True, exist_ok=False)
    password = next(
        line.removeprefix("password: ")
        for line in credentials.read_text().splitlines()
        if line.startswith("password: ")
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=dict(width=1280, height=900))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/review", wait_until="networkidle")
        page.locator("#rp-username").fill("owner")
        page.locator("#rp-password").fill(password)
        page.locator("#rp-login button").click()
        page.locator("#rp-workspace").wait_for()
        assert page.request.get(base + "/api/review-protocol/status").json()["technical"]
        rows = page.request.get(base + "/api/review-protocol/queue?limit=50").json()["items"]
        material = []
        for index, row in enumerate(rows):
            if row["split"] == "independent_holdout":
                continue
            obj = page.request.get(base + "/api/review-protocol/object/" + row["object_id"]).json()
            if obj["sources"] and obj["sources"][0].get("unit_id"):
                material.append((index, obj))
            if (
                len(material) >= 2
                and material[0][1]["sources"][0]["unit_id"]
                != material[-1][1]["sources"][0]["unit_id"]
            ):
                break
        index, obj = material[0]
        source, context = obj["sources"][0], material[-1][1]["sources"][0]
        assert source["unit_id"] != context["unit_id"]
        obj["sources"] = [source, context]
        obj["payload"].update(
            cited_source_ids=[source["unit_id"]], context_source_ids=[context["unit_id"]]
        )
        page.route(
            "**/api/review-protocol/object/" + quote(obj["object_id"], safe=""),
            lambda route: route.fulfill(json=obj),
        )
        page.locator("#rp-queue button").nth(index).click()
        tab = page.locator("#rp-source-tabs button").filter(has_text="Pflichtkontext")
        tab.wait_for()
        tab.click()
        assert context["unit_id"] in page.locator("#rp-locator").inner_text()
        assert page.locator("#rp-quote mark").inner_text() == context["quote"]
        assert (
            page.locator("#rp-original-link")
            .get_attribute("href")
            .startswith(context["original_url"])
        )
        page.screenshot(path=str(output / "required-context.png"), full_page=True)
        assert not errors, errors
        receipt = dict(
            schema="review-context-browser-v1",
            technical=True,
            synthetic_read_only_object_projection=True,
            actual_graph_relation_verified=False,
            backend_handoff_evidence="tests/test_review_protocol.py::test_claim_handoff_preserves_required_graph_context_separately",
            checks=[
                "Required context has separate visible tab",
                "Context tab displays exact passage highlight, unit locator and original link",
            ],
            new_reviews_created=False,
            model_calls=0,
            holdout_accessed=False,
            page_errors=errors,
        )
        (output / "REPORT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
