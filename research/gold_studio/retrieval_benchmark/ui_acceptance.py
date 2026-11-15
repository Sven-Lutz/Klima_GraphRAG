"""Offline UI smoke test, confined to an explicitly synthetic simulation package."""

import argparse
import json
import tempfile
import zipfile
from pathlib import Path


def run(folder, package_name="A.zip"):
    from playwright.sync_api import sync_playwright

    folder = Path(folder).resolve()
    with zipfile.ZipFile(folder / package_name) as archive:
        package = json.loads(
            archive.read("package.js").decode().removeprefix("window.STUDIO_PACKAGE=")[:-1]
        )
        if (
            not package["binding"]["simulation"]
            or package["tasks"][0]["task_id"] != "TECHNICAL_D1_ONLY"
        ):
            raise ValueError(
                "Synthetic simulation only; never open a research package in this test"
            )
        with tempfile.TemporaryDirectory(prefix="klima-d1-ui-") as name:
            archive.extractall(name)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chrome")
                context = browser.new_context(offline=True, accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(6000)
                errors, network = [], []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "request",
                    lambda r: (
                        network.append(r.url) if r.url.startswith(("http:", "https:")) else None
                    ),
                )
                page.on("dialog", lambda d: d.accept())
                page.goto((Path(name) / "START.html").as_uri())
                page.locator("#person").fill("SYNTHETIC-D1-A")
                page.locator("#qualification").fill("TECHNICAL TEST NOT HUMAN RESEARCH")
                page.locator("#human").check()
                page.locator("#developer").check()
                page.get_by_text("Profil speichern und beginnen", exact=True).click()
                page.locator('[data-key="candidate:candidate-a"]').select_option("sufficient")
                page.locator('[data-key="candidate:candidate-b"]').select_option("contextual")
                page.get_by_role("radio", name="Ja", exact=True).click()
                page.get_by_role("radio", name="Keine unabhängige Nachsuche", exact=True).click()
                page.locator("#inspected").check()
                page.locator("#comment").fill("SYNTHETIC TECHNICAL TEST NOT HUMAN")
                page.locator("#lock").click()
                page.wait_for_function(
                    "()=>document.getElementById('status').textContent"
                    ".includes('Beleggruppen fehlen')"
                )
                assert page.evaluate("()=>state.locked.length") == 0
                page.get_by_label("Notwendiger Aspekt", exact=True).fill("SYNTHETIC aspect")
                page.locator('input[data-qrel-alternative][value="candidate-a"]').check()
                page.get_by_text(
                    "Notwendigen Aspekt mit diesen Alternativen merken", exact=True
                ).click()
                page.wait_for_function("()=>!dirty")
                with page.expect_download() as result:
                    page.locator("#backup").click()
                checkpoint = folder / "checkpoint.json"
                result.value.save_as(checkpoint)
                page.evaluate("()=>localStorage.clear()")
                page.reload()
                page.locator("#restore").set_input_files(checkpoint)
                page.wait_for_function(
                    "()=>document.getElementById('backupStatus').textContent.includes('wiederhergestellt')"
                )
                assert page.evaluate("()=>qrelGroups.length") == 1
                page.locator("#lock").click()
                page.wait_for_function("()=>state.locked.length===1")
                with page.expect_download() as result:
                    page.locator("#backup").click()
                result.value.save_as(folder / "A-return.json")
                assert not errors and not network
                (folder / "BROWSER.json").write_text(
                    json.dumps(
                        {
                            "simulation": True,
                            "human_gold": False,
                            "browser": browser.version,
                            "missing_groups_rejected": True,
                            "group_backup_restore": True,
                            "qrel_lock": True,
                            "javascript_errors": errors,
                            "network": network,
                        },
                        indent=2,
                    )
                )
                browser.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder")
    p.add_argument("--package", default="A.zip")
    args = p.parse_args()
    run(args.folder, args.package)
