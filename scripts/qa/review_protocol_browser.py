"""Real Chrome acceptance of individual review, with isolated technical users only.

Run against a freshly composed --technical-validation loopback service. Credentials
are read locally and never printed or written to receipts. No external service called.
"""

import argparse
import json
import secrets
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def run(base, credentials, output):
    output.mkdir(parents=True, exist_ok=False)
    lines = Path(credentials).read_text().splitlines()
    owner_password = next(
        line.removeprefix("password: ") for line in lines if line.startswith("password: ")
    )
    suffix = secrets.token_hex(3)
    users = {
        role: ("technical_" + role + "_" + suffix, secrets.token_urlsafe(20))
        for role in ["first", "second", "adjudicator"]
    }
    checks, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        contexts = []

        def page_for(username, password):
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            contexts.append(context)
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/review", wait_until="networkidle")
            page.locator("#rp-username").fill(username)
            page.locator("#rp-password").fill(password)
            page.locator("#rp-login button").click()
            page.locator("#rp-workspace").wait_for(state="visible")
            return page

        owner = page_for("owner", owner_password)
        status = owner.request.get(base + "/api/review-protocol/status").json()
        assert status["technical"] is True, (
            "Never create synthetic decisions in a real review store"
        )
        assert len(status["kinds"]) == 10 and len(status["decisions"]) == 10
        owner.locator("#rp-admin summary").first.click()
        for role, (name, password) in users.items():
            account = owner.locator("#rp-account")
            account.locator('[name="username"]').fill(name)
            account.locator('[name="identity"]').fill("SYNTHETIC DISTINCT TEST USER " + name)
            account.locator('[name="password"]').fill(password)
            account.locator('[name="role"]').select_option(
                "adjudicator" if role == "adjudicator" else "reviewer"
            )
            account.locator("button").click()
            owner.wait_for_function(
                "name=>document.getElementById('rp-accounts').textContent.includes(name)", arg=name
            )
        checks.append("Three individual synthetic identities created through real account forms")
        owner.locator("#rp-source-search button").click()
        owner.locator("#rp-source-results button").first.wait_for()
        owner.locator("#rp-source-results button").first.click()
        owner.wait_for_function("()=>document.getElementById('rp-unit').options.length>1")
        owner.locator("#rp-unit").select_option(index=1)
        quote = owner.locator("#rp-unit-text").inner_text()
        assert quote.strip()
        candidate = owner.locator("#rp-candidate")
        candidate.locator('[name="kind"]').select_option("claim")
        candidate.locator('[name="label"]').fill("SYNTHETIC CLAIM " + suffix)
        candidate.locator('[name="proposed_text"]').fill(
            "Technical source inspection candidate; no real human gold."
        )
        candidate.locator('[name="key"]').fill("technical-browser-" + suffix)
        candidate.locator("button.primary").click()
        owner.locator("#rp-object").wait_for(state="visible")
        assert quote in owner.locator("#rp-quote").inner_text()
        assert "Technical source inspection candidate" in owner.locator("#rp-proposal").inner_text()
        original = owner.locator("#rp-original-link").get_attribute("href")
        proof = owner.request.get(base + original)
        assert proof.status == 200 and len(proof.headers.get("x-original-sha256", "")) == 64
        owner.locator("#rp-first").select_option(users["first"][0])
        owner.locator("#rp-second").select_option(users["second"][0])
        owner.locator("#rp-adjudicator").select_option(users["adjudicator"][0])
        owner.locator("#rp-assign button").click()
        owner.wait_for_function(
            "()=>document.getElementById('rp-object-state').textContent.in"
            "cludes('Erst-/Zweitprüfung offen')"
        )
        checks.append(
            "Actual archived source SHA and exact passage displayed beside "
            "proposed statement; distinct assignment"
        )

        first = page_for(*users["first"])
        first.locator("#rp-queue button").first.click()
        first.locator("#rp-object").wait_for(state="visible")
        first.locator("#rp-decision").select_option("SUPPORTED")
        first.locator("#rp-comment").fill("Synthetic first judgment for technical browser test.")
        first.locator("#rp-inspected").check()
        first.locator("#rp-independent").check()
        first.locator("#rp-save").click()
        first.wait_for_function(
            "()=>document.getElementById('rp-message').textContent.includes('Entwurf gespeichert')"
        )
        first.reload(wait_until="networkidle")
        first.locator("#rp-queue button").first.click()
        first.locator("#rp-object").wait_for(state="visible")
        first.wait_for_function(
            "()=>document.getElementById('rp-comment').value.includes('Synthetic first judgment')"
        )
        assert "Synthetic first judgment" in first.locator("#rp-comment").input_value()
        first.locator("#rp-submit").click()
        first.wait_for_function(
            "()=>document.getElementById('rp-object-state').textContent.in"
            "cludes('Eigene Prüfung abgegeben')"
        )
        assert first.locator("#rp-form").is_hidden()
        checks.append("Reviewer one draft saved and resumed after reload; first judgment locked")
        second = page_for(*users["second"])
        second.locator("#rp-queue button").first.click()
        second.locator("#rp-object").wait_for(state="visible")
        second.locator("#rp-object").wait_for(state="visible")
        assert "Synthetic first judgment" not in second.locator("#rp-history").inner_text()
        assert second.locator("#rp-history").inner_text() == ""
        assert second.request.get(base + "/api/review-protocol/accounts").status == 403
        second.locator("#rp-decision").select_option("WRONG_MUNICIPALITY")
        second.locator("#rp-comment").fill(
            "Synthetic disagreement to test adjudication, no real factual assertion."
        )
        second.locator("#rp-inspected").check()
        second.locator("#rp-independent").check()
        second.locator("#rp-submit").click()
        second.wait_for_function(
            "()=>document.getElementById('rp-object-state').textContent.in"
            "cludes('Eigene Prüfung abgegeben')"
        )
        checks.append(
            "Reviewer two cannot see first judgment or accounts; distinct "
            "conflicting judgment recorded"
        )
        adjudicator = page_for(*users["adjudicator"])
        adjudicator.locator("#rp-filter-state").select_option("CONFLICT")
        adjudicator.locator("#rp-filters button").click()
        adjudicator.locator("#rp-queue button").first.click()
        adjudicator.locator("#rp-object").wait_for(state="visible")
        history = adjudicator.locator("#rp-history").inner_text()
        assert "Synthetic first judgment" in history and "Synthetic disagreement" in history
        adjudicator.locator("#rp-decision").select_option("PARTIALLY_SUPPORTED")
        adjudicator.locator("#rp-comment").fill(
            "Synthetic adjudication rationale; testing traceability only."
        )
        adjudicator.locator("#rp-inspected").check()
        adjudicator.locator("#rp-save").click()
        adjudicator.wait_for_function(
            "()=>document.getElementById('rp-message').textContent.includes('Entwurf gespeichert')"
        )
        adjudicator.locator("#rp-submit").click()
        adjudicator.wait_for_function(
            "()=>document.getElementById('rp-object-state').textContent.includes('Adjudiziert')"
        )
        checks.append(
            "Assigned third reviewer sees both locked judgments, saves draft, "
            "and adjudicates with rationale"
        )
        owner.locator("#rp-filters button").click()
        owner.locator("#rp-agreement").click()
        owner.wait_for_function(
            "()=>document.getElementById('rp-agreement-result').textContent.includes('cohen_kappa')"
        )
        with owner.expect_download() as download:
            owner.locator("#rp-gold").click()
        path = output / "synthetic-gold-export.json"
        download.value.save_as(path)
        exported = json.loads(path.read_text())
        assert exported["technical"] and not exported["human_gold"]
        label = next(x for x in exported["labels"] if x["decision"] == "PARTIALLY_SUPPORTED")
        assert label["label_origin"] == "synthetic_fixture" and label["review_hashes"]
        assert label["sources"][0]["quote"] == quote
        checks.append(
            "IAA and consolidated version export preserve actual source "
            "anchors and synthetic boundary"
        )
        for width in [1440, 768, 390, 320]:
            owner.set_viewport_size({"width": width, "height": 1000})
            assert owner.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
            owner.screenshot(path=str(output / f"studio-{width}.png"), full_page=True)
        owner.set_viewport_size({"width": 320, "height": 1000})
        owner.evaluate("""()=>{
          const sheet=[...document.styleSheets].find(s=>s.href?.endsWith('/review_protocol.css'));
          sheet.insertRule('* {line-height:1.5!important;letter-spacing:.12em!important;'+
                           'word-spacing:.16em!important}',sheet.cssRules.length);
          sheet.insertRule('p {margin-bottom:2em!important}',sheet.cssRules.length);
        }""")
        assert owner.evaluate("document.documentElement.scrollWidth<=innerWidth")
        checks.append(
            "Open review/admin workflow at four widths including320px and "
            "increased text spacing without page overflow"
        )
        # Native named controls, no arbitrary DOM-element count.
        for control in owner.locator(
            "input:visible,select:visible,textarea:visible,button:visible"
        ).all():
            assert control.evaluate(
                "e=>!!(e.labels?.length||e.getAttribute('aria-label')||e.textContent.trim())"
            )
        checks.append("Visible native controls have accessible names")
        assert errors == [], errors
        for context in contexts:
            context.close()
        browser.close()
        # Separate disposable Chrome profile; explicit real browser zoom with DPR proof.
        with tempfile.TemporaryDirectory(prefix="klima-independent-review-zoom-") as profile:
            context = p.chromium.launch_persistent_context(
                profile,
                channel="chrome",
                headless=True,
                viewport=None,
                args=["--window-size=1440,1000"],
            )
            zoomed = context.pages[0]
            zoomed.goto(base + "/review", wait_until="networkidle")
            before = zoomed.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
            settings = context.new_page()
            settings.goto("chrome://settings/appearance")
            settings.locator("#zoomLevel").select_option("2")
            settings.close()
            zoomed.reload(wait_until="networkidle")
            zoomed.locator("#rp-username").fill("owner")
            zoomed.locator("#rp-password").fill(owner_password)
            zoomed.locator("#rp-login button").click()
            zoomed.locator("#rp-workspace").wait_for(state="visible")
            zoomed.locator("#rp-filter-q").fill(suffix)
            zoomed.locator("#rp-filters button").click()
            zoomed.locator("#rp-queue button").first.click()
            zoomed.locator("#rp-object").wait_for(state="visible")
            after = zoomed.evaluate("({width:innerWidth,dpr:devicePixelRatio})")
            assert after["dpr"] == before["dpr"] * 2, (before, after)
            assert zoomed.evaluate("document.documentElement.scrollWidth<=innerWidth")
            focus_checks = []
            for _ in range(15):
                zoomed.keyboard.press("Tab")
                focused = zoomed.evaluate(
                    "()=>({tag:document.activeElement.tagName,id:document.activeElement.id,hidden:!!document.activeElement.closest('[hidden]')})"
                )
                assert not focused["hidden"]
                focus_checks.append(focused)
            zoomed.screenshot(path=str(output / "studio-native-200.png"), full_page=True)
            context.close()
            checks.append(
                "Real200% browser zoom doubles device pixel ratio;15visible keyboard focus steps"
            )
    receipt = {
        "schema": "review-protocol-browser-v1",
        "technical": True,
        "human_gold_created": False,
        "checks": checks,
        "page_errors": errors,
        "independent_usability_claim": False,
        "zoom_before": before,
        "zoom_after": after,
        "keyboard_focus_checks": focus_checks,
    }
    (output / "BROWSER.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base.rstrip("/"), args.credentials, args.output)
