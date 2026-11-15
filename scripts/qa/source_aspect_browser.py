"""Targeted real-browser content regression, no model inference or review judgments."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, output):
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Source QA requires private loopback")
    output.mkdir(parents=True, exist_ok=False)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=dict(width=1440, height=1000), accept_downloads=True)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("()=>state.status?.research_product")
        assert page.evaluate("state.status.research_product.answer_provider") == "local_extractive"
        page.locator('button[data-view="workbench"]').first.click()
        page.locator("#question").fill(
            "Wer plant den Windpark Brunn-Laaber, welcher Jahresertrag wird erwartet "
            "und handelt es sich dabei um eine Prognose?"
        )
        page.locator("#mode").select_option("graph")
        page.locator("details.advanced > summary").click()
        for control, ags in [("municipality", "09375122"), ("comparison-municipality", "09375162")]:
            value = page.evaluate(
                "ags=>municipalityLabel(state.status.municipalities.find(m=>m.ags===ags))", ags
            )
            page.locator("#" + control).fill(value)
        page.locator("#comparison-add").click()
        page.locator("#generate").check()
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy&&state.result?.answer_result?.answer_text")
        answer = page.locator("#answer-panel").inner_text()
        assert (
            "Orsted Onshore Deutschland GmbH" in answer
            and "etwa 34 GWh jährlich erwartet" in answer
        )
        # Exact summaries avoid matches in individual source inspectors.
        page.get_by_text(
            "3 Aussagen einzeln prüfen und zur Validierung übergeben", exact=True
        ).click()
        assert "Prognose oder Potenzial" in page.locator("#answer-panel").inner_text()
        page.get_by_text("Query-Plan und Methodentrace", exact=True).click()
        assert "source_bound_aspects_preserved" in page.locator("#answer-panel").inner_text()
        payload = page.evaluate("state.result")
        assert payload["answer_assessment"]["status"] == "PARTIAL_SOURCE_SYNTHESIS"
        assert all(c["exact_span_supported"] for c in payload["answer_assessment"]["claims"])
        with page.expect_download() as downloaded:
            page.locator("#export-json").click()
        exported = output / "actual-answer-export.json"
        downloaded.value.save_as(exported)
        saved = json.loads(exported.read_text())
        assert saved["answer_assessment"]["sentence_selection"]["source_bound_aspects_preserved"]
        with page.expect_download() as markdown_download:
            page.locator("#export-md").click()
        markdown = output / "actual-answer-export.md"
        markdown_download.value.save_as(markdown)
        assert "## Methodentrace" in markdown.read_text()
        assert payload["answer_result"]["generation"]["model"] in markdown.read_text()
        assert "## Aussagen und Prüfstatus" in markdown.read_text()
        page.screenshot(path=str(output / "answer-and-method-trace.png"), full_page=True)
        assert not errors, errors
        receipt = dict(
            schema="source-aspect-browser-v1",
            result_id=payload["result_id"],
            checks=[
                "Actual form request preserves original actor and projected annual generation",
                "Claim UI distinguishes projection from implementation and exposes selection trace",
                "Browser export retains exact spans and source-bound method metadata",
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
