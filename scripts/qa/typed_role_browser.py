"""Actual Affing role-query/implementation-boundary regression, no model or human labels."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, output):
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Typed role QA requires loopback")
    output.mkdir(parents=True, exist_ok=False)
    errors = []
    question = (
        "Welche Organisation erstellt das Freiflächen-PV-Standortkonzept für Affing, "
        "und belegt die Passage bereits eine fertig gebaute Anlage?"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1000), accept_downloads=True)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("()=>state.status?.research_product")
        assert page.evaluate("state.status.research_product.answer_provider") == "local_extractive"
        page.locator('button[data-view="workbench"]').first.click()
        page.locator("#question").fill(question)
        page.locator("#mode").select_option("hybrid")
        label = page.evaluate(
            "()=>municipalityLabel(state.status.municipalities.find(m=>m.ags==='09771112'))"
        )
        page.locator("#municipality").fill(label)
        page.locator("details.advanced > summary").click()
        page.locator("#generate").check()
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy&&state.result?.answer_result?.answer_text")
        payload = page.evaluate("state.result")
        assert payload["answer_result"]["answer_text"].startswith(
            "Das Institut für Energietechnik IfE GmbH"
        )
        assert "finden Sie hier" not in payload["answer_result"]["answer_text"]
        assert "keine Fertigstellung" in page.locator("#answer-panel").inner_text()
        assert payload["answer_assessment"]["status"] == "PARTIAL_SOURCE_SYNTHESIS"
        page.get_by_text("Query-Plan und Methodentrace", exact=True).click()
        assert (
            "SOURCE_BOUND_TYPED_ROLE_SECOND_PASS_V1" in page.locator("#answer-panel").inner_text()
        )
        assert all(c["exact_span_supported"] for c in payload["answer_assessment"]["claims"])
        with page.expect_download() as downloaded:
            page.locator("#export-json").click()
        exported = output / "actual-affing-answer.json"
        downloaded.value.save_as(exported)
        saved = json.loads(exported.read_text())
        assert saved["answer_assessment"]["sentence_selection"]["navigation_rejections"]
        assert "implementation" in saved["query_plan"]["operations"]
        page.screenshot(path=str(output / "typed-role-and-open-implementation.png"), full_page=True)
        assert not errors, errors
        receipt = dict(
            schema="typed-role-browser-v1",
            result_id=payload["result_id"],
            checks=[
                "Actual Hybrid form request recovers source-bound commissioned organization",
                "Construction remains explicitly unresolved; pure document-link sentence omitted",
                "Visible trace and real JSON export preserve typed-role provenance and warnings",
            ],
            page_errors=errors,
            inference_invoked=False,
            human_gold_created=False,
            holdout_accessed=False,
            overall_product_acceptance=False,
        )
        (output / "REPORT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
        browser.close()
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
