# ruff: noqa: E501
"""Actual delivery package offline QA, with explicit synthetic runtime shadow bindings."""

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..offline_acceptance import download, fill, profile

ROOT = Path(__file__).resolve().parents[3]


def command(folder, action, cache=None):
    subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "research.gold_studio.bavaria_study.delivery_fixture",
            str(folder),
            action,
            *(["--cache", str(cache)] if cache else []),
        ],
        cwd=ROOT,
        check=True,
    )


def resource_gate():
    if shutil.disk_usage(ROOT).free < 18 * 1024**3 + 512 * 1024**2:
        raise ValueError(
            "Phase C: 18-GiB floor plus 512-MiB bounded offline test allowance required"
        )


def run(folder, version="v2", cache=None):
    folder = Path(folder).resolve()
    resource_gate()
    folder.mkdir(exist_ok=True)
    bindings = []
    for role in ["A", "B"]:
        path = ROOT / f"deliverables/Bavaria_Kalibrierung_Reviewer_{role}_{version}.zip"
        with zipfile.ZipFile(path) as z:
            data = json.loads(
                z.read("package.js").decode().removeprefix("window.STUDIO_PACKAGE=")[:-1]
            )
        b = copy.deepcopy(data["binding"])
        b.update(
            simulation=True,
            package_id="TECH_SHADOW_" + b["package_id"],
            assignment_token="TECHNICAL_NOT_HUMAN_" + role,
        )
        bindings.append(b)
    binding_path = folder / "shadow-bindings.json"
    if binding_path.exists() and json.loads(binding_path.read_text()) != bindings:
        raise ValueError("Delivery packages changed; choose a new test workspace")
    if not binding_path.exists():
        binding_path.write_text(json.dumps(bindings, ensure_ascii=False, indent=2))
    command(folder, "issue", cache)
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", args=["--disk-cache-size=16777216", "--media-cache-size=1048576"]
        )
        for role in ["A", "B", "C"]:
            if (folder / (role + "-return.json")).exists():
                command(folder, role, cache)
                continue
            if role == "C" and not (folder / "C.zip").exists():
                command(folder, "B", cache)
            resource_gate()
            path = (
                folder / "C.zip"
                if role == "C"
                else ROOT / f"deliverables/Bavaria_Kalibrierung_Reviewer_{role}_{version}.zip"
            )
            with tempfile.TemporaryDirectory(prefix="klima-delivery-offline-") as name:
                target = Path(name)
                with zipfile.ZipFile(path) as z:
                    z.extractall(target)
                manifest = json.loads((target / "MANIFEST.json").read_text())
                assert all(
                    hashlib.sha256((target / n).read_bytes()).hexdigest() == h
                    for n, h in manifest["files_sha256"].items()
                )
                ctx = browser.new_context(
                    offline=True, accept_downloads=True, viewport={"width": 1450, "height": 1000}
                )
                if role != "C":
                    ctx.add_init_script(
                        """(()=>{let pkg;Object.defineProperty(window,'STUDIO_PACKAGE',{configurable:true,get:()=>pkg,set:v=>{v.binding=SHADOW;pkg=v;}});})();""".replace(
                            "SHADOW", json.dumps(bindings[0 if role == "A" else 1])
                        )
                    )
                pg = ctx.new_page()
                dialog_policy = {"accept": True}
                pg.on("dialog", lambda d: d.accept() if dialog_policy["accept"] else d.dismiss())
                errors = []
                network = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.on(
                    "request",
                    lambda r: (
                        network.append(r.url) if r.url.startswith(("http:", "https:")) else None
                    ),
                )
                pg.goto((target / "START.html").as_uri())
                if role == "C":
                    pg.locator("#person").fill("TECHNICAL-C")
                    pg.locator("#qualification").fill("SYNTHETIC_TECHNICAL_TEST_NOT_HUMAN")
                    pg.locator("#human").check()
                    pg.locator("#developer").check()
                    pg.get_by_text("Profil speichern und beginnen", exact=True).click()
                    pg.wait_for_selector("#owner:not([hidden])")
                else:
                    profile(pg, role)
                seen_source_types = set()
                if role != "C":
                    if role == "B":
                        result = pg.evaluate(
                            "async text=>{try{await STUDIO_PORTABLE.restore(text);return 'BAD'}catch(e){return e.message}}",
                            (folder / "A-return.json").read_text(),
                        )
                        assert "anderen Person" in result
                    for i in range(48):
                        resource_gate()
                        pg.wait_for_function('()=>!document.querySelector("#lock").disabled')
                        source_type = pg.evaluate(
                            "()=>task.sources.find(s=>s.sha256===document.getElementById('sourceSelect').value).file.endsWith('.pdf')?'PDF':'HTML'"
                        )
                        if source_type == "PDF":
                            pg.wait_for_function(
                                "()=>document.getElementById('pdfPage').complete&&document.getElementById('pdfPage').naturalWidth>0"
                            )
                            if version == "v4":
                                assert pg.evaluate(
                                    "()=>document.getElementById('sourceSelect').value===task.focal_source_sha256"
                                )
                            if "PDF" not in seen_source_types:
                                pg.locator("#pdfPage").screenshot(
                                    path=str(folder / (role + "-PDF-preview.png"))
                                )
                        elif "HTML" not in seen_source_types:
                            with pg.expect_popup() as popup_info:
                                pg.locator("#fullSource").click()
                            popup = popup_info.value
                            popup.wait_for_load_state("domcontentloaded")
                            assert (
                                popup.url.startswith("file:")
                                and popup.locator("body").inner_text().strip()
                            )
                            popup.close()
                        seen_source_types.add(source_type)
                        pg.evaluate(
                            "()=>{for(const n of document.querySelectorAll('#controls [data-key]'))if(['fidelity','document_type','temporal_basis'].includes(n.dataset.key))n.value='unclear';}"
                        )
                        if i == 0 and version != "v2":
                            # A technical outage is a recoverable draft, never a negative lock.
                            pg.locator("#technicalUnavailable").check()
                            fill(pg, role, first=True)
                            pg.wait_for_function("()=>!dirty")
                            assert pg.evaluate("()=>state.locked.length") == 0
                            download(pg, folder / (role + "-technical-draft.json"))
                            pg.reload()
                            pg.wait_for_selector("#technicalUnavailable:checked")
                            # Actual backup recovery, rather than only localStorage reload.
                            pg.evaluate("()=>localStorage.clear()")
                            pg.reload()
                            pg.evaluate(
                                "async text=>{await STUDIO_PORTABLE.restore(text);await STUDIO_REFRESH();}",
                                (folder / (role + "-technical-draft.json")).read_text(),
                            )
                            pg.wait_for_selector("#technicalUnavailable:checked")
                            pg.locator("#technicalUnavailable").uncheck()
                            dialog_policy["accept"] = False
                            fill(pg, role, first=True)
                            assert pg.evaluate("()=>state.locked.length") == 0
                            dialog_policy["accept"] = True
                        fill(pg, role, first=i == 0)
                        try:
                            pg.wait_for_function(
                                "(n)=>state.locked.length===n", arg=i + 1, timeout=5000
                            )
                        except Exception:
                            probe = pg.evaluate(
                                "()=>({stage,task_id:task.id,locked:state.locked.length,technical:document.getElementById('technicalUnavailable')?.checked,form:form(),status:[...document.querySelectorAll('[role=status]')].map(n=>n.textContent)})"
                            )
                            (folder / "FAILURE.json").write_text(
                                json.dumps(
                                    {"simulation": True, "role": role, "index": i, "probe": probe},
                                    indent=2,
                                )
                            )
                            raise
                        if i == 9:
                            download(pg, folder / (role + "-checkpoint.json"))
                            pg.reload()
                            pg.wait_for_function("()=>state.locked.length===10")
                    download(pg, folder / (role + "-return.json"))
                    if role == "A":
                        command(folder, "A", cache)
                else:
                    pg.locator("#conflicts").get_by_role(
                        "radio", name="A", exact=True
                    ).first.click()
                    pg.locator('[data-key="note"]').first.fill("SYNTHETIC TECHNICAL ADJUDICATION")
                    pg.get_by_text(
                        "Adjudikation unveränderlich speichern", exact=True
                    ).first.click()
                    pg.wait_for_function(
                        "()=>document.querySelector('#conflicts').textContent.includes('Abgeschlossen:')"
                    )
                    download(pg, folder / "C-return.json")
                    command(folder, "C", cache)
                assert not errors and not network, (errors, network)
                records.append(
                    {
                        "role": role,
                        "original_zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "files_verified": len(manifest["files_sha256"]),
                        "javascript_errors": errors,
                        "network_requests": network,
                        "simulation_shadow_only": True,
                        "original_source_types_inspected": sorted(seen_source_types),
                        "technical_draft_backup_restore": version != "v2" and role != "C",
                        "explicit_lock_confirmation_cancel_accept": version != "v2" and role != "C",
                    }
                )
                ctx.close()
        browser.close()
    (folder / "BROWSER.json").write_text(
        json.dumps(
            {
                "browser": "installed Chrome",
                "actual_delivery_bytes_verified": True,
                "productive_packages_unmodified": True,
                "technical_shadow_bindings_only": True,
                "records": records,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("folder")
    p.add_argument("--version", choices=["v2", "v3", "v4"], default="v2")
    p.add_argument("--cache", type=Path)
    a = p.parse_args()
    run(a.folder, a.version, a.cache)
