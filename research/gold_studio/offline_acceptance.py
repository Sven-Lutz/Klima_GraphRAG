# ruff: noqa: E501
"""Technical-only file:// browser, download/restore and actual Studio import acceptance.
Use a Python with Playwright. Production Store is never annotated.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/bin/python"


def command(*args):
    r = subprocess.run(
        [str(PY), "-m", "research.gold_studio.portable", *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout


def profile(pg, role):
    pg.locator("#person").fill("TECHNICAL-" + role)
    pg.locator("#qualification").fill("SYNTHETIC_TECHNICAL_TEST_NOT_HUMAN")
    pg.locator("#human").check()
    pg.locator("#developer").check()
    pg.get_by_text("Profil speichern und beginnen", exact=True).click()
    pg.wait_for_selector("#studio:not([hidden])")


def download(pg, path):
    with pg.expect_download() as info:
        pg.locator("#backup").click()
    info.value.save_as(path)
    pg.locator("#restore").set_input_files(path)
    pg.wait_for_function(
        '()=>document.querySelector("#backupStatus").textContent.includes("wiederhergestellt")'
    )
    pg.locator("#verifyBackup").click()
    pg.wait_for_function(
        '()=>document.querySelector("#backupStatus").textContent.includes("verifiziert")'
    )
    return json.loads(Path(path).read_text())


def fill(pg, role, first=False):
    # Deterministic synthetic test input through the shared real form / submit handlers.
    pg.evaluate(
        """([role,first])=>{
      const set=(key,v)=>{for(const n of document.querySelectorAll('[data-key]'))if(n.dataset.key===key)n.value=v;};
      set('authority',first?(role==='A'?'official':'institutional'):'unclear');set('scope','unclear');set('policy','unclear');set('answerability','UNANSWERABLE');set('temporal','UNKNOWN');set('judgment','not_proven');
      for(const n of document.querySelectorAll('#controls [data-key]')){
        if(n.dataset.key.startsWith('candidate:'))n.value='unclear';
        if(['complete','abstention'].includes(n.dataset.key))n.value='unclear';
        if(['support','citation','scope_time_numbers'].includes(n.dataset.key))n.value='not_proven';
      }
      document.querySelector('#inspected').checked=true;
      document.querySelector('#comment').value='SYNTHETIC_TECHNICAL_TEST_NOT_HUMAN';
    }""",
        [role, first],
    )
    pg.locator("#lock").click()


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    workspace = out / "simulation-workspace"
    if workspace.exists():
        raise ValueError(
            "Choose a fresh output directory; previous technical judgments are preserved"
        )
    for role in ["A", "B"]:
        command(
            "export",
            "--workspace",
            workspace,
            "--simulation",
            "--role",
            role,
            "--output",
            out / (role + ".zip"),
        )
    records = []
    # The desktop server is unnecessary: fresh unzip in an independent system temporary directory.
    with (
        tempfile.TemporaryDirectory(prefix="klima-portable-offline-") as temp_str,
        sync_playwright() as pw,
    ):
        temp = Path(temp_str)
        for role in ["A", "B"]:
            target = temp / role
            with zipfile.ZipFile(out / (role + ".zip")) as z:
                z.extractall(target)
            manifest = json.loads((target / "MANIFEST.json").read_text())
            assert all(
                hashlib.sha256((target / name).read_bytes()).hexdigest() == h
                for name, h in manifest["files_sha256"].items()
            )
            browser = pw.chromium.launch()
            context = browser.new_context(
                offline=True, accept_downloads=True, viewport={"width": 1450, "height": 1000}
            )
            pg = context.new_page()
            errors = []
            network = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on(
                "request",
                lambda r: network.append(r.url) if r.url.startswith(("http:", "https:")) else None,
            )
            pg.goto((target / "START.html").as_uri())
            profile(pg, role)
            assert pg.locator("#portableMode").inner_text().startswith("TECHNISCHE SIMULATION")
            # Actual original text, PDF page image and passive full HTML must work without a server.
            pdfsha = pg.evaluate(
                'Object.values(STUDIO_PACKAGE.sources).find(x=>x.file.endsWith(".pdf")).sha256'
            )
            pg.select_option("#sourceSelect", pdfsha)
            pg.wait_for_function('()=>document.querySelector("#pdfPage").naturalWidth>100')
            assert pg.locator("#sourceText").inner_text().strip()
            pg.screenshot(path=str(out / f"original-pdf-{role}.png"), full_page=True)
            htmlsha = pg.evaluate(
                'Object.values(STUDIO_PACKAGE.sources).find(x=>x.file.endsWith(".html")).sha256'
            )
            pg.select_option("#sourceSelect", htmlsha)
            pg.wait_for_function('()=>document.querySelector("#pdfPage").hidden')
            with pg.expect_popup() as popup:
                pg.locator("#fullSource").click()
            popup = popup.value
            popup.wait_for_load_state()
            assert "passive Ansicht" in popup.inner_text("body")
            assert popup.locator("script").count() == 0
            popup.close()
            # A real text selection and visible highlight, not a manufactured reviewer label.
            pg.evaluate(
                """()=>{const n=document.querySelector('#sourceText').firstChild;const r=document.createRange();r.setStart(n,0);r.setEnd(n,Math.min(35,n.textContent.length));getSelection().removeAllRanges();getSelection().addRange(r);}"""
            )
            pg.locator("#capture").click()
            assert pg.locator("#sourceText mark").count() == 1
            pg.locator("#quote").fill("")
            pg.locator("#comment").fill("SYNTHETIC_TECHNICAL_TEST_NOT_HUMAN")
            pg.locator("#save").click()
            # Backup and suspend; reopen with a clean browser context (no localStorage or cookies).
            backup = out / f"{role}-partial.json"
            download(pg, backup)
            context.close()
            context = browser.new_context(offline=True, accept_downloads=True)
            pg = context.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto((target / "START.html").as_uri())
            pg.locator("#restore").set_input_files(backup)
            pg.wait_for_selector("#studio:not([hidden])")
            assert pg.locator("#comment").input_value() == "SYNTHETIC_TECHNICAL_TEST_NOT_HUMAN"
            for i in range(48):
                fill(pg, role, i == 0)
                pg.wait_for_function(
                    '(n)=>document.querySelector("#progressText").textContent.startsWith(n+" von")',
                    arg=i + 1,
                )
            assert pg.locator("#progressText").inner_text().startswith("48 von 48")
            pg.locator("#feedbackPanel summary").click()
            pg.locator("#clarity").fill("3")
            pg.locator("#questionsCount").fill("0")
            pg.locator("#feedbackComment").fill("SYNTHETIC SOFTWARE TEST, NOT HUMAN USABILITY")
            pg.locator("#feedbackSave").click()
            final = out / f"{role}-final.json"
            envelope = download(pg, final)
            parsed = json.loads(envelope["content"])
            assert parsed["profile"]["human"] is False
            assert len([e for e in parsed["events"] if e["kind"] == "lock"]) == 48
            # Real FastAPI import endpoint through local ASGI TestClient, no running server, same production Store API.
            script = """import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
