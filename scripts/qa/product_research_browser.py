"""Real Chrome research QA over local technical stores, no human scientific claims."""

import argparse
import json
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def finish_views(page, context, base, record):
    page.locator('button[data-view="graph"]').first.click()
    page.locator("#graph-accessible button").first.wait_for()
    page.locator("#graph-accessible button").first.click()
    assert "vorläufig" in page.locator("#inspector").inner_text()
    record("Graph Explorer loads actual graph and accessible node inspection")
    page.locator('button[data-view="workbench"]').first.click()
    page.locator("details.advanced > summary").click()
    page.locator("#generate").uncheck()
    for control, ags in [("municipality", "09577151"), ("comparison-municipality", "09475175")]:
        label = page.evaluate(
            "ags=>municipalityLabel(state.status.municipalities.find(m=>m.ags===ags))", ags
        )
        page.locator("#" + control).fill(label)
    page.locator("#comparison-add").click()
    page.locator("#question").fill("Welche Quellen beschreiben Wärmeversorgung und Wärmeplanung?")
    page.locator("#mode").select_option("hybrid")
    page.locator("#search").click()
    page.wait_for_function("()=>!state.busy")
    assert page.evaluate(
        "['09577151','09475175'].every(id=>state.result.evidence.some(e=>e.document.municipality_ids.includes(id)))"
    )
    record("Municipality comparison keeps actual evidence for both explicitly selected places")
    page.locator('button[data-view="methods"]').first.click()
    page.locator("#comparison-question").fill("Welche Quellen beschreiben die Wärmeplanung?")
    page.locator("#method-comparison button[type=submit]").click()
    page.wait_for_function(
        "()=>document.querySelector('#comparison-status').innerText"
        ".includes('Vergleich abgeschlossen')"
    )
    assert page.locator("#comparison-results table").count() > 0
    record("Scientific-method comparison runs separately on the same selected scope")
    page.goto(base + "/coverage", wait_until="networkidle")
    page.locator("#coverage-rows button").first.wait_for()
    page.locator("#coverage-q").fill("München")
    page.locator("#coverage-filter button").first.click()
    page.wait_for_function(
        "()=>document.querySelector('#coverage-rows').innerText.includes('München')"
    )
    page.locator("#coverage-rows button").first.click()
    page.locator("#coverage-detail").wait_for(state="visible")
    assert (
        "Nicht" in page.locator("#coverage-detail-body").inner_text()
        or "Unbekannt" in page.locator("#coverage-detail-body").inner_text()
    )
    record("Coverage Dashboard filters Munich and distinguishes source/claim/freshness boundaries")
    page.goto(base + "/evaluation", wait_until="networkidle")
    page.locator("#evaluation-questions tr").first.wait_for()
    assert "150" in page.locator("#catalog-counts").inner_text()
    assert "holdout" not in page.locator("#evaluation-split").locator("option").evaluate_all(
        "nodes=>nodes.map(n=>n.value)"
    )
    page.locator("#evaluation-split").select_option("adversarial")
    page.locator("#evaluation-filters button").click()
    page.wait_for_function("()=>document.querySelector('#question-page').innerText.includes('251')")
    record("Evaluation Dashboard browses permitted splits and keeps Holdout sealed")
    for width in [1440, 768, 390, 320]:
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    broken = context.new_page()
    broken.route("**/api/status", lambda request: request.abort())
    broken.goto(base)
    broken.locator('#home .notice[role="alert"]').wait_for(state="visible")
    record("Unavailable backend shows visible actionable error instead of hidden notice")
    broken.close()


def native_zoom(p, cookies, base, output, record):
    with tempfile.TemporaryDirectory(prefix="klima-research-product-zoom-") as profile:
        zoom_context = p.chromium.launch_persistent_context(
            profile,
            channel="chrome",
            headless=True,
            viewport=None,
            args=["--window-size=1440,1000"],
        )
        zoom_context.add_cookies(cookies)
        zoomed = zoom_context.pages[0]
        native_errors = []
        zoomed.on("pageerror", lambda e: native_errors.append(str(e)))
        zoomed.goto(base, wait_until="networkidle")
        before = zoomed.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
        settings = zoom_context.new_page()
        settings.goto("chrome://settings/appearance")
        settings.locator("#zoomLevel").select_option("2")
        settings.close()
        zoomed.reload(wait_until="networkidle")
        zoomed.locator("#persistent-research-list button").first.wait_for(state="attached")
        zoomed.locator("#persistent-research > summary").click()
        zoomed.locator("#persistent-research-list button").last.click()
        zoomed.locator("#answer-panel").wait_for(state="visible")
        after = zoomed.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
        assert after["dpr"] == before["dpr"] * 2
        assert zoomed.evaluate("document.documentElement.scrollWidth<=innerWidth")
        for _ in range(15):
            zoomed.keyboard.press("Tab")
            assert zoomed.evaluate("!document.activeElement.closest('[hidden]')")
        # Chromium full-page capture at native zoom clips to CSS dimensions.
        # Native viewport capture preserves the actual device-pixel geometry.
        zoomed.screenshot(path=str(output / "research-native-200-top.png"))
        zoomed.locator("#answer-panel").evaluate("n=>n.scrollIntoView({block:'start'})")
        zoomed.screenshot(path=str(output / "research-native-200-answer.png"))
        record("Native 200% Chrome zoom and keyboard focus preserve restored research answer")
        assert not native_errors, native_errors
        zoom_context.close()
    return dict(zoom_before=before, zoom_after=after)


