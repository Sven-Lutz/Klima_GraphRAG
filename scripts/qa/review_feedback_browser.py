"""Incremental real-browser QA of automatic review-to-evaluation feedback only.

Requires a separate technical workspace; never creates real Human Gold or calls a model.
"""

import argparse
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, credentials, output):
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Technical feedback QA requires loopback")
    output.mkdir(parents=True, exist_ok=False)
    owner_password = next(
        s.removeprefix("password: ")
        for s in credentials.read_text().splitlines()
        if s.startswith("password: ")
    )
    suffix = secrets.token_hex(3)
    people = {
        role: ("technical_feedback_" + role + "_" + suffix, secrets.token_urlsafe(20))
        for role in ["first", "second", "adjudicator"]
    }
    checks, errors = [], []

    def record(text):
        checks.append(text)
        (output / "PROGRESS.json").write_text(
            json.dumps(
                dict(
                    checks=checks,
                    page_errors=errors,
                    status="IN_PROGRESS",
                    human_gold_created=False,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        contexts = []

        def login(username, password):
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            contexts.append(context)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/review", wait_until="networkidle")
            page.locator("#rp-username").fill(username)
            page.locator("#rp-password").fill(password)
            page.locator("#rp-login button").click()
            page.locator("#rp-workspace").wait_for()
            return page

        owner = login("owner", owner_password)
        status = owner.request.get(base + "/api/review-protocol/status").json()
        assert status["technical"] is True and status["automatic_feedback"] is True
        packet_response = owner.request.get(base + "/api/evaluation/annotation-packet?limit=1")
        assert packet_response.status == 200, packet_response.text()
        packet = packet_response.json()
        candidate = packet["objects"][0]
        qid = candidate["task_id"]
        assert candidate["split"] == "human_annotation_candidate"
        imported = owner.request.post(
            base + "/api/review-protocol/import", data={"objects": [candidate]}
        )
        assert imported.status == 200, imported.text()
        oid = imported.json()["object_ids"][0]
        record(
            "One actual source-bound candidate imported through authenticated API; "
            "Holdout untouched"
        )
        owner.locator("#rp-admin summary").first.click()
        for role, (name, password) in people.items():
            form = owner.locator("#rp-account")
            for field, value in [
                ("username", name),
                ("identity", "SYNTHETIC FEEDBACK TEST " + name),
                ("password", password),
            ]:
                form.locator("[name=" + field + "]").fill(value)
            form.locator("[name=role]").select_option(
                "adjudicator" if role == "adjudicator" else "reviewer"
            )
            form.locator("button").click()
            owner.wait_for_function(
                'name=>document.getElementById("rp-accounts").textContent.includes(name)', arg=name
            )
        owner.locator("#rp-filter-q").fill(qid)
        owner.locator("#rp-filters button").click()
        owner.locator("#rp-queue button").first.click()
        owner.locator("#rp-object").wait_for()
        assert owner.locator("#rp-quote mark").count() > 0
        assert (
            owner.locator("#rp-original-link")
            .get_attribute("href")
            .startswith("/api/review-protocol/original/")
        )
        owner.locator("#rp-first").select_option(people["first"][0])
        owner.locator("#rp-second").select_option(people["second"][0])
        owner.locator("#rp-adjudicator").select_option(people["adjudicator"][0])
        owner.locator("#rp-assign button").click()
        owner.wait_for_function(
            '()=>document.getElementById("rp-object-state").textContent'
            '.includes("Erst-/Zweitprüfung offen")'
        )
        for role in ["first", "second"]:
            reviewer = login(*people[role])
            reviewer.locator("#rp-queue button").first.click()
            reviewer.locator("#rp-object").wait_for()
            reviewer.locator("#rp-decision").select_option("SUPPORTED")
            reviewer.locator("#rp-comment").fill(
                "SYNTHETIC technical feedback transport test; not a real factual judgment."
            )
            reviewer.locator("#rp-inspected").check()
            reviewer.locator("#rp-independent").check()
            reviewer.locator("#rp-submit").click()
            reviewer.wait_for_function(
                '()=>document.getElementById("rp-object-state").textContent'
                '.includes("Eigene Prüfung abgegeben")'
            )
            reviewer.context.close()
        ready = owner.request.get(base + "/api/evaluation/review-feedback").json()
        assert ready["state"] == "READY" and ready["human_gold"] is False
        assert qid in ready["references"]["human_candidates"]["question_ids"]
        record(
            "Two isolated synthetic submissions automatically publish versioned references; "
            "no manual Gold import"
        )
        owner.goto(base + "/evaluation", wait_until="networkidle")
        owner.locator("#evaluation-split").select_option("human_candidates")
        pick = owner.locator(f'input[name=question][value="{qid}"]')
        pick.wait_for(state="attached")
        owner.wait_for_function(
            'id=>!document.querySelector(`input[name=question][value="${id}"]`).disabled', arg=qid
        )
        assert "kein Human Gold" in owner.locator("#automatic-reference-status").inner_text()
        pick.check()
        owner.locator("#evaluation-answer").uncheck()
        with owner.expect_response(
            lambda r: r.url.endswith("/api/evaluation/run") and r.request.method == "POST"
        ) as completed:
            owner.locator("#evaluation-run-button").click()
        response = completed.value
        assert response.status == 200, response.text()
        report = response.json()
        assert report["reference_id"] == ready["references"]["human_candidates"]["reference_id"]
        assert report["summary"]["configuration"]["method"].startswith("product-retrieval:")
        assert report["summary"]["independent_gold_available"] is False
        metric = next(m for m in report["summary"]["metrics"] if m["metric"] == "hit_at_k")
        assert metric["label_origin"] == "synthetic"
        owner.locator("#evaluation-runs details > summary").first.click()
        assert "synthetic" in owner.locator("#evaluation-runs").inner_text()
        record(
            "Annotated candidate becomes selectable; retrieval-only re-evaluation "
            "automatically uses exact versioned references"
        )
        owner.screenshot(path=str(output / "automatic-feedback-evaluation.png"), full_page=True)
        assert not errors, errors
        result = dict(
            schema="review-feedback-browser-v1",
            checks=checks,
            page_errors=errors,
            snapshot_id=status["snapshot_id"],
            object_id=oid,
            question_id=qid,
            gold_version=ready["gold_version"],
            run_id=report["run_id"],
            human_gold_created=False,
            inference_invoked=False,
            holdout_accessed=False,
        )
        (output / "REPORT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        browser.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
