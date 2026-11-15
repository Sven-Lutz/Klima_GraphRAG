# ruff: noqa: E501
"""Offline transport adapter for the existing Studio tasks/forms and append-only Store.
Hashes bind trusted owner-issued packages; they are NOT signatures or reviewer authentication.
"""

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from crawler.core.source_presentation import sanitized_html as sanitized_html

from .prepare import ROOT, prepare, sha
from .store import (
    CHOICES,
    RESERVE,
    STAGES,
    Store,
    canonical,
    digest,
    prerequisites,
    required_stages,
)

SCHEMA = "research-gold-portable-v1"
UI_CONTRACT = "gold-studio-d1-v5-opt-in-qrels"
ENVELOPE = "research-gold-portable-envelope-v1"
STATIC = Path(__file__).parent / "static"


def check_reserve(path, allowance=256 * 1024**2):
    if shutil.disk_usage(path).free < RESERVE + allowance:
        raise ValueError("15-GiB reserve plus bounded export allowance required")


def reject_duplicates(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("Duplicate JSON key")
        result[k] = v
    return result


def decode(envelope):
    if not isinstance(envelope, dict):
        raise ValueError("Wrong result envelope")
    if envelope.get("schema") != ENVELOPE or not isinstance(envelope.get("content"), str):
        raise ValueError("Wrong result schema")
    raw = envelope["content"]
    if len(raw.encode()) > 8 * 1024**2 or hashlib.sha256(raw.encode()).hexdigest() != envelope.get(
        "sha256"
    ):
        raise ValueError("Result checksum or size invalid")
    return json.loads(raw, object_pairs_hook=reject_duplicates)



def preview_pages(bundle, task_ids, source_sha, count):
    related = [
        t
        for t in bundle["tasks"]
        if t["task_id"] in task_ids and source_sha in t["allowed_original_sha256"]
    ]
    if not related or any(t.get("focal_source_sha256") != source_sha for t in related):
        if count > 200:
            raise ValueError("Unfocused PDF requires an explicit preview plan above 200 pages")
        return list(range(1, count + 1))
    pages = {1}
    for t in related:
        locator = str(t.get("focus", {}).get("page_ref") or "")
        if not locator.isdigit() or not 1 <= int(locator) <= count:
            raise ValueError("Explicit physical PDF page required")
        pages.update(
            p for p in [int(locator) - 1, int(locator), int(locator) + 1] if 1 <= p <= count
        )
    if len(pages) > 40:
        raise ValueError("Bounded focal preview page budget exceeded")
    return sorted(pages)


def source_assets(bundle, cache, task_ids=None):
    """Verify originals; preserve complete PDFs, render bounded focal/neighbor previews."""
    ids = {
        s
        for t in bundle["tasks"]
        if t["task_id"] in (task_ids or bundle["selection"]["calibration"])
        for s in t["allowed_original_sha256"]
    }
    sources = {}
    cache.mkdir(parents=True, exist_ok=True)
    for s in bundle["sources"]:
        h = s["sha256"]
        if h not in ids:
            continue
        from .bavaria_study.bundle import source_path

        original = source_path(s)
        if sha(original) != h:
            raise ValueError("Original hash mismatch")
        folder = cache / h
        folder.mkdir(exist_ok=True)
        meta = folder / "source.json"
        if meta.exists():
            record = json.loads(meta.read_text())
            if all(sha(folder / name) == value for name, value in record["asset_hashes"].items()):
                if (folder / "original.html").exists():
                    (folder / "original.html").rename(folder / "original.html.bin")
                    record["archive"] = record["archive"] + ".bin"
                    record["asset_hashes"]["original.html.bin"] = record["asset_hashes"].pop(
                        "original.html"
                    )
                    meta.write_text(json.dumps(record, ensure_ascii=False))
                if original.suffix == ".pdf":
                    needed = preview_pages(
                        bundle,
                        task_ids or bundle["selection"]["calibration"],
                        h,
                        record.get("page_count", max(map(int, record["pages"]))),
                    )
                    if not set(map(str, needed)).issubset(record["pages"]):
                        raise ValueError(
                            "Verified cache lacks required preview pages; use a new explicit cache version"
                        )
                sources[h] = record
                continue
            raise ValueError("Cached portable source integrity failure")
        check_reserve(cache)
        target = folder / ("original.pdf" if original.suffix == ".pdf" else "original.html.bin")
        shutil.copy2(original, target)
        pages: dict[str, dict[str, Any]] = {}
        if original.suffix == ".pdf":
            info = subprocess.check_output(["pdfinfo", str(original)], timeout=20).decode()
            count = int(next(x.split(":")[1] for x in info.splitlines() if x.startswith("Pages:")))
            selected_pages = preview_pages(
                bundle, task_ids or bundle["selection"]["calibration"], h, count
            )
            for i in selected_pages:
                check_reserve(cache)
                text = (
                    subprocess.check_output(
                        ["pdftotext", "-f", str(i), "-l", str(i), "-layout", str(original), "-"],
                        timeout=60,
                    )
                    .decode(errors="replace")
                    .replace("\f", "")
                )
                prefix = folder / ("page-" + str(i))
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        str(i),
                        "-l",
                        str(i),
                        "-singlefile",
                        "-scale-to",
                        "1300",
                        "-jpeg",
                        "-jpegopt",
                        "quality=75",
                        str(original),
                        str(prefix),
                    ],
                    check=True,
                    timeout=120,
                    capture_output=True,
                )
                pages[str(i)] = {"text": text, "image": f"sources/{h}/{prefix.name}.jpg"}
            view = f"sources/{h}/{target.name}"
        else:
            raw = original.read_bytes()
            soup = BeautifulSoup(raw, "html.parser")
            for node in soup(["script", "style", "noscript", "iframe", "object", "form", "svg"]):
                node.decompose()
            full = soup.get_text("\n", strip=True)
            passive = folder / "view.html"
            passive.write_text(sanitized_html(raw))
            pages["1"] = {"text": full, "image": None}
            view = f"sources/{h}/view.html"
        record = {
            **{k: s[k] for k in ["sha256", "file", "url", "frame_ags", "claim_scope"] if k in s},
            "pages": pages,
            "page_count": count if original.suffix == ".pdf" else 1,
            "preview_policy": "focal-neighbors-or-legacy-full; complete PDF always included",
            "view": view,
            "archive": f"sources/{h}/{target.name}",
            "asset_hashes": {p.name: sha(p) for p in folder.iterdir() if p.is_file()},
        }
        meta.write_text(json.dumps(record, ensure_ascii=False))
        sources[h] = record
    return sources


