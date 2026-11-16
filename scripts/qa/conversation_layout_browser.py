"""Layout-only synthetic browser regression. No product DB or answer simulation.

Uses the real static files plus a clearly synthetic empty-session HTTP stub.
This is not a successful application start or a substitute for product E2E.
"""

import argparse
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[2] / "crawler/web/static"


class AssetsHandler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - standard library method name
        if self.path == "/":
            self.path = "/conversation.html"
        if self.path.startswith("/static/"):
            self.path = self.path[len("/static") :]
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(AssetsHandler, directory=str(ASSETS)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    receipts = []
    try:
        with sync_playwright() as runtime:
            with runtime.chromium.launch(channel="chrome", headless=True) as browser:
                page = browser.new_page()

                def stub(route):
                    value = {
                        "csrf": "synthetic_layout",
                        "fixture": True,
                        "snapshot": "LAYOUT_ONLY_NO_CORPUS",
                        "provider": "NO_ANSWER_ENGINE",
                    }
                    route.fulfill(json=value if route.request.url.endswith("/session") else [])

                page.route("**/api/chat/**", stub)
                for height in [568, 720, 1000]:
                    for width in [320, 360, 375, 768, 1024, 1440]:
                        page.set_viewport_size({"width": width, "height": height})
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.locator("#dataset-notice").filter(has_text="Synthetische").wait_for()
                        page.locator("#question").focus()
                        for selector in ["#question", "#send"]:
                            box = page.locator(selector).bounding_box()
                            assert box["y"] >= 0 and box["y"] + box["height"] <= height, (
                                selector,
                                width,
                                height,
                                box,
                            )
                        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
                        receipts.append(
                            {
                                "width": width,
                                "height": height,
                                "status": "PASS",
                                "scope": "LAYOUT_ONLY_NOT_PRODUCT_E2E",
                            }
                        )
                        if height == 568 and width in [320, 1440]:
                            page.screenshot(path=str(args.output / f"layout-{width}-{height}.png"))
    finally:
        server.shutdown()
        server.server_close()
    (args.output / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    print(json.dumps(receipts))


if __name__ == "__main__":
    main()
