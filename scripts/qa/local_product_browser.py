"""Real local browser acceptance; all automated reviews are technical, never human gold."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        assert "2.056" in page.locator("#frame-count").inner_text()
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill(
            "Welche Förderung nennt Nennslingen für die kommunale Wärmeplanung?"
        )
        page.locator("#search").click()
        page.wait_for_selector(".evidence-card")
        page.locator(".evidence-card button").first.click()
        page.get_by_role("button", name="Quelle validieren", exact=True).click()
        page.wait_for_selector("#validation-form:not([hidden])")
        oid = page.locator("#validation-object-id").inner_text()
        assert oid.startswith("review-object:")
        page.locator("#validation-reviewer").fill("AUTOMATED_BROWSER_ACCEPTANCE")
        page.locator("#validation-decision").select_option("UNCERTAIN")
        page.locator("#validation-comment").fill(
            "Technical browser workflow only; no substantive scientific judgment."
        )
        page.locator("#validation-confirm").check()
        page.locator("#validation-save").click()
        page.wait_for_function(
            "()=>$('validation-status').textContent.includes('persistent gespeichert')"
        )
        saved = context.request.get(base + "/api/validation/object/" + oid).json()
        assert saved["history"][-1]["payload"]["human"] is False
        assert saved["history"][-1]["payload"]["gold"] is False
        page.reload(wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        page.locator("#validation-nav").click()
        page.locator("#validation-queue").get_by_role(
            "button",
            name=__import__("re").compile(
                __import__("re").escape(saved["payload"].get("url", saved["key"])[:80])
            ),
        ).click()
        page.wait_for_function(
            "()=>document.getElementById('validation-history').textContent.includes('AUTOMATED_BROWSER_ACCEPTANCE')"
        )
        # Source catalog with type and region; no fixed packet selection.
        page.locator("#validation-catalog-details > summary").click()
        page.locator("#validation-catalog-region").select_option("Oberfranken")
        page.locator("#validation-catalog-kind").select_option("pdf")
        page.locator("#validation-catalog-form button").click()
        page.wait_for_function(
            "()=>document.getElementById('validation-catalog-count').textContent.includes('Quellen')"
        )
        with page.expect_download() as pending:
            page.locator("#validation-export").click()
        download = pending.value
        download.save_as(output / "own-review-snapshot.json")
        page.locator("#validation-import").set_input_files(str(output / "own-review-snapshot.json"))
        page.wait_for_function(
            "()=>document.getElementById('validation-status').textContent.includes('importiert')"
        )
        page.locator("#validation-coverage").click()
        page.wait_for_selector("#validation-coverage-result table")
        page.locator("#validation-coverage-result input[type=checkbox]").check()
        page.wait_for_function(
            "()=>document.getElementById('validation-coverage-result')"
            ".textContent.includes('323 passende Gemeinden')"
        )
        page.locator("#validation-coverage-result").locator("..").locator("summary").first.click()
        page.locator('nav [data-view="validation"]').click()
        screenshots = []
        for width in (1440, 1024, 768, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            for dark in (False,):
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                name = f"studio-{width}-{dark}.png"
                page.screenshot(path=str(output / name), full_page=True)
                screenshots.append(name)
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill(
            "Welche Förderung nennt Nennslingen für die kommunale Wärmeplanung?"
        )
        page.locator("#search").click()
        page.wait_for_selector(".evidence-card")
        page.wait_for_function(
            "()=>$('inspector').textContent.includes('Explorativer Reviewstatus: UNCERTAIN')"
        )
        page.get_by_role("button", name="Rechercheergebnis validieren", exact=True).click()
        page.wait_for_function("()=>validationState.object?.kind==='retrieval'")
        page.locator('nav [data-view="graph"]').click()
        page.wait_for_function("()=>window.klimaGraph?.data?.edges?.length>0")
        page.locator("#validation-entity").click()
        page.wait_for_function("()=>validationState.object?.kind==='entity'")
        page.locator('nav [data-view="graph"]').click()
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.get_by_role("button", name="Relation validieren", exact=True).first.click()
        page.wait_for_function("()=>validationState.object?.kind==='relation'")
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill("qxzunfindable9861")
        page.locator("#search").click()
        page.wait_for_function(
            "()=>document.getElementById('query-status').dataset.state==='empty'"
        )
        exported = context.request.get(
            base + page.locator("#export-json").get_attribute("href")
        ).json()
        assert exported["query"] == "qxzunfindable9861"
        assert exported["items"] == []
        assert "Keine Belegstelle" in page.locator("#inspector").inner_text()
        page.get_by_role("button", name="Rechercheergebnis validieren", exact=True).click()
        page.wait_for_function("()=>validationState.object?.payload?.query==='qxzunfindable9861'")
        assert page.evaluate("validationState.object.payload.items.length") == 0
        assert not errors, errors
        report = dict(
            schema="local-product-browser-v1",
            browser=browser.version,
            workflow_steps_1_to_12="PASS",
            source_handoff=True,
            reopen_history=True,
            research_review_status_updated=True,
            retrieval_entity_relation_handoff=True,
            export_import=True,
            viewport_themes=4,
            page_errors=errors,
            automated_reviews_human=False,
            gold=False,
            independent_usability=False,
            empty_state_no_stale_export=True,
            empty_result_validation_handoff=True,
            coverage_gap_table_323=True,
            screenshots=screenshots,
        )
        (output / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    run(a.base, a.output.resolve())