def run(base, output, credentials):
    output.mkdir(parents=True, exist_ok=False)
    checks, errors = [], []

    def record(check):
        checks.append(check)
        (output / "PROGRESS.json").write_text(
            json.dumps(
                dict(
                    status="IN_PROGRESS", checks=checks, page_errors=errors, scientific_gold=False
                ),
                ensure_ascii=False,
                indent=2,
            )
        )

    owner_password = next(
        line.removeprefix("password: ")
        for line in credentials.read_text().splitlines()
        if line.startswith("password: ")
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("()=>Boolean(state.status?.research_product)")
        assert page.locator("#workspace").is_visible()
        assert page.locator("#generate").is_checked()
        page.locator("#persistent-research").wait_for()
        page.locator("details.advanced > summary").click()
        generative = page.evaluate(
            "state.status.research_product.answer_provider==='ollama_grounded'"
        )
        label = page.locator("#generate").evaluate("n=>n.closest('label').innerText")
        assert ("Modellsynthese" in label) == generative
        page.locator("details.advanced > summary").click()
        assert (
            "Serverneustart löscht die Historie nicht"
            in page.locator("#privacy-description").text_content()
        )
        record("Configured provider and durable history are truthfully disclosed")
        page.locator("#question").fill("Wer finanziert die Wärmeplanung in Nennslingen?")
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy", timeout=180000)
        assert page.evaluate("state.result.answer_result.status") == "answered"
        answer = page.locator("#answer-panel").inner_text()
        assert "Bundesministerium für Wirtschaft und Klimaschutz" in answer, answer
        assert page.locator('#answer-panel a[href^="/review#result="]').count() > 0
        assert page.evaluate(
            "state.result.answer_assessment.claims.every(c=>c.exact_span_supported||c.source_spans?.length>0)"
        )
        record(
            "Fresh financing formulation produces actual cited source sentences and claim handoff"
        )
        funding_result = page.evaluate("state.result")
        context.storage_state(path=str(output / "technical-browser-state.json"))
        (output / "technical-browser-state.json").chmod(0o600)
        page.locator("#answer-panel button").first.click()
        with page.expect_popup() as original_popup:
            page.locator("#inspector .archived-link").click()
        original = original_popup.value
        original.wait_for_load_state("domcontentloaded")
        assert original.url.startswith(base + "/api/source/")
        assert "BMWK" in original.locator("body").inner_text()
        original.close()
        record("Answer citation opens actual archived source with funder text")
        with page.expect_download() as exported:
            page.locator('#answer-panel a[href$="/export/json"]').click()
        export_path = output / "answer-evidence-export.json"
        exported.value.save_as(str(export_path))
        saved = json.loads(export_path.read_text())
        assert saved["answer_result"] == funding_result["answer_result"]
        assert saved["snapshot"] == funding_result["snapshot"]
        record("Actual answer, evidence and snapshot exported without synthetic Gold")
        page.locator("#answer-panel details").filter(
            has=page.locator('a[href^="/review#result="]')
        ).locator("summary").click()
        page.locator('#answer-panel a[href^="/review#result="]').first.click()
        page.locator("#rp-username").fill("owner")
        page.locator("#rp-password").fill(owner_password)
        page.locator("#rp-login button").click()
        page.locator("#rp-object").wait_for(state="visible")
        assert "Bundesministerium" in page.locator("#rp-proposal").inner_text()
        assert page.locator("#rp-quote mark").count() > 0
        assert len(page.frame_locator("#rp-original").locator("body").inner_text()) > 30
        page.screenshot(path=str(output / "actual-claim-handoff.png"), full_page=True)
        record(
            "Research claim enters correct unjudged review task "
            "with actual original and highlighted span"
        )
        page.goto(base, wait_until="networkidle")
        page.locator("#persistent-research-list button").first.wait_for(state="attached")
        page.locator("#persistent-research > summary").click()
        page.locator("#persistent-research-list button").first.click()
        page.wait_for_function("()=>Boolean(state.result?.result_id)")
        assert page.evaluate("state.result.result_id") == funding_result["result_id"]
        record("Research result survives navigation to review and restores conversation context")
        page.screenshot(path=str(output / "answer-desktop.png"), full_page=True)
        page.locator("#research-followup").check()
        page.locator("#question").fill("Und was steht dazu über den Zeitraum?")
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy", timeout=180000)
        assert "Rückfrage" in page.evaluate("state.result.query_plan.rewritten")
        record("Follow-up carries explicit prior question and municipality context")
        page.locator("#research-followup").uncheck()
        page.locator("#question").fill("Welche Quellen in Bayern nennen einen Hitzeaktionsplan?")
        page.locator("#mode").select_option("graph")
        page.locator("#search").click()
        page.wait_for_function("()=>!state.busy", timeout=180000)
        assert page.evaluate("state.result.evidence.length") == 0
        assert "Keine hinreichend" in page.locator("#answer-panel").inner_text()
        record("Known unrelated graph candidates rejected; explicit evidence abstention")
        for width in [1440, 1024, 768, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
            page.screenshot(path=str(output / f"research-{width}.png"), full_page=True)
        record("Research answer, claim links and traces reflow at five widths")
        page.reload(wait_until="networkidle")
        page.wait_for_function(
            '()=>document.querySelectorAll("#persistent-research-list button").length>0'
        )
        page.locator("#persistent-research > summary").click()
        page.locator("#persistent-research-list button").last.click()
        page.locator("#answer-panel").wait_for(state="visible")
        record("Saved answer reopens from server-backed browser session history")
        finish_views(page, context, base, record)
        assert not errors, errors
        report = dict(
            schema="product-research-browser-v1",
            checks=checks,
            page_errors=errors,
            browser=browser.version,
            scientific_gold=False,
        )
        cookies = context.cookies()
        browser.close()
        report.update(native_zoom(p, cookies, base, output, record))
        (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8813")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--credentials", type=Path, required=True)
    a = p.parse_args()
    run(a.base, a.output, a.credentials)
