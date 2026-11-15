"""Browser UX regression with explicitly synthetic delayed evaluation transport."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, output):
    if urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Loopback QA only")
    output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=dict(width=1280, height=900))
        errors, calls = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        pending = dict(
            status="EVALUATION_PENDING",
            run_id="synthetic-ui-only",
            poll_url="/api/evaluation/jobs/synthetic-ui-only",
        )
        summary = dict(succeeded=1, failed=0)

        def dispatch(route):
            calls.append(route.request.method)
            route.fulfill(status=202, json=pending)

        polls = []

        def poll(route):
            polls.append(route.request.method)
            if len(polls) == 1:
                route.fulfill(status=202, json=pending)
            else:
                route.fulfill(status=200, json=dict(run_id=pending["run_id"], summary=summary))

        page.route("**/api/evaluation/run", dispatch)
        page.route("**/api/evaluation/jobs/synthetic-ui-only", poll)
        page.goto(base + "/evaluation", wait_until="networkidle")
        page.locator('input[name="question"]').first.check()
        page.locator("#evaluation-run-button").click()
        page.wait_for_function(
            "() => document.querySelector('#evaluation-status').textContent"
            ".includes('arbeitet weiter')"
        )
        assert page.locator("#evaluation-run-button").is_disabled()
        page.screenshot(path=str(output / "pending.png"))
        page.wait_for_function(
            "() => document.querySelector('#evaluation-status').textContent"
            ".includes('Lauf beendet: 1 erfolgreich')",
            timeout=15000,
        )
        page.wait_for_function("() => !document.querySelector('#evaluation-run-button').disabled")
        assert calls == ["POST"] and polls == ["GET", "GET"], (calls, polls)
        page.screenshot(path=str(output / "completed.png"))
        assert not errors, errors
        receipt = dict(
            schema="evaluation-pending-browser-v1",
            synthetic_transport=True,
            actual_backend_execution=False,
            holdout_accessed=False,
            model_calls=0,
            human_judgments_created=0,
            post_count=len(calls),
            poll_count=len(polls),
            page_errors=errors,
            checks=[
                "Pending status remains readable and submit disabled",
                "Polling completes without redispatch; submit re-enabled",
            ],
            backend_evidence="tests/test_product_evaluation.py::test_evaluation_timeout_keeps_worker_lock_until_real_completion",
        )
        (output / "REPORT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
