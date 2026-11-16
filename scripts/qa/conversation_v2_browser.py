"""Synthetic V2 browser acceptance. No human UX or answer accuracy inference."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8825")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    with sync_playwright() as runtime:
        with runtime.chromium.launch(channel="chrome", headless=True) as browser:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(args.base)
            page.locator("#dataset-notice").filter(has_text="Synthetische").wait_for()
            page.locator("#question").fill("Wer plant die Solaranlage?")
            page.locator("#question").press("Enter")
            page.locator("#turns article").wait_for()
            original = page.evaluate("turns[0].claims[0].text")
            page.evaluate("""() => {
              turns[0].claims[0].text = '# Test\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |\\n\\n' +
                '<script>window.injected=true</script> [bad](javascript:alert(1))';
              render();
            }""")
            assert page.locator("#turns h1").count() == 1
            assert page.locator("#turns table").count() == 1
            assert page.locator("#turns script, #turns a[href^='javascript:']").count() == 0
            assert page.evaluate("window.injected === undefined")
            page.evaluate("text => { turns[0].claims[0].text = text; render(); }", original)
            receipts.append({"check": "markdown_headings_tables_xss_protocols", "status": "PASS"})
            page.get_by_role("button", name="Graph öffnen", exact=True).click()
            page.locator("#workspace-content table tbody tr").first.wait_for()
            graph_url = page.url
            assert "EXTRACTED_UNVERIFIED" in page.locator("#workspace-content").inner_text()
            page.get_by_label("Knoten suchen").fill("no-such-node")
            assert "0 Knoten" in page.locator("#workspace-content").inner_text()
            page.get_by_label("Knoten suchen").fill("")
            page.locator("#workspace-content button").first.click()
            page.locator("#evidence-dialog").wait_for(state="visible")
            page.keyboard.press("Escape")
            receipts.append({"check": "graph_filter_table_evidence", "status": "PASS"})
            for route in [
                "/graph",
                "/visualizations",
                "/settings",
                "/coverage",
                "/validation",
                "/",
            ]:
                page.goto(args.base + route)
                if route == "/validation":
                    page.locator("#review-demo").wait_for(state="visible")
                elif route != "/":
                    page.locator("#workspace-content").wait_for()
                    page.wait_for_timeout(150)
                else:
                    page.locator("#dataset-notice").filter(has_text="Synthetische").wait_for()
                for width in [320, 360, 375, 768, 1024, 1440]:
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    ), (
                        route,
                        width,
                    )
                    if route == "/":
                        page.locator("#question").focus()
                        box = page.locator("#question").bounding_box()
                        assert box["y"] >= 0 and box["y"] + box["height"] <= 1000
                    if width in [320, 768, 1440]:
                        page.screenshot(
                            path=str(args.output / (route.strip("/") or "chat")) + f"-{width}.png",
                            full_page=True,
                        )
                    receipts.append(
                        {
                            "check": "viewport_overflow",
                            "route": route,
                            "width": width,
                            "status": "PASS",
                        }
                    )
            page.goto(args.base)
            page.set_viewport_size({"width": 320, "height": 568})
            page.locator("#question").focus()
            box = page.locator("#send").bounding_box()
            assert box["y"] >= 0 and box["y"] + box["height"] <= 568
            page.screenshot(path=str(args.output / "chat-320-short.png"), full_page=True)
            receipts.append({"check": "small_height_composer", "status": "PASS"})
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(args.base + "/visualizations")
            page.get_by_role("button", name="Datensatz exportieren").wait_for()
            assert "20" in page.locator("#workspace-content table").inner_text()
            with page.expect_download() as download:
                page.get_by_role("button", name="Datensatz exportieren").click()
            download.value.save_as(args.output / "visualization.json")
            spec = json.loads((args.output / "visualization.json").read_text())
            assert spec["rows"][0]["value"] == 20 and spec["human_validated"] is False
            page.goto(args.base + "/validation")
            page.get_by_role("button", name="Demo-Prüfkonto A", exact=True).click()
            page.locator("#review-queue button").first.click()
            page.evaluate("""() => {
              window.originalLayoutFetch = window.fetch;
              window.fetch = (...args) => String(args[0]).endsWith('/original') ?
                new Promise(resolve => { window.releaseOldOriginal = async () => {
                  const response = await window.originalLayoutFetch(...args);
                  await response.clone().text(); resolve(response);
                  await new Promise(requestAnimationFrame);
                }; }) : window.originalLayoutFetch(...args);
            }""")
            page.locator("#review-open-original").click()
            page.wait_for_function("typeof window.releaseOldOriginal === 'function'")
            first_title = page.locator("#review-title").inner_text()
            page.locator("#review-queue button").nth(1).click()
            page.wait_for_function(
                "old => document.querySelector('#review-title').textContent !== old",
                arg=first_title,
            )
            page.evaluate("() => window.releaseOldOriginal()")
            assert "noch nicht geöffnet" in page.locator("#review-original").inner_text()
            page.evaluate("window.fetch = window.originalLayoutFetch")
            page.locator("#review-queue button").first.click()
            page.locator("#review-open-original").click()
            page.locator("#review-original").filter(has_text="20 kWp").wait_for()
            page.locator("#review-history").evaluate("e => e.parentElement.open = true")
            page.set_viewport_size({"width": 320, "height": 1000})
            page.screenshot(path=str(args.output / "review-original-320.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(path=str(args.output / "review-case-1440.png"), full_page=True)
            receipts.append({"check": "stale_original_response_is_ignored", "status": "PASS"})
            page.locator("#review-comment").fill(
                "Synthetic browser draft; no independent judgment."
            )
            page.get_by_text("Entwurf gespeichert", exact=False).wait_for()
            page.get_by_role("button", name="Demo-Prüfkonto B", exact=True).click()
            page.locator("#review-queue button").first.click()
            assert page.locator("#review-comment").input_value() == ""
            page.goto(graph_url)
            page.locator("#workspace-content table tbody tr").first.wait_for()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Aktuellen Chat löschen").click()
            page.get_by_text("Noch keine Antwort vorhanden.", exact=False).wait_for()
            assert page.locator("#workspace-graph canvas").count() == 0
            receipts.append({"check": "deleted_chat_clears_graph", "status": "PASS"})
            assert errors == [], errors
            receipts.append({"check": "chart_export_review_autosave_blinding", "status": "PASS"})
            receipts.append({"check": "native_zoom_screenreader_human_UX", "status": "NOT_TESTED"})
    (args.output / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    print(json.dumps(receipts, indent=2))


if __name__ == "__main__":
    main()
