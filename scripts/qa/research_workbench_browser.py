"""Real Chrome workbench journey over explicit synthetic app; no human acceptance inference."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8826")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    with sync_playwright() as runtime:
        with runtime.chromium.launch(channel="chrome", headless=True) as browser:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.base)
            page.locator("#method option[value=semantic]").wait_for(state="attached")
            assert page.locator('input[type="password"]').count() == 0
            assert page.locator('#method option[value="lexical"]').is_disabled()
            page.locator("#method").select_option("semantic")
            question = "Welche Solarprojekte planen die Stadtwerke?"
            page.locator("#question").fill(question)
            page.locator("#question").press("Enter")
            page.locator("#turns article").wait_for()
            assert "20 kWp" in page.locator("#turns").inner_text()
            page.locator("#turns .citation").first.click()
            page.locator("#evidence-dialog").wait_for(state="visible")
            page.keyboard.press("Escape")
            page.locator("#question").fill("Wie viel Leistung?")
            page.locator("#question").press("Enter")
            page.wait_for_function("() => turns.length === 2")
            assert page.evaluate("turns[1].parent_turn_id === turns[0].turn_id")
            page.locator("#method").select_option("graph")
            page.locator("#question").fill(question)
            page.locator("#run-compare").click()
            page.locator(".comparison-grid article").nth(2).wait_for()
            assert page.locator(".comparison-grid article").count() == 3
            assert "UNVALIDATED" in page.locator("#comparison-panel").inner_text()
            with page.expect_download() as download:
                page.get_by_role("button", name="Vergleich exportieren", exact=True).click()
            download.value.save_as(args.output / "comparison.json")
            record = json.loads((args.output / "comparison.json").read_text())
            assert len({r["turn"]["dataset_snapshot"] for r in record["results"]}) == 1
            for width in [320, 360, 375, 768, 1024, 1440]:
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                if width in [320, 768, 1440]:
                    page.screenshot(path=str(args.output / f"compare-{width}.png"))
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator(".comparison-grid article").nth(1).get_by_role(
                "button", name="Graph untersuchen", exact=True
            ).click()
            page.locator("#workspace-content table").wait_for()
            assert page.locator('[aria-label="Nachbarschaft einer Entität"]').count() == 1
            assert "Quellenpassage" in page.locator("#workspace-content").inner_text()
            for width in [320, 768, 1440]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_function("""() => {
                    const box=workspaceGraph.elements().renderedBoundingBox();
                    return box.x1>=0 && box.y1>=0 && box.x2<=workspaceGraph.width()
                      && box.y2<=workspaceGraph.height();
                }""")
                page.screenshot(path=str(args.output / f"graph-{width}.png"))
            with page.expect_download() as download:
                page.get_by_role("button", name="PNG exportieren", exact=True).click()
            download.value.save_as(args.output / "graph.png")
            assert (args.output / "graph.png").stat().st_size > 100
            page.goto(args.base + "/visualizations")
            page.get_by_role("button", name="Datensatz exportieren").wait_for()
            assert "20" in page.locator("#workspace-content table").inner_text()
            page.goto(args.base + "/validation")
            page.locator("#claim").filter(has_text="20 kWp").wait_for()
            assert page.locator('input[type="password"]').count() == 0
            page.locator("#label").select_option("Partially supported")
            page.locator("#comment").fill("Synthetische technische Bedienprobe; kein Human Gold.")
            page.locator("#status").filter(has_text="Entwurf gespeichert").wait_for()
            page.locator("#kind").select_option("source")
            assert page.locator("#label").input_value() == ""
            page.locator("#kind").select_option("claim")
            page.locator("#next").click()
            page.locator("#status").filter(has_text="Urteil versioniert gespeichert").wait_for()
            with page.expect_download() as download:
                page.locator("#export").click()
            download.value.save_as(args.output / "local-notes.json")
            notes = json.loads((args.output / "local-notes.json").read_text())
            assert notes["human_gold"] is False
            assert any(e["state"] == "judgment" for e in notes["events"])
            for width in [320, 768, 1440]:
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(args.output / f"notes-{width}.png"), full_page=True)
            page.goto(args.base)
            page.locator("#turns article").first.wait_for()
            page.reload()
            page.locator("#turns article").first.wait_for()
            context.clear_cookies()  # Simulate client-side cookie expiry in this isolated browser.
            page.locator("#question").fill(question)
            page.locator("#question").press("Enter")
            page.wait_for_function("() => turns.length === 1 && turns[0].claims.length > 0")
            page.reload()
            page.locator("#turns article").first.wait_for()
            assert "csrf_or_session_required" not in page.locator("body").inner_text()
            assert "Expired session" not in page.locator("body").inner_text()
            assert not errors, errors
            receipts.append(
                {
                    "journey": (
                        "anonymous_question_followup_methods_compare_graph_chart_"
                        "note_export_expiry_reload"
                    ),
                    "status": "PASS",
                    "corpus": "SYNTHETIC",
                    "human_gold": False,
                    "widths": [320, 360, 375, 768, 1024, 1440],
                    "human_acceptance": "NOT_EVALUATED",
                    "native_zoom": "NOT_TESTED",
                }
            )
    (args.output / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    print(json.dumps(receipts, indent=2))


if __name__ == "__main__":
    main()
