"""Existing UI against the real minimal loopback service; no external OTP simulation."""

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
        page.wait_for_selector("#examples button")
        page.locator('nav [data-view="workbench"]').click()
        page.locator("#question").fill("Welche Rolle hat Ørsted beim Windpark Brunn-Laaber?")
        page.locator('#query-form button[type="submit"]').click()
        page.wait_for_selector(".evidence-card")
        page.locator(".evidence-card button").first.click()
        assert "Eigene sachliche Darstellung" in page.locator("#inspector").inner_text()
        assert "kein Originalzitat" in page.locator("#inspector").inner_text()
        assert page.locator("#inspector .archived-link").count() == 0
        url = page.locator("#inspector .original-link").get_attribute("href")
        assert url.startswith("https://www.gemeinde-brunn.de/")
        exports = {}
        for key in ("json", "md"):
            selector = "#export-json" if key == "json" else "#export-md"
            link = page.locator(selector)
            if link.count() == 0 and key == "md":
                link = page.locator("#export-markdown")
            href = link.get_attribute("href")
            response = context.request.get(base + href)
            assert response.status == 200
            exports[key] = len(response.body())
        page.locator('nav [data-view="graph"]').click()
        page.wait_for_function("()=>window.klimaGraph?.data?.edges?.length>0")
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.wait_for_selector("#inspector .original-link")
        assert "Eigene sachliche Darstellung" in page.locator("#inspector").inner_text()
        assert page.locator("#inspector .archived-link").count() == 0
        screenshots = []
        for width in (1440, 1024, 768, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            for dark in (False, True):
                page.evaluate("(d)=>theme(d)", dark)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                name = f"minimal-{width}-{'dark' if dark else 'light'}.png"
                page.screenshot(path=str(output / name), full_page=True)
                screenshots.append(name)
        assert not errors, errors
        report = dict(
            schema="minimal-live-browser-v1",
            browser=browser.version,
            viewport_themes=8,
            screenshots=screenshots,
            page_errors=errors,
            free_query="PASS",
            graph_to_inspector="PASS",
            own_fact_label="PASS",
            public_original_link="PASS_HREF_ONLY",
            archive_link="ABSENT",
            exports_bytes=exports,
            horizontal_overflow=False,
            email_otp="NOT_TESTED",
            independent_human_review=False,
        )
        (output / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.output)
