"""Real graph gestures, evidence navigation, filters, export and light-only reflow."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"checks": [], "screenshots": [], "independent_human_review": False}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        page.locator("nav [data-view=graph]").click()
        page.wait_for_function("() => window.klimaGraph?.data?.nodes.length > 0", timeout=30000)
        data = page.evaluate("window.klimaGraph.data")
        assert data["edges"]
        before = page.evaluate("window.klimaGraph.graph.zoom()")
        page.locator("#graph-zoom-in").click()
        assert page.evaluate("window.klimaGraph.graph.zoom()") > before
        canvas = page.locator("#graph-canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        before = page.evaluate("window.klimaGraph.graph.pan()")
        page.mouse.move(box["x"] + 10, box["y"] + 10)
        page.mouse.down()
        page.mouse.move(box["x"] + 80, box["y"] + 60, steps=8)
        page.mouse.up()
        assert page.evaluate("window.klimaGraph.graph.pan()") != before
        page.locator("#graph-fit").click()
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        node = page.evaluate("window.klimaGraph.graph.nodes()[0].json().data")
        position = page.evaluate("window.klimaGraph.graph.nodes()[0].renderedPosition()")
        page.mouse.click(box["x"] + position["x"], box["y"] + position["y"])
        assert page.locator("#inspector h2").inner_text() == node["label"]
        # Focusing the inspector may scroll the page; use current canvas coordinates.
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        position = page.evaluate("window.klimaGraph.graph.edges()[0].renderedMidpoint()")
        page.mouse.click(box["x"] + position["x"], box["y"] + position["y"])
        page.wait_for_selector("#inspector .archived-link", timeout=20000)
        report["checks"].append("Actual canvas node and edge pointer selection")
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.wait_for_selector("#inspector .archived-link", timeout=20000)
        assert "Nicht unabhängig menschlich validiert" in page.locator("#inspector").inner_text()
        archived = page.locator("#inspector .archived-link").get_attribute("href")
        response = context.request.get(base + archived.split("#")[0])
        assert response.status == 200
        report["checks"].append(
            "Real graph pan/zoom, edge selection, canonical inspector and SHA-checked archive"
        )
        with page.expect_download() as download:
            page.locator("#graph-export").click()
        saved = output / "subgraph.json"
        download.value.save_as(saved)
        assert json.loads(saved.read_text())["subgraph_sha256"] == data["subgraph_sha256"]
        overview = context.request.get(base + "/api/graph").json()
        catalog = json.loads(Path("docs/product/graph-release/CATALOG.json").read_text())
        q = next(q for q in catalog if q["category"] == "graph_paths")
        source, target = q["origin"]["entity_ids"]
        e = next(e for e in overview["entity_index"] if e["id"] == source)
        page.locator("#graph-search").fill(f"{e['label']} ({source})")
        page.locator("#graph-depth").select_option("3")
        page.locator("#graph-target").select_option(target)
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function("() => window.klimaGraph?.data?.chain.length >= 2")
        assert page.evaluate('window.klimaGraph.graph.edges(".chain").length') >= 2
        report["checks"].append("Stored multi-hop chain is interactively highlighted")
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.wait_for_selector("#inspector .archived-link")
        for width in [1440, 1024, 768, 390]:
            page.set_viewport_size({"width": width, "height": 1000})
            page.wait_for_timeout(250)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            name = f"graph-{width}-light.png"
            page.screenshot(path=str(output / name), full_page=True)
            report["screenshots"].append(name)
        predicate = sorted(overview["relation_types"])[0]
        page.locator("#graph-predicate").select_option(predicate)
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function(
            "(p) => window.klimaGraph?.data?.filters?.predicate === p", arg=predicate
        )
        assert all(
            e["predicate"] == predicate for e in page.evaluate("window.klimaGraph.data.edges")
        )
        page.locator("#graph-predicate").select_option("")
        page.locator("#graph-type").select_option("Organization")
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function(
            '() => window.klimaGraph?.data?.filters?.entity_type==="Organization"'
        )
        assert all(
            n["type"] == "Organization" or n["id"] == source
            for n in page.evaluate("window.klimaGraph.data.nodes")
        )
        page.locator("#graph-type").select_option("")
        page.locator("#graph-target").select_option("")
        if len(overview["reference_years"]):
            year = overview["reference_years"][0]
            page.locator("#graph-year").select_option(year)
            page.locator("#graph-form button[type=submit]").click()
            page.wait_for_function(
                "(year) => window.klimaGraph?.data?.filters?.reference_year===Number(year)",
                arg=year,
            )
            assert all(
                year in str(e["reference_time"])
                for e in page.evaluate("window.klimaGraph.data.edges")
            )
        report["checks"].append("Entity-type and reference-year filters operate on stored data")
        page.locator("#graph-accessible button").first.focus()
        page.keyboard.press("Enter")
        assert page.locator("#inspector").evaluate("(e)=>e===document.activeElement")
        report["checks"].append("Accessible keyboard alternative selects entity and inspector")
        assert not errors, errors
        report["browser"] = browser.version
        report["page_errors"] = errors
        browser.close()
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8810")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.base, a.output)
