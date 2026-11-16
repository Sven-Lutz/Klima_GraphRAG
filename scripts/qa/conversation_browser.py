"""Chrome checks on synthetic data, not a human usability study."""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8825")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    records = []
    with sync_playwright() as runtime:
        with runtime.chromium.launch(channel="chrome", headless=True) as browser:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(a.base)
            page.wait_for_function(
                "document.querySelector('#dataset-notice').textContent.includes('Synthetische')"
            )
            page.locator("#question").fill("Welche Solarprojekte planen die Stadtwerke?")
            start = time.perf_counter()
            page.locator("#question").press("Enter")
            page.locator("#turns article").first.wait_for()
            assert "20 kWp" in page.locator("#turns").inner_text()
            records.append(
                {
                    "check": "answer_first",
                    "seconds": time.perf_counter() - start,
                    "status": "PASS",
                    "human_quality": False,
                }
            )
            page.locator("button.citation").first.click()
            page.locator("#evidence-dialog").wait_for(state="visible")
            assert "20 kWp" in page.locator("#quote").inner_text()
            page.keyboard.press("Escape")
            assert not page.locator("#evidence-dialog").is_visible()
            page.locator("#question").fill("Wie viel Leistung?")
            page.locator("#question").press("Enter")
            page.locator("#turns article").nth(1).wait_for()
            assert "20 kWp" in page.locator("#turns article").nth(1).inner_text()
            page.locator("#question").fill("Wie viele Ladesäulen gibt es in Günzburg?")
            page.locator("#question").press("Enter")
            page.locator("#turns article").nth(2).wait_for()
            assert "Evidenzlücke" in page.locator("#turns article").nth(2).inner_text()
            records.append(
                {"check": "followup_abstention_citation_keyboard_escape", "status": "PASS"}
            )
            for width in [360, 768, 1440, 640]:
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                page.locator("#question").focus()
                assert page.locator("#question").evaluate("(e)=>e===document.activeElement")
                page.screenshot(path=str(a.output / f"chat-{width}.png"), full_page=True)
                records.append({"check": "layout_focus", "width": width, "status": "PASS"})
            records.append(
                {
                    "check": "200_percent_reflow_equivalent",
                    "status": "PASS",
                    "native_browser_zoom": "NOT_TESTED",
                }
            )
            page.goto(a.base + "/research")
            assert page.locator("#expert").get_attribute("open") is not None
            page.goto(a.base + "/validation")
            assert page.locator("#review-login").is_visible()
            assert errors == [], errors
            records.append(
                {"check": "expert_validation_navigation_no_page_errors", "status": "PASS"}
            )
    (a.output / "receipts.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
