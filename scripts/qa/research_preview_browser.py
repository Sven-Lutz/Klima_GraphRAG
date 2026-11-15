"""Real Chromium acceptance of a running loopback preview; no models or Gold writes."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def open_archive(context, page, base):
    link = page.locator("#inspector .archived-link")
    href = link.get_attribute("href")
    assert href.startswith("/api/source/")
    with context.expect_page(timeout=15000) as opened:
        link.click()
    original = opened.value
    original.wait_for_load_state("domcontentloaded", timeout=15000)
    assert original.url.startswith(base + "/api/source/")
    original.close()
    response = context.request.get(base + href.split("#")[0], timeout=20000)
    assert response.status == 200
    return response


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"browser": "", "checks": [], "screenshots": [], "independent_review": False}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        report["browser"] = browser.version
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle", timeout=30000)
        page.wait_for_function(
            "() => document.getElementById('frame-count').textContent.includes('2.056')"
        )
        report["checks"].append("Actual snapshot coverage: 2056 frames; no fake KPI")
        for width in [1440, 1024, 768, 390]:
            page.set_viewport_size({"width": width, "height": 1000})
            for mode in ["light", "dark"]:
                page.evaluate("(mode)=>theme(mode==='dark')", mode)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    width,
                    mode,
                )
                name = f"home-{width}-{mode}.png"
                page.screenshot(path=str(output / name), full_page=True)
                report["screenshots"].append(name)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("() => theme(false)")
        # Journey 1: actual municipality example -> inspector -> archived source -> export.
        start = time.monotonic()
        page.locator("#examples button").first.click()
        page.wait_for_function(
            "() => ['completed','empty','partial','timeout','error','unavailable']"
            ".includes(document.getElementById('query-status').dataset.state)",
            timeout=25000,
        )
        assert page.locator(".evidence-card").count() > 0, page.locator(
            "#query-status"
        ).inner_text()
        page.locator(".evidence-card button").first.click()
        assert (
            page.locator("#inspector").inner_text().find("Nicht unabhängig menschlich validiert")
            >= 0
        )
        response = open_archive(context, page, base)
        assert "sandbox" in response.headers.get("content-security-policy", "")
        report["checks"].append(
            {
                "journey": "Municipality -> passage -> hash-checked archive",
                "seconds": round(time.monotonic() - start, 2),
                "archive_type": response.headers.get("content-type"),
            }
        )
        export = context.request.get(base + page.locator("#export-json").get_attribute("href"))
        canonical = export.json()
        assert canonical.get("database_snapshot_id") or canonical.get("retrieval_result", {}).get(
            "database_snapshot_id"
        )
        report["checks"].append("Canonical JSON export retains snapshot")
        page.screenshot(path=str(output / "workbench-1440-light.png"), full_page=True)
        for width in [1024, 768, 390]:
            page.set_viewport_size({"width": width, "height": 1000})
            for dark in [False, True]:
                page.evaluate("dark => theme(dark)", dark)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                name = f"workbench-{width}-{'dark' if dark else 'light'}.png"
                page.screenshot(path=str(output / name), full_page=True)
                report["screenshots"].append(name)
        # Journey 2: independent mobile municipality -> archived source.
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("() => theme(false)")
        page.locator("nav [data-view=home]").click()
        page.locator("#examples button").nth(1).click()
        page.wait_for_selector(".evidence-card", timeout=25000)
        page.locator(".evidence-card button").first.click()
        assert page.locator("#inspector").evaluate("(n)=>n===document.activeElement")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        open_archive(context, page, base)
        page.screenshot(path=str(output / "inspector-390-light.png"), full_page=True)
        report["checks"].append("Mobile municipality -> original, inspector focus/reflow")
        # Journey 3: graph edges -> original evidence.
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator("#year").fill("2026")
        page.locator("nav [data-view=relations]").click()
        assert page.locator("#mode").is_disabled()
        assert page.locator("#year").is_disabled() and page.locator("#year").input_value() == "2026"
        page.locator("#relations button").first.click()
        page.wait_for_function(
            "() => document.getElementById('query-status').dataset.state!=='searching'",
            timeout=25000,
        )
        count = page.locator(".relation").count()
        assert count > 0, page.locator("#query-status").inner_text()
        page.locator(".relation button").first.click()
        assert "vorläufig" in page.locator("#inspector").inner_text()
        assert count == 2, "Explicitly named actor must not show thematic unrelated edges"
        open_archive(context, page, base)
        page.locator("#relations select").select_option("FUNDS")
        assert page.locator(".relation").count() == count

        page.screenshot(path=str(output / "relationships-1440-light.png"), full_page=True)
        report["checks"].append(
            {"journey": "Relation -> original evidence", "visible_relations": count}
        )
        graph_export = context.request.get(
            base + page.locator("#export-json").get_attribute("href")
        ).json()
        assert graph_export["search_scope"]["basis"] == "provisional_claim_scope"
        assert all(a["required_source_ids"] for x in graph_export["items"] for a in x["assertions"])
        page.locator("nav [data-view=workbench]").click()
        assert page.locator("#mode").input_value() == "lexical"
        assert (
            page.locator("#year").input_value() == "2026"
            and not page.locator("#year").is_disabled()
        )
        report["checks"].append(
            "Relations/workbench restores lexical filter; graph export retains context"
        )
        # Journey 4: text-year filter and original without a validity claim.
        page.locator("nav [data-view=home]").click()
        page.locator("#examples button").first.click()
        page.wait_for_selector(".evidence-card", timeout=25000)
        page.locator("#year").fill("2026")
        page.locator("#search").click()
        page.wait_for_selector("#query-status[data-state=partial]", timeout=25000)
        page.locator(".evidence-card button").first.click()
        open_archive(context, page, base)
        export = context.request.get(base + page.locator("#export-md").get_attribute("href"))
        assert export.ok and "keine Gültigkeitsprüfung" in export.text()
        report["checks"].append("Textjahr 2026 -> archive and Markdown retains temporal limit")
        page.locator("#municipality").fill("")
        page.locator("#question").fill("Wärmeplanung in Stammbach")
        page.locator("#year").fill("")
        page.locator("#search").click()
        page.wait_for_selector("#query-status[data-state=completed]", timeout=25000)
        assert "Aus Frage erkannt: Stammbach" in page.locator("#effective-scope").inner_text()
        report["checks"].append("Implicit municipality scope displayed from actual runtime")
        # Transparency, keyboard, and 200% effective layout.
        page.locator("nav [data-view=transparency]").click()
        assert (
            "Noch kein freigegebenes unabhängiges Human Gold"
            in page.locator("#transparency").inner_text()
        )
        page.set_viewport_size({"width": 720, "height": 500})
        page.screenshot(path=str(output / "transparency-effective-200-percent.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        report["checks"].append(
            "200% effective CSS viewport reflow; actual browser zoom NOT_TESTED"
        )
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.tagName") in [
            "BUTTON",
            "A",
            "SUMMARY",
            "INPUT",
            "TEXTAREA",
            "SELECT",
        ]
        # Journey 5: explicit empty, timeout, offline. Faults are synthetic technical only.
        page.locator("nav [data-view=workbench]").click()
        page.locator("#question").fill("Technische Fehlerzustandsprüfung – kein Forschungsurteil")

        def failure(route):
            route.fulfill(status=504, json={"error": {"code": "timeout"}})

        page.route("**/api/query", failure)
        page.locator("#search").click()
        page.wait_for_function(
            "() => document.getElementById('query-status').dataset.state==='timeout'"
        )
        assert page.locator(".evidence-card").count() == 0
        page.unroute("**/api/query", failure)
        context.set_offline(True)
        page.locator("#search").click()
        page.wait_for_function(
            "() => document.getElementById('query-status').dataset.state==='unavailable'"
        )
        context.set_offline(False)
        report["checks"].append("Synthetic timeout/offline: honest error and no stale evidence")
        # Visual regression: consecutive stable renders, exact bitmap equality.
        page.locator("nav [data-view=home]").click()
        a = page.screenshot()
        b = page.screenshot()
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
        report["checks"].append(
            "Stable-state visual repeatability: pixel-identical consecutive captures"
        )
        assert not errors, errors
        report["page_errors"] = errors
        report["screenreader"] = "NOT_TESTED"
        report["wcag_22_aa_conformance"] = "NOT_CLAIMED"
        browser.close()
    (output / "BROWSER_VALIDATION.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8800")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.output)