def export_package(store, role, output, cache, rights=None, batch=None):
    if role not in ["A", "B", "C"]:
        raise ValueError("Invalid role")
    if store.meta("phase") == "main":
        freezes = store.events("C", "portable_main_freeze")
        if not freezes or freezes[-1]["payload"].get("selection_sha256") != digest(
            store.bundle["selection"]
        ):
            raise ValueError(
                "Main export requires actual human calibration and explicit task/dependency freeze"
            )
        task_ids = freezes[-1]["payload"]["task_ids"]
    else:
        task_ids = store.bundle["selection"]["calibration"]
    if batch:
        if store.meta("phase") != "main" or role == "C":
            raise ValueError("Batches only after main freeze, for A/B")
        plan = next((b for b in store.bundle.get("batches", []) if b["batch_id"] == batch), None)
        if not plan or not set(plan["task_ids"]).issubset(task_ids):
            raise ValueError("Unknown or unfrozen batch")
        task_ids = plan["task_ids"]
    if role == "C" and not all(store.locked(r) for r in ["A", "B"]):
        raise ValueError("Both complete reviews required before adjudication package")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    check_reserve(output.parent)
    if output.exists():
        raise ValueError("Refuse to overwrite an issued package")
    assets = source_assets(store.bundle, Path(cache), task_ids)
    selected = []
    for t in store.tasks():
        if t["task_id"] not in task_ids:
            continue
        if (
            store.meta("phase") == "calibration"
            and t.get("effective_split") != "DEVELOPMENT_EXPOSED"
        ):
            raise ValueError("Quarantine export refused")
        selected.append(
            {
                k: t[k]
                for k in [
                    "task_id",
                    "question",
                    "allowed_original_sha256",
                    "pool",
                    "field_options",
                    "required_stages",
                    "focus",
                    "quality_review",
                    "primary_purpose",
                    "focal_source_sha256",
                    "pool_status",
                    "qrel_contract",
                    "retrieval_class",
                ]
                if k in t
            }
        )
    answers = [
        {k: a[k] for k in ["neutral_answer_id", "question", "candidate_answer", "citations"]}
        for a in store.bundle["answers"]
        if any(t["question"] == a["question"] for t in selected)
    ]
    binding = {
        "schema": SCHEMA,
        "package_id": secrets.token_hex(16),
        "reviewer": role,
        "assignment_token": secrets.token_urlsafe(32),
        "task_schema": "gold-studio-forms-v1",
        "ui_contract": UI_CONTRACT,
        "batch_id": batch,
        "phase": store.meta("phase"),
        "task_ids": [t["task_id"] for t in selected],
        "selection_sha256": digest(store.bundle["selection"]),
        "tasks_sha256": digest(selected),
        "sources": sorted(assets),
        "simulation": store.simulation,
        "distribution": "LOCAL_PREVIEW_NOT_CLEARED",
        "rights_receipt": None,
    }
    if rights:
        permit = json.loads(Path(rights).read_text())
        if (
            permit.get("approved_for_external_distribution") is not True
            or not permit.get("owner")
            or not permit.get("date")
            or sorted(permit.get("source_sha256", [])) != sorted(assets)
        ):
            raise ValueError("Explicit owner rights receipt required for every source")
        binding.update(distribution="OWNER_CLEARED_PRIVATE_REVIEW", rights_receipt=sha(rights))
    data = {
        "binding": binding,
        "tasks": selected,
        "sources": assets,
        "answers": answers,
        "choices": CHOICES,
        "stages": STAGES,
        "municipalities": [],
    }
    if role == "C":
        data.update(conflicts=store.conflicts(), report=store.report())
    js = (STATIC / "studio.js").read_text()
    css = (STATIC / "studio.css").read_text()
    adapter = (STATIC / "portable.js").read_text()
    page = (
        (STATIC / "index.html")
        .read_text()
        .replace('href="/assets/studio.css"', 'href="studio.css"')
        .replace('src="/assets/studio.js"', 'src="studio.js"')
    )
    page = page.replace(
        '<script defer="" src="studio.js">',
        '<script defer src="package.js"></script><script defer src="portable.js"></script><script defer="" src="studio.js">',
    )
    page = page.replace('href="/"', 'href="START.html"')
    page = page.replace(
        "<body>",
        """<body><section class="card" id="portableToolbar"><strong id="portableMode"></strong><p id="persistence">Geprüfte Browser: Chrome / Chromium und Firefox. Safari nicht abgenommen. Browserdaten sind kein verlässliches Datei-Backup. Regelmäßig Backup exportieren; beim Browserwechsel wiederherstellen.</p><button id="backup">Backup / Ergebnisse exportieren</button><label>Backup wiederherstellen <input type="file" id="restore" accept="application/json,.json"></label><button id="verifyBackup">Geladene Backupdatei prüfen</button><span id="backupStatus" role="status"></span><a href="Reviewer_Anleitung.pdf" target="_blank">PDF-Anleitung</a></section>""",
    )
    page = page.replace(
        "Autospeichern ist aktiv. Abschließen sperrt das Urteil unveränderlich.",
        "Zwischenstände werden im Browser versucht zu speichern. Datei-Backup ausdrücklich exportieren. Abschließen sperrt das Urteil.",
    )
    page = page.replace(
        "Nur auf diesem Rechner. Kein Upload, keine Modellaufrufe.",
        "Offline-Paket: keine Übertragung, keine Modellaufrufe. Keine fremden Urteile enthalten.",
    )
    page = page.replace(
        "<head>",
        "<head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'self' file:; style-src 'self' file:; img-src 'self' file: data:; frame-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'\">",
    )
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        tmp = Path(tmp)
        for name, content in [
            ("START.html", page),
            ("studio.js", js),
            ("studio.css", css),
            ("portable.js", adapter),
            (
                "package.js",
                "window.STUDIO_PACKAGE="
                + json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
                + ";",
            ),
        ]:
            (tmp / name).write_text(content)
        for h in assets:
            shutil.copytree(Path(cache) / h, tmp / "sources" / h)
        guide = ROOT / "deliverables/Reviewer_Quick_Start.pdf"
        if not guide.exists():
            guide = ROOT / "deliverables/Reviewer_Anleitung.pdf"
        if not guide.exists():
            raise ValueError("Generate reviewer PDF guide before packaging")
        shutil.copy2(guide, tmp / "Reviewer_Anleitung.pdf")
        pilot = ROOT / "research/gold_studio/bavaria_study/phase_c/CALIBRATION_PILOT_GUIDE.md"
        if pilot.exists() and store.meta("phase") == "calibration":
            shutil.copy2(pilot, tmp / "Kalibrierungsleitfaden.md")
        (tmp / "LIES_MICH.txt").write_text(
            "START.html per Doppelklick öffnen. Paket vollständig entpacken. Nur eigener Reviewerzugang. Regelmäßig Backup exportieren und wiederherstellen. "
            + binding["distribution"]
            + "\nOhne Owner-Freigabe nicht extern weitergeben. SHA-256 ist keine Signatur oder Identitätsprüfung.\n"
        )
        files = {str(p.relative_to(tmp)): sha(p) for p in tmp.rglob("*") if p.is_file()}
        manifest = {**binding, "files_sha256": files}
        (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(tmp.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(tmp))
    with store.conn() as c:
        store.event(
            c,
            "C",
            "",
            "portable",
            "portable_issue",
            {"binding": binding, "manifest": manifest, "zip_sha256": sha(output)},
        )
    return manifest


def validate_source_bindings(value, allowed, depth=0):
    """All nested relation/claim evidence must stay inside the task's original set."""
    if depth > 32:
        raise ValueError("Evidence structure too deeply nested")
    if isinstance(value, dict):
        if "source_sha256" in value and value["source_sha256"] not in allowed:
            raise ValueError("Foreign source in nested evidence")
        for item in value.values():
            validate_source_bindings(item, allowed, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_source_bindings(item, allowed, depth + 1)


def import_result(store, envelope):
    data = decode(envelope)
    if not isinstance(data, dict):
        raise ValueError("Wrong result object")
    binding = data.get("binding", {})
    if not isinstance(binding, dict):
        raise ValueError("Wrong package binding")
    role = binding.get("reviewer")
    issued = next(
        (
            e["payload"]
            for e in store.events("C", "portable_issue")
            if e["payload"]["binding"] == binding
        ),
        None,
    )
    if not issued or role not in ["A", "B", "C"]:
        raise ValueError("Unknown, foreign or modified package identity")
    if binding["simulation"] != store.simulation:
        raise ValueError("Simulation boundary mismatch")
    if digest(store.bundle["selection"]) != binding["selection_sha256"]:
        raise ValueError("Selection changed")
    tasks = {t["task_id"]: t for t in store.tasks() if t["task_id"] in binding["task_ids"]}
    if list(tasks) != binding["task_ids"]:
        raise ValueError("Task set changed")
    # Check originals against the trusted local archive, never against attacker-supplied paths.
    records = {s["sha256"]: s for s in store.bundle["sources"]}
    for h in binding["sources"]:
        from .bavaria_study.bundle import source_path

        if sha(source_path(records[h])) != h:
            raise ValueError("Original source version mismatch")
    receipt_id = envelope["sha256"]
    if any(
        e["payload"].get("content_sha256") == receipt_id
        for e in store.events("C", "portable_import")
    ):
        return {
            "status": "ALREADY_IMPORTED",
            "missing": sum(
                not store.latest(role, tid, stage)
                for tid in tasks
                for stage in required_stages(tasks[tid])
            )
            if role in ["A", "B"]
            else 0,
        }
    profile = data.get("profile")
    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("name"), str)
        or not profile["name"].strip()
        or not isinstance(profile.get("qualification"), str)
        or not profile["qualification"].strip()
        or any(not isinstance(profile.get(k), bool) for k in ["human", "developer", "independent"])
    ):
        raise ValueError("Reviewer profile incomplete")
    if store.simulation:
        profile = {**profile, "human": False, "independent": False}
    elif profile["human"] is not True:
        raise ValueError("Real human attestation required")
    other = store.profile("B" if role == "A" else "A")
    if (
        role in ["A", "B"]
        and other
        and other["name"].strip().casefold() == profile["name"].strip().casefold()
    ):
        raise ValueError("Distinct reviewers required")
    if store.profile(role) and store.profile(role) != profile:
        raise ValueError("Immutable reviewer identity mismatch")
    events = data.get("events", [])
    if not isinstance(events, list):
        raise ValueError("Events must be a list")
    feedback = data.get("feedback", [])
    if not isinstance(feedback, list) or any(
        not isinstance(f, dict)
        or type(f.get("clarity")) is not int
        or not 1 <= f["clarity"] <= 5
        or type(f.get("questions")) is not int
        or f["questions"] < 0
        or not isinstance(f.get("comment"), str)
        for f in feedback
    ):
        raise ValueError("Invalid calibration feedback")
    if len(events) > 10000:
        raise ValueError("Too many events")
    ids = set()
    seen_locks = set()
    locks = {
        (e["task"], e["stage"]): e["payload"]
        for e in store.events(role, "lock")
        if e["task"] in tasks
    }
    todo = []
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or e.get("id") in ids:
            raise ValueError("Duplicate event ID")
        ids.add(e["id"])
        task = e.get("task")
        stage = e.get("stage")
        kind = e.get("kind")
        p = e.get("payload")
        if (
            task not in tasks
            or stage not in required_stages(tasks[task])
            or kind not in ["draft", "lock", "adjudicate"]
        ):
            raise ValueError("Invalid task/stage/event")
        if not isinstance(p, dict) or len(canonical(p)) > 256000:
            raise ValueError("Invalid form size")
        if role == "C":
            if kind != "adjudicate" or e.get("task") not in binding["task_ids"]:
                raise ValueError("Invalid adjudication")
            if kind != "adjudicate":
                raise ValueError("Invalid adjudication event")
            if (task, stage) in seen_locks:
                raise ValueError("Duplicate adjudication")
            seen_locks.add((task, stage))
            todo.append({**e, "payload": store.adjudication_payload(task, stage, p)})
            continue
        if kind == "adjudicate":
            raise ValueError("Reviewer cannot adjudicate")
        if kind == "lock":
            if (task, stage) in seen_locks:
                raise ValueError("Duplicate judgment")
            seen_locks.add((task, stage))
        if p.get("source_sha256") not in tasks[task]["allowed_original_sha256"]:
            raise ValueError("Foreign source")
        validate_source_bindings(p, set(tasks[task]["allowed_original_sha256"]))
        if not isinstance(p.get("spans", []), list) or any(
            not isinstance(x, dict) for x in p.get("spans", [])
        ):
            raise ValueError("Invalid source spans")
        for span in p.get("spans", []):
            if span.get("source_sha256") not in tasks[task]["allowed_original_sha256"]:
                raise ValueError("Foreign evidence span")
        if (task, stage) in locks:
            if kind == "lock" and locks[task, stage] != p:
                raise ValueError("Attempt to overwrite immutable judgment")
            if kind == "lock":
                continue
            # Historical drafts preceding an identical lock are safe on idempotent reimport.
            continue
        if kind == "lock":
            store.validate(stage, p, tasks[task])
            for prior_tid, earlier in prerequisites(list(tasks.values()), tasks[task], stage):
                if (prior_tid, earlier) not in locks:
                    raise ValueError("Source-first round order violated")
            locks[task, stage] = p
        todo.append(e)
    receipt_id = envelope["sha256"]
    if any(
        e["payload"].get("content_sha256") == receipt_id
        for e in store.events("C", "portable_import")
    ):
        return {
            "status": "ALREADY_IMPORTED",
            "missing": sum(len(required_stages(t)) for t in tasks.values()) - len(locks),
        }
    archives = store.path.parent / "portable-imports"
    archives.mkdir(exist_ok=True)
    archived = archives / (receipt_id + ".json")
    encoded = canonical(envelope)
    if archived.exists():
        if archived.read_text() != encoded:
            raise ValueError("Archived envelope integrity mismatch")
    else:
        check_reserve(archives, 16 * 1024**2)
        with archived.open("x") as f:
            f.write(encoded)
        archived.chmod(0o400)
    with store.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if role == "C" and any(
            store.latest("C", e["task"], e["stage"], "adjudicate") for e in todo
        ):
            raise ValueError("Concurrent adjudication change")
        # Locks/profiles may have changed in a concurrent native UI session: fail without partial import.
        if role in ["A", "B"] and any(
            store.latest(role, e["task"], e["stage"]) for e in todo if e["kind"] == "lock"
        ):
            raise ValueError("Concurrent judgment change; retry from a fresh export")
        if not store.profile(role):
            store.event(c, role, "", "profile", "profile", profile)
        for e in todo:
            store.event(c, role, e["task"], e["stage"], e["kind"], e["payload"])
        for f in data.get("feedback", []):
            store.event(c, role, "", "calibration", "feedback", f)
        receipt = {
            "content_sha256": receipt_id,
            "package_id": binding["package_id"],
            "reviewer": role,
            "simulation": store.simulation,
            "missing": sum(len(required_stages(t)) for t in tasks.values()) - len(locks)
            if role in ["A", "B"]
            else sum(not x["resolution"] for x in store.conflicts()) - len(todo),
            "imported_events": len(todo),
            "client_history_archive": str(archived.relative_to(store.path.parent)),
            "client_history_sha256": sha(archived),
            "gold_status": "NOT_RELEASED",
        }
        store.event(c, "C", "", "portable", "portable_import", receipt)
    store.audit()
    return receipt


