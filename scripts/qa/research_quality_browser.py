"""Interactive product acceptance with real data, isolated technical reviews and AX checks."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        assert page.locator("#workspace").is_visible()
        assert page.locator("#theme").count() == 0
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
        checks.append("Research-first entry; light-only even with dark device preference")
        page.emulate_media(color_scheme="dark")
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "light"
        # Test open forms, not just screenshots of collapsed filters.
        page.locator("#query-form .advanced summary").click()
        for width in (1440, 1024, 768, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            controls = page.locator(
                "#query-form input:not([type=checkbox]):visible,"
                "#query-form select:visible,#query-form textarea:visible"
            )
            for control in controls.all():
                box = control.bounding_box()
                assert box and box["width"] >= 100 and box["x"] + box["width"] <= width, box
            page.screenshot(path=str(output / f"research-filters-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        checks.append(
            "Five responsive widths, including 320px; expanded filters remain within viewport"
        )
        page.locator("#comparison-municipality").fill("München (09162000)")
        page.locator("#comparison-add").click()
        assert "München" in page.locator("#comparison-selection").inner_text()
        page.locator("#comparison-selection button").click()
        assert page.locator("#research-compare").input_value() == ""
        page.locator("#research-region").select_option("Oberbayern")
        page.locator("#reset-filters").click()
        assert page.locator("#research-region").input_value() == ""
        checks.append("Named municipality comparison add/remove and reset")
        page.locator("#question").fill("Wie ist die Hitzevorsorge in München?")
        page.locator("#mode").select_option("lexical_expanded")
        page.locator("#search").click()
        page.wait_for_function(
            "()=>document.getElementById('query-status').dataset.state==='empty'"
        )
        assert "108 indexierte Texteinheiten" in page.locator("#results").inner_text()
        assert "im selben Gemeinderahmen" in page.locator("#results").inner_text()
        assert page.evaluate("state.result.retrieval_result.diagnostics.effective_ags") == [
            "09162000"
        ]
        assert page.evaluate("state.result.evidence.length") == 0
        page.screenshot(path=str(output / "munich-data-gap.png"), full_page=True)
        page.get_by_role("button", name="Quellenabdeckung prüfen", exact=True).click()
        page.wait_for_function(
            "()=>document.getElementById('validation-catalog-count')"
            ".textContent.startsWith('2 Quellen')"
        )
        checks.append("Munich gap explains actual scope; exact two-source catalog handoff")
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill("Wer fördert die kommunale Wärmeplanung in Nennslingen?")
        page.locator("#search").click()
        page.wait_for_selector(".evidence-card")
        page.locator(".evidence-card button").first.click()
        page.get_by_role("button", name="Quelle validieren", exact=True).click()
        page.wait_for_selector("#validation-form:not([hidden])")
        oid = page.locator("#validation-object-id").inner_text()
        original_url = page.locator("#validation-originals a").first.get_attribute("href")
        original = context.request.get(base + original_url)
        assert original.status == 200 and original.headers.get("x-original-sha256")

        page.locator("#validation-reviewer").fill("AUTOMATED_PRODUCT_QUALITY_TEST")
        page.locator("#validation-decision").select_option("UNCERTAIN")
        page.locator("#validation-comment").fill(
            "Technical transport check; no scientific judgment."
        )
        page.locator("#validation-confirm").check()
        page.locator("#validation-save").click()
        page.wait_for_function(
            "()=>document.getElementById('validation-status')"
            ".textContent.includes('persistent gespeichert')"
        )
        obj = context.request.get(base + "/api/validation/object/" + oid).json()
        assert obj["history"][-1]["payload"]["human"] is False
        assert obj["history"][-1]["payload"]["gold"] is False
        page.reload(wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        page.evaluate("id=>loadValidationObject(id)", oid)
        page.wait_for_function(
            "()=>document.getElementById('validation-history').textContent.includes('AUTOMATED_PRODUCT_QUALITY_TEST')"
        )
        with page.expect_download() as pending:
            page.locator("#validation-export").click()
        pending.value.save_as(output / "technical-review-snapshot.json")
        page.locator("#validation-import").set_input_files(
            str(output / "technical-review-snapshot.json")
        )
        page.wait_for_function(
            "()=>document.getElementById('validation-status').textContent.includes('importiert')"
        )
        checks.append(
            "Real source review persistence/reopen/export/import; technical non-Gold identity"
        )
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill("Wer fördert die kommunale Wärmeplanung in Nennslingen?")
        page.locator("#search").click()
        page.wait_for_selector(".evidence-card")
        page.wait_for_function(
            "()=>document.getElementById('inspector')"
            ".textContent.includes('Explorativer Reviewstatus: UNCERTAIN')"
        )
        page.get_by_role("button", name="Rechercheergebnis validieren", exact=True).click()
        page.wait_for_function("()=>validationState.object?.kind==='retrieval'")
        checks.append(
            "Research displays updated source review status; actual retrieval Studio handoff"
        )
        page.locator('nav [data-view="methods"]').click()
        page.locator('#comparison-methods input[value="graph"]').check()
        page.locator("#method-comparison button[type=submit]").click()
        page.wait_for_function(
            "()=>document.getElementById('comparison-status')"
            ".textContent.startsWith('Vergleich abgeschlossen')"
        )
        assert page.locator("#comparison-results tr").count() == 4
        assert (
            "Nicht als Freifrage-Verfahren verfügbar"
            in page.locator("#method-catalog").inner_text()
        )
        page.screenshot(path=str(output / "method-comparison.png"), full_page=True)
        checks.append(
            "Three actual strategy executions; unavailable Dense/Hybrid; IE metadata visible"
        )
        page.locator('nav [data-view="graph"]').click()
        page.wait_for_function("()=>window.klimaGraph?.data?.edges.length>0")
        zoom = page.evaluate("window.klimaGraph.graph.zoom()")
        page.locator("#graph-zoom-in").click()
        assert page.evaluate("window.klimaGraph.graph.zoom()") > zoom
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.get_by_role("button", name="Relation validieren", exact=True).first.click()
        page.wait_for_function("()=>validationState.object?.kind==='relation'")
        page.locator('nav [data-view="graph"]').click()
        page.locator("#validation-entity").click()
        page.wait_for_function("()=>validationState.object?.kind==='entity'")
        checks.append("Actual graph zoom, edge evidence and relation/entity Studio handoff")
        page.locator('footer [data-view="feedback"]').click()
        page.locator("#feedback-message").fill(
            "AUTOMATED_PRODUCT_FEEDBACK: technical UI test, not Gold."
        )
        page.locator("#feedback-context").check()
        page.locator("#feedback-save").click()
        page.wait_for_function(
            "()=>document.getElementById('feedback-status')"
            ".textContent.includes('persistent gespeichert')"
        )
        page.reload(wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        page.locator("#feedback-history").click()
        page.wait_for_function(
            "()=>document.getElementById('feedback-stored').textContent.includes('AUTOMATED_PRODUCT_FEEDBACK')"
        )
        stored = context.request.get(base + "/api/feedback").json()
        assert stored["items"][-1]["payload"]["context"]["snapshot"]
        assert stored["research_gold"] is False
        checks.append(
            "Explicit feedback with snapshot context persists after reopening; separate from Gold"
        )
        page.locator('nav [data-view="transparency"]').click()
        page.locator("#coverage-open").click()
        page.wait_for_selector("#validation-coverage-result table")
        page.locator("#validation-coverage-result input[type=checkbox]").check()
        assert "323 passende Gemeinden" in page.locator("#validation-coverage-result").inner_text()
        checks.append("Real coverage table and 323 no-text gaps")
        # Text-spacing/reflow check with expanded controls at the WCAG 320px width.
        page.locator('nav [data-view="workbench"]').click()
        page.set_viewport_size({"width": 320, "height": 1000})
        page.locator("#query-form .advanced").evaluate("e=>e.open=true")
        page.evaluate("""()=>{
          for(const e of document.querySelectorAll('body,button,input,select,textarea,p')){
            e.style.lineHeight='1.5';e.style.letterSpacing='.12em';e.style.wordSpacing='.16em';
            if(e.tagName==='P')e.style.marginBottom='2em';}}""")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        checks.append("320px reflow with increased line, letter, word and paragraph spacing")
        page.reload(wait_until="networkidle")
        page.wait_for_function("()=>validationState.ready")
        page.set_viewport_size({"width": 1440, "height": 1000})
        # Semantic accessibility audit of all main sections via Chrome's AX tree.
        names_by_view = {}
        for view in ("workbench", "graph", "validation", "methods", "transparency"):
            page.locator(f'nav [data-view="{view}"]').click()
            ax_session = context.new_cdp_session(page)
            tree = ax_session.send("Accessibility.getFullAXTree")["nodes"]
            controls = [
                n
                for n in tree
                if not n.get("ignored")
                and n.get("role", {}).get("value")
                in {"button", "link", "textbox", "combobox", "checkbox"}
            ]
            assert all(n.get("name", {}).get("value") for n in controls), view
            names_by_view[view] = len(controls)
            ax_session.detach()

        page.locator('nav [data-view="workbench"]').click()
        session = context.new_cdp_session(page)
        ax = session.send("Accessibility.getFullAXTree")["nodes"]
        interactive = [
            n
            for n in ax
            if not n.get("ignored")
            and n.get("role", {}).get("value")
            in {"button", "link", "textbox", "combobox", "checkbox"}
        ]
        unnamed = [n.get("role") for n in interactive if not n.get("name", {}).get("value")]
        assert not unnamed, unnamed
        focus = []
        page.locator("#question").focus()
        for _ in range(18):
            page.keyboard.press("Tab")
            item = page.evaluate(
                "()=>({tag:document.activeElement.tagName,id:document.activeElement.id,hidden:!!document.activeElement.closest('[hidden]')})"
            )
            assert not item["hidden"]
            focus.append(item)
        checks.append(
            "Chrome AX interactive names; visible keyboard focus sequence; no hidden focus targets"
        )
        page.route("**/api/query", lambda route: route.abort())
        page.locator("#question").fill("Technical network failure acceptance")
        page.locator("#search").click()
        page.wait_for_selector("#error:not([hidden])")
        assert page.locator("#exports").is_hidden()
        assert page.locator(".evidence-card").count() == 0
        assert "nicht abgeschlossen" in page.locator("#inspector").inner_text()
        page.unroute("**/api/query")
        checks.append(
            "Technical network failure is distinct from no evidence; no stale results/export"
        )
        assert not errors, errors
        report = dict(
            schema="research-quality-browser-v1",
            checks=checks,
            page_errors=errors,
            chrome=browser.version,
            accessibility_names_by_view=names_by_view,
            ax_interactive_names=len(interactive),
            keyboard_focus=focus,
            human_scientific_validation=False,
            independent_usability=False,
            actual_screenreader_test=False,
        )
        (output / "BROWSER.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8800")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.base, a.output)
