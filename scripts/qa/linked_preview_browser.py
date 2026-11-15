"""Local linked-original journey; no external source fetch or rights decision."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.locator('nav [data-view="graph"]').click()
        page.wait_for_function("()=>window.klimaGraph?.data?.edges?.length>0")
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.wait_for_selector("#inspector .original-link")
        assert page.locator("#inspector .archived-link").count() == 0
        assert "Keine Archivdatei wird bereitgestellt" in page.locator("#inspector").inner_text()
        assert "hier nicht erneut geladen" in page.locator("#inspector").inner_text()
        href = page.locator("#inspector .original-link").get_attribute("href")
        assert href.startswith("https://")
        for width in (1440, 1024, 768, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            for dark in (False, True):
                page.evaluate("(d)=>theme(d)", dark)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(output / f"linked-{width}-{dark}.png"), full_page=True)
        assert not errors, errors
        report = {
            "schema": "linked-preview-browser-v1",
            "browser": browser.version,
            "graph_to_public_url": "PASS_HREF_ONLY_NOT_FETCHED",
            "archive_link": "ABSENT",
            "runtime_rehash_not_claimed": True,
            "page_errors": errors,
            "viewport_themes": 8,
            "independent_human_review": False,
            "external_source_fetched": False,
        }
        (output / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.output)