def freeze_main(store, attestation):
    if store.simulation or store.meta("phase") != "main" or not store.events("C", "phase_release"):
        raise ValueError("Actual reviewed calibration and native main release required")
    for role in ["A", "B"]:
        p = store.profile(role)
        if (
            not p
            or not p.get("human")
            or not p.get("independent")
            or not store.events(role, "feedback")
        ):
            raise ValueError("Actual independent profiles and human calibration feedback required")
        for tid in store.bundle["selection"]["calibration"]:
            if not all(store.latest(role, tid, stage) for stage in STAGES):
                raise ValueError("Human calibration incomplete")
    task_ids = [
        t["task_id"]
        for t in store.tasks()
        if t["task_id"] not in store.bundle["selection"]["calibration"]
    ]
    if (
        attestation.get("task_ids") != task_ids
        or not attestation.get("owner")
        or not attestation.get("date")
        or not all(
            attestation.get(k) is True
            for k in ["dependency_clearance", "rights_confirmed", "calibration_discussed"]
        )
    ):
        raise ValueError(
            "Exact task list and owner dependency/rights/calibration attestation required"
        )
    payload = {
        **attestation,
        "selection_sha256": digest(store.bundle["selection"]),
        "historical_splits_unchanged": True,
    }
    with store.conn() as c:
        store.event(c, "C", "", "portable", "portable_main_freeze", payload)
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["export", "import", "prepare-adjudication", "freeze-main"])
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--role", choices=["A", "B", "C"])
    ap.add_argument("--output", type=Path)
    ap.add_argument("--input", type=Path)
    ap.add_argument("--batch", help="Owner-frozen main batch ID")
    ap.add_argument("--study", type=Path)
    ap.add_argument("--rights", type=Path)
    ap.add_argument("--cache", type=Path, help="Explicit verified portable-source cache")
    ap.add_argument("--simulation", action="store_true")
    a = ap.parse_args()
    from .bavaria_study.bundle import load_bundle

    store = Store(
        a.workspace / "annotations.sqlite",
        load_bundle(a.study) if a.study else prepare(),
        a.simulation,
    )
    if a.command == "export":
        print(
            json.dumps(
                {
                    "package": str(a.output),
                    "binding": export_package(
                        store,
                        a.role,
                        a.output,
                        a.cache or ROOT / "generated/portable-source-cache-v1",
                        a.rights,
                        a.batch,
                    )["package_id"],
                }
            )
        )
    elif a.command == "import":
        print(json.dumps(import_result(store, json.loads(a.input.read_text()))))
    elif a.command == "freeze-main":
        print(json.dumps(freeze_main(store, json.loads(a.input.read_text()))))
    else:
        if a.output.exists():
            raise ValueError("Output exists")
        with zipfile.ZipFile(a.output, "x", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "START.html",
                '<!doctype html><meta charset="utf-8"><h1>Adjudikation noch gesperrt</h1><p>Erst beide vollständigen unabhängigen Einzelreviews importieren, danach das C-Paket aus demselben Studio exportieren. Dieses Vorbereitungspaket enthält keine Urteile und ist kein Research Gold.</p>',
            )
            z.writestr(
                "ABLAUF.txt",
                "A und B importieren; store.conflicts prüft Vollständigkeit; python -m research.gold_studio.portable export --role C --workspace WORKSPACE --output Adjudikation.zip. Keine Hauptreview-Freigabe vor menschlicher Kalibrierung.",
            )


if __name__ == "__main__":
    main()
