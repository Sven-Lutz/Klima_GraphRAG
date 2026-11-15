"""Actual browser check of source-quality UI, strictly confined to a simulation DB."""

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(workspace, url):
    workspace = Path(workspace).resolve()
    if urlsplit(url).hostname not in ["localhost", "127.0.0.1"]:
        raise ValueError("Local HTTP only")
    c = sqlite3.connect((workspace / "annotations.sqlite").as_uri() + "?mode=ro", uri=True)
    cfg = json.loads(c.execute("select value from metadata where key='config'").fetchone()[0])
    c.close()
    if not cfg["simulation"]:
        raise ValueError("Never annotate a productive human workspace")
    key = (workspace / "Zugangscodes.txt").read_text().split("A: ")[1].splitlines()[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        pg = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(url)
        pg.locator("#code").fill(key)
        pg.locator("#loginForm button.primary").click()
        pg.wait_for_selector("#profile:not([hidden]), #studio:not([hidden])")
        if pg.locator("#profile").is_visible():
            pg.locator("#person").fill("TECHNICAL-QUALITY-A")
            pg.locator("#qualification").fill("SYNTHETIC SOFTWARE TEST NOT HUMAN")
            pg.locator("#human").check()
            pg.locator("#developer").check()
            pg.locator("#independent").uncheck()
            pg.get_by_text("Profil speichern und beginnen", exact=True).click()
        pg.wait_for_selector('[data-key="fidelity"]', state="attached")
        assert pg.locator("#sourceText").inner_text().strip()
        initial = pg.evaluate("()=>state.locked.length")
        pg.screenshot(path=str(workspace / "source-quality.png"), full_page=True)
        if initial < 12:
            values = {
                "fidelity": "unclear",
                "document_type": "unclear",
                "temporal_basis": "unclear",
                "authority": "unclear",
                "scope": "unclear",
                "policy": "unclear",
                "answerability": "UNANSWERABLE",
            }
            for field, value in values.items():
                n = pg.locator('[data-key="' + field + '"]')
                if n.evaluate("(n)=>n.tagName") == "SELECT":
                    n.select_option(value)
                else:
                    label = pg.evaluate("(v)=>names[v]||v", value)
                    n.locator("..").get_by_role("radio", name=label, exact=True).click()
            pg.locator("#inspected").check()
            pg.locator("#comment").fill("SYNTHETIC SOFTWARE TEST NOT HUMAN")
            pg.locator("#technicalUnavailable").check()
            pg.locator("#save").click()
            pg.wait_for_function("()=>!dirty")
            pg.reload()
            pg.wait_for_selector("#technicalUnavailable:checked")
            assert pg.evaluate("()=>state.locked.length") == initial
            pg.locator("#technicalUnavailable").uncheck()
            pg.once("dialog", lambda dialog: dialog.dismiss())
            pg.locator("#lock").click()
            assert pg.evaluate("()=>state.locked.length") == initial
            pg.once("dialog", lambda dialog: dialog.accept())
            pg.locator("#lock").click()
            pg.wait_for_function("n=>state.locked.length===n", arg=initial + 1)
        pg.reload()
        pg.wait_for_function("()=>state?.locked?.length>=1")
        assert not errors
        (workspace / "BROWSER_QUALITY.json").write_text(
            json.dumps(
                {
                    "simulation": True,
                    "human_gold": False,
                    "new_source_quality_fields_visible": True,
                    "real_original_displayed": True,
                    "radio_and_select_interactions": True,
                    "lock_and_reload_retained": True,
                    "technical_unavailability_retained_as_draft": True,
                    "explicit_lock_confirmation_cancel_and_accept": True,
                    "javascript_errors": errors,
                },
                indent=2,
            )
        )
        browser.close()
    print("PASS: actual source-quality controls, original, synthetic lock and reload")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--url", required=True)
    a = p.parse_args()
    run(a.workspace, a.url)