from research.gold_studio.app import create_app
app=create_app(sys.argv[2],True);s=app.state.store
with TestClient(app) as c:
 c.post('/api/login',json={'role':'C','code':json.loads(s.meta('keys'))['C']})
 r=c.post('/api/portable/import',json=json.loads(Path(sys.argv[1]).read_text()));print(r.status_code,r.text);assert r.status_code==200
 assert s.report()['gold_status']=='NOT_RELEASED';s.audit()
"""
            r = subprocess.run(
                [str(PY), "-c", script, str(final), str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            assert r.returncode == 0, r.stderr + r.stdout
            records.append(
                {
                    "role": role,
                    "browser": "Chromium",
                    "version": browser.version,
                    "file_zip_roundtrip": True,
                    "fresh_context_restore": True,
                    "locked_steps": 48,
                    "api_import": r.stdout.strip(),
                    "errors": errors,
                    "external_requests": network,
                }
            )
            assert not errors and not network
            context.close()
            browser.close()
        # Installed Google Chrome is tested as a second real browser binary, explicitly same engine family.
        browser = pw.chromium.launch(channel="chrome")
        context = browser.new_context(offline=True, accept_downloads=True)
        pg = context.new_page()
        pg.goto((temp / "A/START.html").as_uri())
        pg.locator("#restore").set_input_files(out / "A-final.json")
        pg.wait_for_selector("#studio:not([hidden])")
        assert pg.locator("#progressText").inner_text().startswith("48 von 48")
        download(pg, out / "Chrome-restored.json")
        records.append(
            {
                "browser": "Google Chrome",
                "version": browser.version,
                "fresh_profile_restore_and_download": "PASS",
            }
        )
        context.close()
        browser.close()
    # Import is append-only, repeat imports are idempotent, all originals remain reconstructible.
    checks = """import json,sys
from pathlib import Path
from research.gold_studio.store import Store
from research.gold_studio.prepare import prepare
s=Store(Path(sys.argv[1])/'annotations.sqlite',prepare(),True)
print(json.dumps({'locked_A':len(s.events('A','lock')),'locked_B':len(s.events('B','lock')),'conflicts':len(s.conflicts()),'gold_status':s.report()['gold_status'],'audit_head':s.audit()}))
assert len(s.events('A','lock'))==48 and len(s.events('B','lock'))==48
"""
    r = subprocess.run(
        [str(PY), "-c", checks, str(workspace)], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    report = {
        "schema": "portable-offline-technical-acceptance-v1",
        "synthetic_only": True,
        "records": records,
        "store": json.loads(r.stdout),
        "server_required": False,
        "real_human_calibration": "NOT_PERFORMED",
        "native_browser_scope": "This harness covers Chromium/Chrome. See separate Firefox and Safari delivery receipts.",
    }
    (out / "OFFLINE_ACCEPTANCE.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    run(
        a.out.resolve()
        if a.out
        else Path(tempfile.mkdtemp(prefix="portable-offline-acceptance-", dir=ROOT / "generated"))
    )
