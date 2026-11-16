"""Owner-local exposed real-source journey. Outputs contain private evidence: never CI/upload."""

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
    with sync_playwright() as runtime:
        with runtime.chromium.launch(channel="chrome", headless=True) as browser:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.base)
            page.locator("#dataset-notice").filter(has_text="REAL LOCAL CORPUS").wait_for()
            page.locator('#method option[value="lexical"]').wait_for(state="attached")
            for method in ["lexical", "semantic", "hybrid", "graph"]:
                assert page.locator(f'#method option[value="{method}"]').is_enabled()
            page.locator("#method").select_option("graph")
            question = "Wer wurde mit der Wärmeplanung in Nennslingen beauftragt?"
            page.locator("#question").fill(question)
            page.locator("#question").press("Enter")
            page.locator("#turns .citation").first.wait_for()
            page.locator("#turns .citation").first.click()
            page.locator("#evidence-dialog").wait_for(state="visible")
            archive = page.get_by_role("link", name="Archiviertes Original öffnen", exact=True)
            response = page.request.get(args.base + archive.get_attribute("href"))
            assert response.status == 200
            assert "sandbox" in response.headers["content-security-policy"]
            assert len(response.headers["x-original-sha256"]) == 64
            page.keyboard.press("Escape")
            assert page.evaluate("turns[0].claims[0].evidence[0].source_sha256 !== null")
            page.locator("#question").fill("Welche wurden bereits umgesetzt?")
            page.locator("#question").press("Enter")
            page.wait_for_function("() => turns.length === 2")
            assert page.evaluate("turns[1].parent_turn_id === turns[0].turn_id")
            page.locator("#question").fill(question)
            page.locator("#run-compare").click()
            page.locator(".comparison-grid article").nth(3).wait_for()
            with page.expect_download() as download:
                page.get_by_role("button", name="Vergleich exportieren", exact=True).click()
            download.value.save_as(args.output / "comparison-private.json")
            record = json.loads((args.output / "comparison-private.json").read_text())
            assert all(r["status"] == "COMPLETED" for r in record["results"])
            assert len({r["turn"]["dataset_snapshot"] for r in record["results"]}) == 1
            for width in [320, 768, 1440]:
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(args.output / f"real-compare-{width}.png"))
            page.locator(".comparison-grid article").nth(3).get_by_role(
                "button", name="Graph untersuchen", exact=True
            ).click()
            page.locator("#workspace-content table tbody tr").first.wait_for()
            page.locator("#workspace-content button").first.click()
            page.locator("#evidence-dialog").wait_for(state="visible")
            page.keyboard.press("Escape")
            for width in [320, 768, 1440]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.screenshot(path=str(args.output / f"real-graph-{width}.png"))
            page.goto(args.base + "/visualizations")
            page.locator("#workspace-content table").wait_for()
            with page.expect_download() as download:
                page.get_by_role("button", name="Datensatz exportieren", exact=True).click()
            download.value.save_as(args.output / "chart-private.json")
            spec = json.loads((args.output / "chart-private.json").read_text())
            assert spec["snapshot"] == record["configuration"]["snapshot"]
            page.goto(args.base + "/validation")
            page.locator("#claim").filter(has_text="").wait_for()
            assert page.locator('input[type="password"]').count() == 0
            # An automated real-source browser run must never submit a human judgment.
            page.route(
                "**/api/chat/local-review",
                lambda route: (
                    route.continue_(
                        post_data=json.dumps(
                            {**route.request.post_data_json, "origin": "AUTOMATED_TECHNICAL_TEST"}
                        )
                    )
                    if route.request.method == "POST"
                    else route.continue_()
                ),
            )
            page.locator("#comment").fill("Automated technical draft only; no human judgment.")
            page.locator("#status").filter(has_text="Entwurf gespeichert").wait_for()
            with page.expect_download() as download:
                page.locator("#export").click()
            download.value.save_as(args.output / "notes-private.json")
            notes = json.loads((args.output / "notes-private.json").read_text())
            assert not notes["human_gold"]
            assert all(e["state"] == "draft" and e["label"] is None for e in notes["events"])
            page.goto(args.base)
            page.locator("#turns article").first.wait_for()
            page.reload()
            page.locator("#turns article").first.wait_for()
            assert not errors, errors
            receipt = {
                "status": "PASS",
                "corpus": "REAL_LOCAL_RESEARCH",
                "snapshot": record["configuration"]["snapshot"],
                "methods": record["order"],
                "widths": [320, 768, 1440],
                "question_exposure": "DEVELOPMENT_EXPOSED",
                "quality": "UNVALIDATED",
                "human_labels_created": 0,
                "chart_rows": len(spec["rows"]),
                "chart_allowed": spec["chart_allowed"],
                "rights": "PRIVATE_LOCAL_OUTPUT_NOT_FOR_UPLOAD",
            }
            (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
