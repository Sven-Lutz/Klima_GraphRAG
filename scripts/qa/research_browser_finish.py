"""Continue recorded browser QA from durable real results; never repeat model inference.

The final receipt explicitly binds prior verified steps and this continuation.
"""

import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.qa.product_research_browser import finish_views, native_zoom


def run(base, checkpoint, output):
    prior = json.loads((checkpoint / "PROGRESS.json").read_text())
    assert prior["scientific_gold"] is False and not prior["page_errors"]
    assert len(prior["checks"]) >= 9, "Earlier required research/review steps not recorded"
    expected = json.loads((checkpoint / "answer-evidence-export.json").read_text())
    assert expected["answer_result"]["status"] == "answered"
    output.mkdir(parents=True, exist_ok=False)
    checks, errors = list(prior["checks"]), []

    def record(check):
        checks.append(check)
        (output / "PROGRESS.json").write_text(
            json.dumps(
                dict(
                    status="IN_PROGRESS",
                    checks=checks,
                    page_errors=errors,
                    scientific_gold=False,
                    prior_checkpoint=str(checkpoint),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            storage_state=str(checkpoint / "technical-browser-state.json"),
        )
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.locator("#persistent-research-list button").first.wait_for(state="attached")
        page.locator("#persistent-research > summary").click()
        page.get_by_role("button", name=expected["query_plan"]["original"], exact=True).click()
        page.wait_for_function("id=>state.result?.result_id===id", arg=expected["result_id"])
        page.locator("#answer-panel").wait_for(state="visible")
        assert (
            page.evaluate("state.result.answer_result.answer_text")
            == expected["answer_result"]["answer_text"]
        )
        record("Saved actual answer reopens asynchronously with identical content and citations")
        finish_views(page, context, base, record)
        assert not errors, errors
        cookies = context.cookies()
        version = browser.version
        browser.close()
        zoom = native_zoom(p, cookies, base, output, record)
    result = dict(
        schema="composed-research-browser-receipt-v1",
        checks=checks,
        page_errors=errors,
        browser=version,
        scientific_gold=False,
        prior_checkpoint=str(checkpoint),
        prior_receipt_sha256=hashlib.sha256(
            (checkpoint / "PROGRESS.json").read_bytes()
        ).hexdigest(),
        original_answer_sha256=hashlib.sha256(
            (checkpoint / "answer-evidence-export.json").read_bytes()
        ).hexdigest(),
        inference_repeated=False,
        **zoom,
    )
    (output / "REPORT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
