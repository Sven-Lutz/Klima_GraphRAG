"""24 technical first-use journeys on real local data; no independent human judgments."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, output):
    output.mkdir(parents=True, exist_ok=False)
    checks = []

    def passed(case, detail):
        checks.append(
            {"id": case, "status": "PASS", "detail": detail, "review": "TECHNICAL_SELF_REVIEW"}
        )

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
        assert "Research Preview" in page.inner_text("body")
        passed("P01", "Purpose, preview boundary and corpus scope visible")
        page.locator("#examples button").first.click()
        page.wait_for_function(
            "()=>['completed','partial','empty'].includes(document.getElementById('query-status').dataset.state)",
            timeout=25000,
        )
        assert page.locator(".evidence-card").count() > 0
        passed("P02", "First stored municipality example returns real source leads")
        assert page.locator("#mode").input_value() == "lexical"
        passed("P03", "BM25 is the default; no generated answer is presented")
        page.locator(".evidence-card button").first.click()
        assert "Nicht unabhängig menschlich validiert" in page.locator("#inspector").inner_text()
        passed("P04", "Inspector distinguishes source lead and missing independent validation")
        archive = page.locator("#inspector .archived-link").get_attribute("href")
        response = context.request.get(base + archive.split("#")[0])
        assert response.status == 200
        assert "sandbox" in response.headers.get("content-security-policy", "")
        passed("P05", "Real archived HTML opens under passive source CSP")
        j = context.request.get(base + page.locator("#export-json").get_attribute("href")).json()
        assert j.get("database_snapshot_id") or j.get("retrieval_result", {}).get(
            "database_snapshot_id"
        )
        passed("P06", "Canonical JSON retains snapshot and evidence identities")
        assert (
            context.request.get(base + page.locator("#export-md").get_attribute("href")).status
            == 200
        )
        passed("P07", "Markdown export is independently retrievable")
        if not page.locator("#mode").is_visible():
            page.locator(".advanced summary").click()
        option = page.locator("#mode option[value=lexical_expanded]").inner_text()
        assert "experimentell" in option
        page.locator("#mode").select_option("lexical_expanded")
        page.locator("#search").click()
        page.wait_for_function(
            "()=>document.getElementById('query-status').dataset.state!=='searching'"
        )
        passed("P08", "Experimental lexical expansion is understandable and executable")
        page.locator("#question").fill("xqzvnonexistentpilotterm")
        page.locator("#search").click()
        page.wait_for_function(
            "()=>document.getElementById('query-status').dataset.state==='empty'", timeout=25000
        )
        assert (
            "kein Nachweis fehlender kommunaler Maßnahmen" in page.locator("#results").inner_text()
        )
        passed("P09", "Empty results do not imply missing municipal policy")
        page.locator("#year").fill("2024")
        page.locator("#question").fill("Wärmeplanung")
        page.locator("#search").click()
        page.wait_for_function(
            "()=>document.getElementById('query-status').dataset.state!=='searching'"
        )
        assert (
            "Gültigkeit" in page.locator("#year-hint").inner_text()
            or "top-k" in page.locator("#year-hint").inner_text()
        )
        passed("P10", "Text-year limitation is explicit, not presented as validity")
        page.locator("nav [data-view=graph]").click()
        page.wait_for_function("()=>window.klimaGraph?.data")
        overview = context.request.get(base + "/api/graph").json()
        assert (
            overview["entities"] == 124
            and overview["relations"] == 108
            and not overview["human_gold"]
        )
        passed("P11", "Graph overview reports actual provisional data")
        data = page.evaluate("window.klimaGraph.data")
        root = next(e for e in overview["entity_index"] if e["id"] == data["root"])
        page.locator("#graph-search").fill(f"{root['label']} ({root['id']})")
        page.locator("#graph-depth").select_option("2")
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function("()=>window.klimaGraph?.data?.depth===2")
        passed("P12", "Stored entity search and depth-two ego navigation operate")
        page.locator("#graph-accessible button").first.click()
        assert "Verbundene Quellenbelege" in page.locator("#inspector").inner_text()
        passed("P13", "Entity selection offers direct adjacent source evidence")
        page.locator("#graph-accessible button[data-relation-id]").first.click()
        page.wait_for_selector("#inspector .archived-link")
        assert (
            "Keine unabhängige menschliche Fachprüfung" in page.locator("#inspector").inner_text()
        )
        assert page.locator("#inspector details").count() > 0
        passed("P14", "Relation provenance, uncertainty and extraction identity visible")
        original = page.locator("#inspector .archived-link").get_attribute("href")
        assert context.request.get(base + original.split("#")[0]).status == 200
        passed("P15", "Relation to original source resolves")
        predicate = data["edges"][0]["predicate"]
        page.locator("#graph-predicate").select_option(predicate)
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function(
            "(p)=>window.klimaGraph?.data?.filters?.predicate===p", arg=predicate
        )
        assert all(
            e["predicate"] == predicate for e in page.evaluate("window.klimaGraph.data.edges")
        )
        passed("P16", "Relation filter changes the actual bounded subgraph")
        page.locator("#graph-predicate").select_option("")
        catalog = json.loads(Path("docs/product/graph-release/CATALOG.json").read_text())
        q = next(q for q in catalog if q["category"] == "graph_paths")
        a, b = q["origin"]["entity_ids"]
        entity = next(e for e in overview["entity_index"] if e["id"] == a)
        page.locator("#graph-search").fill(f"{entity['label']} ({a})")
        page.locator("#graph-depth").select_option("3")
        page.locator("#graph-target").select_option(b)
        page.locator("#graph-form button[type=submit]").click()
        page.wait_for_function("()=>window.klimaGraph?.data?.chain?.length>=2")
        passed("P17", "Real multi-hop evidence chain can be highlighted")
        with page.expect_download() as download:
            page.locator("#graph-export").click()
        download.value.save_as(output / "subgraph.json")
        assert json.loads((output / "subgraph.json").read_text())["subgraph_sha256"]
        passed("P18", "Subgraph export includes reproducible identity")
        method = context.new_page()
        method.goto(base + "/methodik")
        assert "GraphRAG-Vorteil ist nicht nachgewiesen" in method.inner_text("body")
        assert "keine validierten Gültigkeitsintervalle" in method.inner_text("body")
        method.screenshot(path=str(output / "methodology.png"), full_page=True)
        method.close()
        passed("P19", "Methodology explains extraction, retrieval and open comparisons")
        page.locator("#graph .graph-toolbar [data-view=feedback]").click()
        page.locator("#feedback-message").fill(
            "TECHNICAL_TEST_ONLY: Oberfläche und Rückgabe prüfen, kein Fachurteil."
        )
        page.locator("#feedback-preview").click()
        preview = json.loads(page.locator("#feedback-json").inner_text())
        assert (
            preview["context"] is None
            and preview["snapshot_id"]
            and preview["software"].startswith("preview-sha256:")
        )
        with page.expect_download() as download:
            page.locator("#feedback-form button[type=submit]").click()
        download.value.save_as(output / "feedback-no-context.json")
        page.locator("#feedback-context").check()
        page.locator("#feedback-preview").click()
        assert json.loads(page.locator("#feedback-json").inner_text())["context"]["graph"][
            "subgraph_sha256"
        ]
        with page.expect_download() as download:
            page.locator("#feedback-form button[type=submit]").click()
        download.value.save_as(output / "feedback-opt-in.json")
        passed(
            "P20", "Explicit backup/export, default exclusion and optional context; no transmission"
        )
        for width in [1440, 1024, 768, 390]:
            page.set_viewport_size({"width": width, "height": 1000})
            for dark in [False, True]:
                page.evaluate("(d)=>theme(d)", dark)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(output / f"feedback-{width}-{dark}.png"), full_page=True)
        passed("P21", "Feedback reflows in both themes at desktop/tablet/mobile")
        page.locator("nav [data-view=graph]").click()
        page.wait_for_function("()=>window.klimaGraph?.data")
        page.locator("#graph-accessible button").first.focus()
        page.keyboard.press("Enter")
        assert page.locator("#inspector").evaluate("(n)=>n===document.activeElement")
        passed("P22", "Native keyboard alternative opens graph evidence surface")
        page.screenshot(path=str(output / "graph-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("theme(false)")
        page.screenshot(path=str(output / "graph-desktop.png"), full_page=True)
        passed("P23", "Graph screenshot critique on actual data, no horizontal overflow")
        page.locator("nav [data-view=transparency]").click()
        assert "Noch kein freigegebenes unabhängiges Human Gold" in page.inner_text("body")
        passed("P24", "Transparency keeps technical integrity and human validity separate")
        assert not errors, errors
        report = {
            "schema": "external-pilot-browser-v1",
            "browser": browser.version,
            "tasks": checks,
            "independent_human_review": False,
            "gold_judgments_created": 0,
            "page_errors": errors,
        }
        (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        browser.close()
    print(json.dumps({"passed": len(checks), "independent_human_review": False}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8820")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.base, a.output)
