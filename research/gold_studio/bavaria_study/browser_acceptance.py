# ruff: noqa: E501
"""Real Chrome file:// interactions on synthetic fixture; needs Playwright Python."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv/bin/python"


def command(folder, action):
    subprocess.run(
        [
            str(PYTHON),
            "-m",
            "research.gold_studio.bavaria_study.technical_fixture",
            str(folder),
            action,
        ],
        cwd=ROOT,
        check=True,
    )


def profile(pg, role):
    pg.locator("#person").fill("TECHNICAL-" + role)
    pg.locator("#qualification").fill("SYNTHETIC SOFTWARE TEST NOT HUMAN")
    pg.locator("#human").check()
    pg.locator("#developer").check()
    pg.get_by_text("Profil speichern und beginnen", exact=True).click()
    pg.wait_for_selector("#owner:not([hidden])" if role == "C" else "#studio:not([hidden])")


def backup(pg, path):
    with pg.expect_download() as info:
        pg.locator("#backup").click()
    info.value.save_as(path)
    pg.locator("#restore").set_input_files(path)
    pg.wait_for_function(
        "()=>document.querySelector('#backupStatus').textContent.includes('wiederhergestellt')"
    )
    pg.locator("#verifyBackup").click()
    pg.wait_for_function(
        "()=>document.querySelector('#backupStatus').textContent.includes('verifiziert')"
    )


def run(folder):
    folder = Path(folder).resolve()
    resume = (
        (folder / "A-return.json").exists()
        and (folder / "B-return.json").exists()
        and (folder / "C.zip").exists()
    )
    if not resume:
        command(folder, "prepare")
    records = []
    with (
        tempfile.TemporaryDirectory(prefix="klima-batch-offline-") as temp,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch(channel="chrome")
        for role in ["C"] if resume else ["A", "B", "C"]:
            if role == "C" and not resume:
                command(folder, "B")
            target = Path(temp) / role
            with zipfile.ZipFile(folder / (role + ".zip")) as z:
                z.extractall(target)
            manifest = json.loads((target / "MANIFEST.json").read_text())
            assert all(
                hashlib.sha256((target / name).read_bytes()).hexdigest() == h
                for name, h in manifest["files_sha256"].items()
            )
            ctx = browser.new_context(
                offline=True, accept_downloads=True, viewport={"width": 1400, "height": 1000}
            )
            pg = ctx.new_page()
            errors = []
            net = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on(
                "request",
                lambda r: net.append(r.url) if r.url.startswith(("http:", "https:")) else None,
            )
            pg.goto((target / "START.html").as_uri())
            profile(pg, role)
            if role != "C":
                if role == "B":
                    foreign = json.loads((folder / "A-return.json").read_text())
                    message = pg.evaluate(
                        "async e=>{try{await STUDIO_PORTABLE.restore(JSON.stringify(e));return 'BAD'}catch(e){return e.message}}",
                        foreign,
                    )
                    assert "anderen Person" in message
                # Both actual original formats must load without a project server.
                for kind in [".html", ".pdf"]:
                    source = next(
                        s
                        for s in manifest["sources"]
                        if (
                            target
                            / "sources"
                            / s
                            / ("original.pdf" if kind == ".pdf" else "original.html.bin")
                        ).exists()
                    )
                    file = (
                        target
                        / "sources"
                        / source
                        / ("original.pdf" if kind == ".pdf" else "view.html")
                    )
                    page = ctx.new_page()
                    response = page.goto(file.as_uri())
                    assert response is None or response.ok
                    page.close()
                for i in range(40):
                    pg.wait_for_function('()=>!document.querySelector("#lock").disabled')
                    pg.evaluate(
                        """([role,i])=>{const values={authority:role==='A'&&i===0?'official':'unclear',scope:'unclear',policy:'unclear',answerability:'UNANSWERABLE',temporal:'UNKNOWN',judgment:'not_proven',complete:'unclear'};for(const n of document.querySelectorAll('#controls [data-key]'))if(values[n.dataset.key])n.value=values[n.dataset.key];document.querySelector('#inspected').checked=true;document.querySelector('#comment').value='SYNTHETIC SOFTWARE TEST NOT HUMAN';}""",
                        [role, i],
                    )
                    pg.locator("#lock").click()
                    pg.wait_for_function("(n)=>state.locked.length===n", arg=i + 1)
                    if i == 9:
                        backup(pg, folder / (role + "-checkpoint.json"))
                        pg.reload()
                        pg.wait_for_selector("#studio:not([hidden])")
                backup(pg, folder / (role + "-return.json"))
                pg.screenshot(path=str(folder / (role + "-screen.png")), full_page=True)
                command(folder, role) if role == "A" else None
            else:
                pg.locator("#conflicts").get_by_role("radio", name="A", exact=True).first.click()
                pg.locator('[data-key="note"]').first.fill("SYNTHETIC TECHNICAL ADJUDICATION")
                pg.get_by_text("Adjudikation unveränderlich speichern", exact=True).first.click()
                pg.wait_for_function(
                    "()=>document.querySelector('#conflicts').textContent.includes('Abgeschlossen:')"
                )
                backup(pg, folder / "C-return.json")
                command(folder, "C")
            assert not errors and not net, (errors, net)
            records.append(
                {
                    "role": role,
                    "javascript_errors": errors,
                    "network_requests": net,
                    "file_only": True,
                }
            )
            ctx.close()
        browser.close()
    (folder / "BROWSER.json").write_text(
        json.dumps(
            {
                "simulation": True,
                "browser": "installed Google Chrome",
                "resumed_completed_A_B": resume,
                "records": records,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder")
    a = p.parse_args()
    run(a.folder)
