"""Bounded actual PDF journey and explicitly synthetic UI fault/accessibility probes."""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(output, base="http://127.0.0.1:8800"):
    output.mkdir(parents=True, exist_ok=False)
    report = {"technical_simulations_only": True, "checks": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(base, wait_until="networkidle")
        page.wait_for_selector("#examples button")
        page.locator("#begin").click()
        page.locator("#question").fill("Sabine Steinlechner Einsichtnahme 11.05.2026")
        page.locator(".advanced summary").click()
        page.locator("#top-k").fill("20")
        page.locator("#municipality").fill("Grafenwiesen · Cham (09372130)")
        with page.expect_response("**/api/query") as event:
            page.locator("#search").click()
        payload = event.value.json()
        assert payload.get("evidence"), payload
        pdf = next(
            x for x in payload["evidence"] if x["source_metadata"]["locator"]["kind"] == "pdf_page"
        )
        archive = pdf["source_metadata"]["archive_url"]
        response = context.request.get(base + archive)
        assert response.status == 200 and response.body().startswith(b"%PDF")
        assert response.headers["content-type"] == "application/pdf"
        # Chrome's built-in viewer, original is only ~31 kB; no corpus rendering.
        original = context.new_page()
        original.goto(base + archive + "#page=1", wait_until="load")
        original.wait_for_timeout(1500)  # bounded native viewer rendering, one small PDF
        original.screenshot(path=str(output / "archived-pdf-chrome.png"))
        original.close()
        report["checks"].append(
            {"actual_pdf": pdf["source_metadata"]["original_sha256"], "bytes": len(response.body())}
        )
        # Every visible form control must have a programmatic accessible name.
        unnamed = page.locator(
            "input:visible, select:visible, textarea:visible, button:visible"
        ).evaluate_all("""nodes => nodes.filter(n => !(
          n.getAttribute('aria-label') || n.getAttribute('aria-labelledby') ||
          (n.labels && n.labels.length) || (n.tagName==='BUTTON' && n.textContent.trim())
        )).map(n=>n.id||n.tagName)""")
        assert not unnamed, unnamed
        report["checks"].append(
            "Visible controls have programmatic names; live status and keyboard-native controls"
        )
        before = page.evaluate("({width:innerWidth,ratio:devicePixelRatio})")
        for _ in range(6):
            page.keyboard.press("Meta+=")
        after = page.evaluate("({width:innerWidth,ratio:devicePixelRatio})")
        report["browser_zoom_shortcut_probe"] = {
            "before": before,
            "after": after,
            "status": "NOT_TESTED_NATIVE_ZOOM_UNCHANGED"
            if before == after
            else "ZOOM_CHANGED_VERIFY_LEVEL",
        }
        for _ in range(6):
            page.keyboard.press("Meta+-")
        # Synthetic response based on schema of a real retrieval: adversarial title/URL/answer.
        stress = json.loads(json.dumps(payload))
        stress["evidence"][0]["document"]["heading_context"] = (
            "TECHNISCHER TEST "
            + ("AußergewöhnlichLangerDeutscherDokumenttitelÖÄÜß" * 30)
            + " <script>alert(1)</script>"
        )
        stress["evidence"][0]["document"]["source_url"] = "javascript:alert(1)"
        stress["answer_result"] = {
            "status": "answered",
            "answer_text": "UNSUPPORTED_TECHNICAL_CLAIM",
            "grounding": {
                "citation_resolution_success": True,
                "malformed_citations": [],
                "unknown_citations": [],
                "unsupported_citations": [],
                "uncited_claims": ["not grounded"],
            },
        }

        def synthetic(route):
            time.sleep(0.2)
            route.fulfill(status=200, json=stress)

        page.route("**/api/query", synthetic)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#search").click()
        page.wait_for_selector(".evidence-card")
        page.locator(".evidence-card button").first.click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert "UNSUPPORTED_TECHNICAL_CLAIM" not in page.locator("body").inner_text()
        assert page.locator('#inspector a[href^="javascript:"]').count() == 0
        page.screenshot(path=str(output / "technical-long-title-390.png"), full_page=True)
        report["checks"].append(
            "Synthetic slow response, long German title, unsafe URL, rejected answer: "
            "safe and no overflow"
        )
        page.unroute("**/api/query", synthetic)
        empty = {**payload, "evidence": [], "graph": {"nodes": [], "edges": []}}
        empty["retrieval_result"] = {**payload["retrieval_result"], "items": []}
        page.route("**/api/query", lambda route: route.fulfill(status=200, json=empty))
        page.locator("#search").click()
        page.wait_for_function(
            "() => document.getElementById('query-status').dataset.state==='empty'"
        )
        assert (
            "kein Nachweis fehlender kommunaler Maßnahmen" in page.locator("#results").inner_text()
        )
        report["checks"].append("Synthetic empty: not interpreted as zero municipal measures")
        browser.close()
    (output / "EDGE_VALIDATION.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8800")
    args = parser.parse_args()
    run(args.output, args.base)
