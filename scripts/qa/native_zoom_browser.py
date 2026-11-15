"""Native Chrome 200% zoom in a disposable profile; never alters user settings."""

import argparse
import base64
import json
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output, view="graph"):
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="klima-native-zoom-") as profile:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                profile,
                channel="chrome",
                headless=True,
                viewport=None,
                args=["--window-size=1440,1000"],
            )
            page = context.pages[0]
            page.goto(base)
            before = page.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
            settings = context.new_page()
            settings.goto("chrome://settings/appearance")
            settings.locator("#zoomLevel").select_option("2")
            settings.close()
            page.reload()
            page.locator(f"nav [data-view={view}]").click()
            if view == "graph":
                page.wait_for_function("() => window.klimaGraph?.data")
            elif view == "validation":
                page.wait_for_function("()=>validationState.ready")
                page.locator("#validation-queue button").first.click()
                page.wait_for_selector("#validation-form:not([hidden])")
            else:
                page.wait_for_function("()=>state.status!==null")
                page.locator("#query-form .advanced").evaluate("e=>e.open=true")
                page.locator("#question").focus()
            focus_checks = []
            for _ in range(15):
                page.keyboard.press("Tab")
                focused = page.evaluate(
                    "()=>({tag:document.activeElement.tagName,id:document.activeElement.id,hidden:document.activeElement.closest('[hidden]')!==null})"
                )
                assert not focused["hidden"]
                focus_checks.append(focused)
            after = page.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
            assert after["dpr"] == before["dpr"] * 2, (before, after)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            session = context.new_cdp_session(page)
            screenshot = session.send(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (output / "native-200-light.png").write_bytes(base64.b64decode(screenshot["data"]))
            page.locator(
                "#graph-canvas"
                if view == "graph"
                else "#validation-form"
                if view == "validation"
                else "#query-form"
            ).scroll_into_view_if_needed()
            screenshot = session.send(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
            )
            (output / "native-200-content-light.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
            (output / "REPORT.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "after": after,
                        "native_browser_zoom": 2,
                        "view": view,
                        "keyboard_focus_checks": focus_checks,
                        "browser": context.browser.version,
                        "overflow": False,
                        "human_screenreader_review": False,
                    },
                    indent=2,
                )
            )
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8810")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--view", choices=["graph", "validation", "workbench"], default="graph")
    args = parser.parse_args()
    run(args.base, args.output, args.view)
