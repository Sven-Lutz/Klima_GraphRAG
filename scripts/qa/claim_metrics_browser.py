"""Incremental claim-metric UI QA in a technical store, without model calls or holdout access."""

import argparse
import hashlib
import json
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, credentials, output):
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Technical QA requires loopback")
    output.mkdir(parents=True, exist_ok=False)
    password = next(
        line.removeprefix("password: ")
        for line in credentials.read_text().splitlines()
        if line.startswith("password: ")
    )
    suffix = secrets.token_hex(4)
    people = {
        role: ("technical_metric_" + role + "_" + suffix, secrets.token_urlsafe(20))
        for role in ["first", "second", "adjudicator"]
    }
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)

        def login(name, secret):
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/review", wait_until="networkidle")
            page.locator("#rp-username").fill(name)
            page.locator("#rp-password").fill(secret)
            page.locator("#rp-login button").click()
            page.locator("#rp-workspace").wait_for()
            return page

        def post(page, path, data):
            response = page.request.post(base + "/api/" + path, data=data)
            assert response.status == 200, response.text()
            return response.json()

        owner = login("owner", password)
        status = owner.request.get(base + "/api/review-protocol/status").json()
        assert status["technical"] is True
        for role, (name, secret) in people.items():
            post(
                owner,
                "review-protocol/accounts",
                dict(
                    username=name,
                    password=secret,
                    identity="SYNTHETIC claim metric QA " + name,
                    role="adjudicator" if role == "adjudicator" else "reviewer",
                ),
            )
        packet = owner.request.get(base + "/api/evaluation/annotation-packet?limit=1").json()
        material = packet["objects"][0]
        text = "SYNTHETIC claim metric transport fixture"
        answer = text + " [C1]."
        output_hash = hashlib.sha256(answer.encode()).hexdigest()
        material.update(kind="claim", key="synthetic-claim-metrics-" + suffix)
        material["payload"].update(
            proposed_text=answer,
            claim_id="synthetic-claim-" + suffix,
            output_sha256=output_hash,
            label="SYNTHETIC claim metric QA " + suffix,
        )
        oid = post(owner, "review-protocol/import", dict(objects=[material]))["object_ids"][0]
        post(
            owner,
            "review-protocol/assignment",
            dict(
                object_id=oid,
                reviewers=[people["first"][0], people["second"][0]],
                adjudicator=people["adjudicator"][0],
            ),
        )

        def open_object(page):
            page.locator("#rp-filter-q").fill("SYNTHETIC claim metric QA " + suffix)
            page.locator("#rp-filters button").click()
            page.locator("#rp-queue button").first.click()
            page.locator("#rp-object").wait_for()
            page.locator("#rp-claim-metrics").wait_for()

        for role, value in [("first", "true"), ("second", "false")]:
            page = login(*people[role])
            open_object(page)
            page.locator("#rp-decision").select_option("SUPPORTED")
            page.locator("#rp-comment").fill("SYNTHETIC UI test; no human factual judgment")
            page.locator("[data-metric=supported]").select_option(value)
            page.locator("[data-metric=citation_correct]").select_option("false")
            page.locator("#rp-inspected").check()
            page.locator("#rp-independent").check()
            page.locator("#rp-save").click()
            page.wait_for_function(
                "()=>document.getElementById('rp-message').textContent.includes('Entwurf')"
            )
            page.reload(wait_until="networkidle")
            open_object(page)
            assert page.locator("[data-metric=supported]").input_value() == value
            assert page.locator("[data-metric=temporal_valid]").input_value() == ""
            page.locator("#rp-submit").click()
            page.wait_for_function(
                "()=>document.getElementById('rp-object-state').textContent"
                ".includes('Eigene Prüfung abgegeben')"
            )
            page.context.close()
        assert (
            owner.request.get(base + "/api/review-protocol/object/" + oid).json()["review_state"]
            == "CONFLICT"
        )
        judge = login(*people["adjudicator"])
        open_object(judge)
        judge.locator("#rp-decision").select_option("SUPPORTED")
        judge.locator("#rp-comment").fill("SYNTHETIC adjudication of metric transport only")
        judge.locator("[data-metric=supported]").select_option("true")
        judge.locator("[data-metric=citation_correct]").select_option("false")
        judge.locator("#rp-inspected").check()
        judge.locator("#rp-submit").click()
        judge.wait_for_function(
            "()=>document.getElementById('rp-object-state').textContent.includes('Adjudiziert')"
        )
        gold = post(owner, "review-protocol/gold-export", dict(purpose="development"))
        assert gold["human_gold"] is False
        task = dict(
            question_id=material["task_id"],
            question=material["payload"]["question"],
            snapshot_id=status["snapshot_id"],
            split="human_candidates",
        )
        predicted = dict(
            ranked_ids=[],
            snapshot_id=status["snapshot_id"],
            query_sha256=hashlib.sha256(task["question"].encode()).hexdigest(),
            answer_text_sha256=output_hash,
            claims=[
                dict(
                    id=material["payload"]["claim_id"],
                    text=text,
                    source_ids=[source["unit_id"] for source in material["sources"]],
                )
            ],
        )
        calculation = subprocess.run(
            [
                str(Path.cwd() / ".venv/bin/python"),
                "-c",
                """
import json,sys
from research.product_evaluation.metrics import evaluate_case
from research.product_evaluation.review_bridge import reference_from_review_export
value=json.load(sys.stdin)
task=value['task']
ref=reference_from_review_export(value['gold'],[task],allow_technical=True,skip_other_tasks=True)[task['question_id']]
metrics=evaluate_case(value['predicted'],ref)
assert metrics['answer_support']['value']==1
assert metrics['citation_correctness']['value']==0
assert metrics['temporal_validity']['value'] is None
changed=evaluate_case({**value['predicted'],'answer_text_sha256':'changed'},ref)
assert changed['answer_support']['value'] is None
print(json.dumps(metrics))
""",
            ],
            input=json.dumps(dict(gold=gold, task=task, predicted=predicted)),
            text=True,
            capture_output=True,
            check=False,
        )
        assert calculation.returncode == 0, calculation.stderr
        metrics = json.loads(calculation.stdout)
        judge.screenshot(path=str(output / "claim-metrics-adjudicated.png"), full_page=True)
        assert not errors, errors
        result = dict(
            schema="claim-metrics-browser-v1",
            technical=True,
            human_gold=False,
            inference_invoked=False,
            holdout_accessed=False,
            object_id=oid,
            gold_version=gold["gold_version"],
            checks=[
                "Independent metric controls save/resume; unknown dimensions stay empty",
                "Metric conflict resolved by a distinct adjudicator",
                "Exact bound claim metrics calculate; changed answer does not inherit judgments",
            ],
            page_errors=errors,
            metrics=metrics,
        )
        (output / "REPORT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        browser.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
