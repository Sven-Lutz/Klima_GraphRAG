"""Real source/answer to private review-metric observation; synthetic reviewers only."""

import argparse
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(base, credentials, output, layout_only=False):
    if urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Technical QA requires loopback")
    output.mkdir(parents=True, exist_ok=False)
    password = next(
        line.removeprefix("password: ")
        for line in credentials.read_text().splitlines()
        if line.startswith("password: ")
    )
    suffix = secrets.token_hex(4)
    people = {
        role: ("technical_observation_" + role + "_" + suffix, secrets.token_urlsafe(20))
        for role in ["first", "second", "judge"]
    }
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)

        def login(name, secret):
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
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
        if layout_only:
            owner.goto(base + "/evaluation", wait_until="networkidle")
            owner.locator("#observation-results .observation-metrics").wait_for()
            widths = []
            for width in [1280, 640, 390, 320]:
                owner.set_viewport_size(dict(width=width, height=900))
                owner.locator("#review-observations").scroll_into_view_if_needed()
                row = owner.evaluate("""()=>({width:innerWidth,
                    page:document.documentElement.scrollWidth,
                    content:document.querySelector('#observation-results .observation-metrics')
                        .getBoundingClientRect().width})""")
                assert row["page"] <= row["width"], row
                widths.append(row)
                owner.screenshot(path=str(output / (str(width) + ".png")))
            assert not errors, errors
            receipt = dict(
                schema="research-observation-layout-v1",
                technical=True,
                widths=widths,
                page_errors=errors,
                inference_invoked=False,
                new_reviews_created=False,
            )
            (output / "REPORT.json").write_text(json.dumps(receipt, indent=2) + "\n")
            browser.close()
            print(json.dumps(receipt))
            return
        answer = post(
            owner,
            "research/ask",
            dict(
                question="Welche Wärmeplanung wird für Nennslingen dokumentiert?",
                mode="lexical_expanded",
                generate_answer=True,
            ),
        )
        assert answer["answer_result"]["generation"]["provider"] in {
            "local-extractive",
            "local_extractive",
        }
        assert answer["answer_assessment"]["claims"]
        handoff = post(
            owner,
            "review-protocol/handoff",
            dict(result_id=answer["result_id"], kind="claim", claim_index=0),
        )
        oid = handoff["object_id"]
        obj = owner.request.get(base + "/api/review-protocol/object/" + oid).json()
        observation_id = obj["payload"]["review_observation_id"]
        assert obj["sources"][0]["quote"]
        for role, (name, secret) in people.items():
            post(
                owner,
                "review-protocol/accounts",
                dict(
                    username=name,
                    password=secret,
                    identity="SYNTHETIC observation QA " + name,
                    role="adjudicator" if role == "judge" else "reviewer",
                ),
            )
        post(
            owner,
            "review-protocol/assignment",
            dict(
                object_id=oid,
                reviewers=[people["first"][0], people["second"][0]],
                adjudicator=people["judge"][0],
            ),
        )
        for role in ["first", "second"]:
            reviewer = login(*people[role])
            reviewer.locator("#rp-queue button").first.click()
            reviewer.locator("#rp-claim-metrics").wait_for()
            assert reviewer.locator("#rp-quote mark").count() > 0
            reviewer.locator("#rp-decision").select_option("SUPPORTED")
            reviewer.locator("#rp-comment").fill(
                "SYNTHETIC workflow fixture only; no independent human factual judgment"
            )
            reviewer.locator("[data-metric=supported]").select_option("true")
            reviewer.locator("[data-metric=citation_correct]").select_option("false")
            reviewer.locator("#rp-inspected").check()
            reviewer.locator("#rp-independent").check()
            reviewer.locator("#rp-submit").click()
            reviewer.wait_for_function(
                "()=>document.getElementById('rp-object-state').textContent"
                ".includes('Eigene Prüfung abgegeben')"
            )
            assert reviewer.request.get(base + "/api/evaluation/review-observations").status == 403
            reviewer.context.close()
        response = owner.request.get(base + "/api/evaluation/review-observations")
        assert response.status == 200, response.text()
        report = response.json()
        row = next(r for r in report["results"] if r["observation_id"] == observation_id)
        assert report["technical"] is True and report["human_gold"] is False
        assert report["catalog_modified"] is False and report["inference_invoked"] is False
        assert row["metrics"]["answer_support"]["value"] == 1
        assert row["metrics"]["citation_correctness"]["value"] == 0
        assert row["metrics"]["temporal_validity"]["value"] is None
        owner.goto(base + "/evaluation", wait_until="networkidle")
        owner.locator("#review-observations").wait_for()
        owner.wait_for_function(
            "()=>document.getElementById('observation-status').textContent"
            ".includes('kein Human Gold')"
        )
        assert "Nennslingen" in owner.locator("#observation-results").inner_text()
        assert "Nicht bewertet" in owner.locator("#observation-results").inner_text()
        with owner.expect_download() as downloaded:
            owner.locator("#observation-export").click()
        exported = output / "reviewed-observations.json"
        downloaded.value.save_as(exported)
        downloaded_report = json.loads(exported.read_text())
        assert downloaded_report["source_gold_sha256"] == report["source_gold_sha256"]
        owner.screenshot(path=str(output / "reviewed-research-evaluation.png"), full_page=True)
        assert not errors, errors
        receipt = dict(
            schema="research-observations-browser-v1",
            technical=True,
            human_gold_created=False,
            inference_invoked=False,
            holdout_accessed=False,
            observation_id=observation_id,
            result_id=answer["result_id"],
            object_id=oid,
            source_gold_sha256=report["source_gold_sha256"],
            checks=[
                "Actual research answer and source captured through session-owned handoff",
                "Two synthetic reviewers create claim feedback without catalog import",
                "Private UI shows rated/unknown dimensions; export binds the same Gold snapshot",
            ],
            page_errors=errors,
        )
        (output / "REPORT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
        browser.close()
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8813")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout-only", action="store_true")
    run(**vars(parser.parse_args()))
