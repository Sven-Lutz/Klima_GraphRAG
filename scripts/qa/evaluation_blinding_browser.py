"""Check existing technical evaluation reports without creating any judgments."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

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
        owner = browser.new_page()
        errors = []
        owner.on("pageerror", lambda error: errors.append(str(error)))
        owner.goto(base + "/review", wait_until="networkidle")
        owner.locator("#rp-username").fill("owner")
        owner.locator("#rp-password").fill(password)
        owner.locator("#rp-login button").click()
        owner.locator("#rp-workspace").wait_for()
        assert owner.request.get(base + "/api/review-protocol/status").json()["technical"]
        reports = owner.request.get(base + "/api/evaluation/runs").json()["items"]
        empty_reference = hashlib.sha256(b"{}").hexdigest()
        private = [
            r
            for r in reports
            if r["summary"]["configuration"].get("reference_sha256") != empty_reference
        ]
        assert private, "Existing synthetic consolidated evaluation required"
        run_id = private[0]["run_id"]
        assert owner.request.get(base + "/api/evaluation/export/" + run_id).status == 200
        owner.goto(base + "/evaluation", wait_until="networkidle")
        assert owner.locator("#evaluation-runs details").count() == len(reports)
        anonymous = browser.new_page()
        anonymous.on("pageerror", lambda error: errors.append(str(error)))
        assert anonymous.request.get(base + "/api/evaluation/export/" + run_id).status == 403
        public = anonymous.request.get(base + "/api/evaluation/runs").json()["items"]
        assert not {r["run_id"] for r in public} & {r["run_id"] for r in private}
        anonymous.goto(base + "/evaluation", wait_until="networkidle")
        assert anonymous.locator("#evaluation-runs details").count() == len(public)
        anonymous.screenshot(path=str(output / "anonymous-evaluation.png"), full_page=True)
        assert not errors, errors
        receipt = dict(
            schema="evaluation-blinding-browser-v1",
            technical=True,
            checks=[
                "Operator can inspect existing synthetic reference reports",
                "Anonymous report list and actual dashboard exclude reviewed reports",
                "Anonymous direct export is forbidden",
            ],
            page_errors=errors,
            new_reviews_created=False,
            model_calls=0,
            holdout_accessed=False,
            real_human_gold=False,
        )
        (output / "REPORT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
