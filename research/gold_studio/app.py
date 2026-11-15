# ruff: noqa: E501
"""Local FastAPI browser adapter for the frozen source-first reviewer packet."""

import json
import secrets
import shutil
import sqlite3
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .prepare import PACKET, ROOT, focus_source, prepare
from .store import CHOICES, RESERVE, STAGES, Store, digest, prerequisites, required_stages

ASSETS = Path(__file__).with_name("static")


def create_app(workspace, simulation=False, study=None):
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if study:
        from .bavaria_study.bundle import load_bundle

        bundle = load_bundle(study)
    else:
        bundle = prepare()
    store = Store(workspace / "annotations.sqlite", bundle, simulation)
    sources = {s["sha256"]: s for s in bundle["sources"]}
    tasks = {t["task_id"]: t for t in bundle["tasks"]}
    with sqlite3.connect(
        (ROOT / "generated/bavaria/acquisition.sqlite").resolve().as_uri() + "?mode=ro", uri=True
    ) as registry:
        municipalities = [
            json.loads(row[0])
            for row in registry.execute("SELECT record FROM municipalities ORDER BY ags")
        ]
    official_ags = {row["ags"] for row in municipalities}
    keys = json.loads(store.meta("keys"))
    sessions: dict[str, str] = {}
    # Access keys remain owner-local; never exposed by a reviewer endpoint.
    access = workspace / "Zugangscodes.txt"
    if not access.exists():
        access.write_text(
            "Lokal vertraulich. A und B getrennt weitergeben. C verwaltet und adjudiziert.\n"
            + "\n".join(f"{role}: {key}" for role, key in keys.items())
        )
        access.chmod(0o600)
    (workspace / "selection.json").write_text(json.dumps(bundle["selection"], indent=2))
    app = FastAPI(title="Klima Research Gold Studio", docs_url=None, redoc_url=None)
    app.state.store = store
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

    @app.middleware("http")
    async def local_only(request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Foreign origin denied"}, status_code=403)
        if int(request.headers.get("content-length", "0")) > (
            9 * 1024**2 if request.url.path == "/api/portable/import" else 512000
        ):
            return JSONResponse({"detail": "Form too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def role(request, owner=False):
        r = sessions.get(request.cookies.get("studio_session") or "")
        expected = request.headers.get("x-studio-role")
        if expected and expected != r:
            raise HTTPException(
                403, "Rollenwechsel in anderem Tab; bitte mit eigenem Zugang neu anmelden"
            )
        if not r or (owner and r != "C"):
            raise HTTPException(403, "Bitte mit eigenem Zugang anmelden.")
        return r

    def authorize_source(sha):
        if not any(sha in t["allowed_original_sha256"] for t in store.tasks()):
            raise HTTPException(403, "Quelle außerhalb der freigegebenen Runde")

    def original(sha):
        if sha not in sources:
            raise HTTPException(404, "Unbekannte Originalversion")
        from .bavaria_study.bundle import source_path

        return source_path(sources[sha])

    def visible(sha, page=1, reading=False):
        p = original(sha)
        if p.suffix == ".pdf":
            if not 1 <= page <= 500:
                raise ValueError("Seite außerhalb des sicheren Bereichs")
            return subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(p), "-"],
                capture_output=True,
                check=True,
                timeout=15,
            ).stdout.decode()
        soup = BeautifulSoup(p.read_bytes(), "html.parser")
        for n in soup(["script", "style", "noscript", "iframe", "object", "form", "svg"]):
            n.decompose()
        if reading:
            for node in soup(["nav", "header", "footer"]):
                node.decompose()
            main = soup.find("main")
            if main is not None:
                soup = main
        return soup.get_text("\n", strip=True)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        r = sessions.get(request.cookies.get("studio_session") or "")
        if r:
            try:
                with store.conn() as c:
                    store.event(
                        c,
                        r,
                        "",
                        "validation",
                        "validation_error",
                        {"path": request.url.path, "message": str(exc)},
                    )
            except (ValueError, sqlite3.Error):
                pass  # reserve/storage failure must not recurse while reporting the original error
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/")
    def home():
        return FileResponse(ASSETS / "index.html")

    @app.post("/api/login")
    async def login(request: Request):
        p = await request.json()
        r = p.get("role")
        if r not in keys or not secrets.compare_digest(str(p.get("code", "")), keys[r]):
            raise HTTPException(403, "Zugangscode stimmt nicht.")
        token = secrets.token_urlsafe(32)
        sessions[token] = r
        response = JSONResponse({"role": r})
        response.set_cookie("studio_session", token, httponly=True, samesite="strict")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        role(request)
        sessions.pop(request.cookies.get("studio_session") or "", None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("studio_session")
        return response

    @app.get("/api/state")
    def state(request: Request):
        r = role(request)
        items = store.tasks()
        done = [
            e
            for e in store.events(r, "lock")
            if any(t["task_id"] == e["task"] and e["stage"] in required_stages(t) for t in items)
        ]
        return {
            "role": r,
            "simulation": simulation,
            "phase": store.meta("phase"),
            "mode": store.meta("mode"),
            "profile": store.profile(r),
            "choices": CHOICES,
            "stages": STAGES,
            "batches": store.bundle.get("batches", []) if r == "C" else [],
            "tasks": [
                {
                    "id": t["task_id"],
                    "question": t["question"],
                    "cohort": t.get("cohort", "prospective"),
                    "required_stages": required_stages(t),
                }
                for t in items
            ],
            "locked": [{"task": x["task"], "stage": x["stage"]} for x in done],
            "adjudication_ready": all(store.locked(x) for x in ["A", "B"]) if r == "C" else None,
        }

    @app.get("/api/municipalities")
    def municipality_choices(request: Request):
        role(request)
        return [
            {"ags": row["ags"], "name": row["name"], "region": row["region"]}
            for row in municipalities
        ]

    @app.post("/api/profile")
    async def profile(request: Request):
        r = role(request)
        p = await request.json()
        if simulation:
            p["human"] = False
            p["independent"] = False
        if (
            not p.get("name", "").strip()
            or not p.get("qualification", "").strip()
            or (not simulation and p.get("human") is not True)
        ):
            raise ValueError("Name, fachlicher Hintergrund und menschliche Teilnahme bestätigen.")
        if not isinstance(p.get("developer"), bool) or not isinstance(p.get("independent"), bool):
            raise ValueError("Entwicklerrolle und Unabhängigkeit angeben.")
        if r in ["A", "B"]:
            other = store.profile("B" if r == "A" else "A")
            if other and other["name"].strip().casefold() == p["name"].strip().casefold():
                raise ValueError("A und B benötigen unterschiedliche tatsächliche Personen")
        if store.events(r, "lock") or store.events(r, "adjudicate"):
            if store.profile(r) != p:
                raise ValueError(
                    "Profil nach gesperrten Urteilen unveränderlich; neue Studie für Rollenwechsel"
                )
            return {"ok": True}
        with store.conn() as c:
            store.event(c, r, "", "profile", "profile", p)
        return {"ok": True}

    @app.get("/api/task/{tid}")
    def task(tid: str, request: Request, stage: str = "source"):
        r = role(request)
        if tid not in {t["task_id"] for t in store.tasks()} or stage not in STAGES:
            raise HTTPException(404)
        if r == "C":
            if not all(store.locked(x) for x in ["A", "B"]):
                raise HTTPException(403, "Einzelreviews noch offen")
        else:
            for task_id, earlier in prerequisites(store.tasks(), tasks[tid], stage):
                if not store.latest(r, task_id, earlier):
                    raise HTTPException(403, "Vorherige Runde zuerst abschließen")
        t = tasks[tid]
        allowed = t["allowed_original_sha256"]
        order = list(dict.fromkeys([x["source_sha256"] for x in t["pool"]] + allowed))
        result = {
            "id": tid,
            "question": t["question"],
            "stage": stage,
            "sources": [sources[s] for s in order if s in sources],
            "pool": t["pool"] if stage == "retrieval" else [],
            "field_options": t.get("field_options", {}),
            "quality_review": t.get("quality_review", False),
            "primary_purpose": t.get("primary_purpose"),
            "focus": t.get("focus"),
            "focal_source_sha256": focus_source(t),
            "pool_status": t.get("pool_status"),
            "qrel_contract": t.get("qrel_contract"),
            "retrieval_class": t.get("retrieval_class"),
            "required_stages": required_stages(t),
            "navigation_pages": {
                sha: sorted(
                    {
                        int(x["page"])
                        for x in t["pool"]
                        if x["source_sha256"] == sha and str(x.get("page") or "").isdigit()
                    }
                )
                for sha in allowed
            },
            "answers": store.answers_for(t) if stage == "answer" else [],
            "draft": store.latest(r, tid, stage, "adjudication_draft" if r == "C" else "draft"),
            "locked": store.latest(r, tid, stage),
        }
        return result

    @app.get("/api/original/{sha}/text")
    def text(sha: str, request: Request, page: int = 1):
        role(request)
        authorize_source(sha)
        return {
            "text": visible(sha, page, reading=True),
            "sha256": sha,
            "page": page,
            "metadata": sources[sha],
        }

    @app.get("/api/original/{sha}/view")
    def original_view(sha: str, request: Request):
        role(request)
        authorize_source(sha)
        p = original(sha)
        if p.suffix == ".pdf":
            return FileResponse(p, media_type="application/pdf")
        soup = BeautifulSoup(p.read_bytes(), "html.parser")
        for node in soup(
            [
                "script",
                "style",
                "noscript",
                "iframe",
                "object",
                "embed",
                "form",
                "input",
                "button",
                "link",
                "meta",
                "base",
                "svg",
                "img",
                "audio",
                "video",
            ]
        ):
            node.decompose()
        for node in soup.find_all(True):
            node.attrs = {k: v for k, v in node.attrs.items() if k in ["colspan", "rowspan"]}
        body = soup.body or soup
        return HTMLResponse(
            '<!doctype html><html lang="de"><meta charset="utf-8"><title>Archiviertes Original · passive Ansicht</title><link rel="stylesheet" href="/assets/studio.css"><body><main class="card"><h1>Archiviertes Original · passive Ansicht</h1><p>Skripte, externe Inhalte und Navigation sind deaktiviert. Tabellen und Textstruktur bleiben soweit möglich erhalten.</p>'
            + str(body)
            + "</main></body></html>"
        )

    @app.get("/api/original/{sha}/pdf")
    def pdf(sha: str, request: Request):
        role(request)
        authorize_source(sha)
        p = original(sha)
        if p.suffix != ".pdf":
            raise HTTPException(404)
        return FileResponse(p, media_type="application/pdf")

    @app.get("/api/original/{sha}/page/{page}")
    def page_image(sha: str, page: int, request: Request):
        role(request)
        authorize_source(sha)
        p = original(sha)
        if p.suffix != ".pdf" or not 1 <= page <= 500:
            raise HTTPException(404)
        cache = workspace / "pages"
        cache.mkdir(exist_ok=True)
        prefix = cache / f"{sha}-{page}"
        if not prefix.with_suffix(".png").exists():
            if shutil.disk_usage(workspace).free < RESERVE + 32 * 1024**2:
                raise ValueError("Speicherreserve")
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-scale-to",
                    "1500",
                    "-singlefile",
                    "-png",
                    str(p),
                    str(prefix),
                ],
                capture_output=True,
                check=True,
                timeout=25,
            )
        return FileResponse(prefix.with_suffix(".png"))

    @app.post("/api/judgment/{tid}/{stage}")
    async def judgment(tid: str, stage: str, request: Request):
        r = role(request)
        p = await request.json()
        form = p.get("form", {})
        if stage == "source" and any(ags not in official_ags for ags in form.get("ags", [])):
            raise ValueError(
                "Bitte Gemeinde aus der amtlichen Namensliste wählen oder Geltung unbekannt lassen"
            )
        if p.get("lock"):
            # Span identity is checked against original rendering, not a model reference.
            spans = list(form.get("spans", [])) + [
                span
                for relation in form.get("relations", [])
                for span in relation.get("evidence_spans", [])
            ]
            if form.get("quote"):
                spans.append(
                    {
                        "source_sha256": form.get("source_sha256"),
                        "physical_page": form.get("page"),
                        "verbatim_quote": form["quote"],
                    }
                )
            for span in spans:
                sha = span.get("source_sha256")
                if (
                    not isinstance(span.get("verbatim_quote"), str)
                    or not span["verbatim_quote"].strip()
                ):
                    raise ValueError("Leere oder ungültige Originalmarkierung")
                if sha not in tasks[tid]["allowed_original_sha256"]:
                    raise ValueError("Beleg außerhalb des Quellenumfangs")
                actual = " ".join(visible(sha, span.get("physical_page") or 1).split())
                quote = " ".join(span.get("verbatim_quote", "").split())
                if not quote or quote not in actual:
                    raise ValueError("Markierung nicht im gewählten Originalabschnitt gefunden")
            if (
                stage == "source"
                and form.get("answerability") in ["ANSWERABLE", "PARTIALLY_ANSWERABLE"]
                and not spans
            ):
                raise ValueError(
                    "Bei beantwortbaren Fällen mindestens eine Originalpassage markieren"
                )
        store.save(r, tid, stage, form, bool(p.get("lock")))
        return {"saved": True, "locked": bool(p.get("lock"))}

    @app.post("/api/calibration-feedback")
    async def feedback(request: Request):
        r = role(request)
        if r not in ["A", "B"] or store.meta("phase") != "calibration" or not store.locked(r):
            raise ValueError("Zuerst die eigene Kalibrierung abschließen")
        p = await request.json()
        if (
            p.get("clarity") not in [1, 2, 3, 4, 5]
            or type(p.get("questions")) is not int
            or p["questions"] < 0
        ):
            raise ValueError("Verständlichkeit und tatsächliche Rückfragenzahl angeben")
        if not p.get("comment", "").strip() and p["clarity"] <= 2:
            raise ValueError("Bitte das Verständlichkeitsproblem kurz benennen")
        p["technical_simulation"] = simulation
        with store.conn() as c:
            store.event(c, r, "", "calibration", "feedback", p)
        return {"ok": True}

    @app.get("/api/conflicts")
    def conflicts(request: Request):
        role(request, True)
        return store.conflicts()

    @app.post("/api/adjudicate/{tid}/{stage}")
    async def adjudicate(tid: str, stage: str, request: Request):
        role(request, True)
        if not store.profile("C"):
            raise ValueError("Adjudicator-Profil fehlt")
        data = await request.json()
        if data.get("choice") == "custom":
            form = data.get("custom_payload", {})
            for span in (
                form.get("spans", [])
                + [
                    span
                    for relation in form.get("relations", [])
                    for span in relation.get("evidence_spans", [])
                ]
            ) + (
                [
                    {
                        "source_sha256": form.get("source_sha256"),
                        "physical_page": form.get("page"),
                        "verbatim_quote": form["quote"],
                    }
                ]
                if form.get("quote")
                else []
            ):
                sha = span.get("source_sha256")
                if (
                    not isinstance(span.get("verbatim_quote"), str)
                    or not span["verbatim_quote"].strip()
                ):
                    raise ValueError("Leere oder ungültige Originalmarkierung")
                if sha not in tasks[tid]["allowed_original_sha256"] or " ".join(
                    span.get("verbatim_quote", "").split()
                ) not in " ".join(visible(sha, span.get("physical_page") or 1).split()):
                    raise ValueError("Unbelegte eigene Auflösung")
        store.adjudicate(tid, stage, data)
        return {"ok": True}

    @app.post("/api/adjudraft/{tid}/{stage}")
    async def adjudraft(tid: str, stage: str, request: Request):
        role(request, True)
        if not any(
            x["task_id"] == tid and x["stage"] == stage and not x["resolution"]
            for x in store.conflicts()
        ):
            raise ValueError("Kein offener Konflikt")
        data = await request.json()
        with store.conn() as c:
            store.event(c, "C", tid, stage, "adjudication_draft", data)
        return {"saved": True}

    @app.get("/api/report")
    def report(request: Request):
        role(request, True)
        return store.report()

    @app.post("/api/main")
    async def main_round(request: Request):
        role(request, True)
        p = await request.json()
        if not p.get("calibration_reviewed") or not p.get("rights_confirmed"):
            raise ValueError("Kalibrierung und Zugriffsrechte bestätigen")
        if not all(store.locked(r) for r in ["A", "B"]):
            raise ValueError("Kalibrierung nicht abgeschlossen")
        if simulation:
            raise ValueError("Technische Simulation wird nicht zum menschlichen Hauptreview")
        if not all(store.events(r, "feedback") for r in ["A", "B"]):
            raise ValueError("Tatsächliches Kalibrierungsfeedback fehlt")
        if any(not x["resolution"] for x in store.conflicts()):
            raise ValueError("Kalibrierungskonflikte offen")
        with store.conn() as c:
            store.event(c, "C", "", "main", "phase_release", p)
            c.execute("UPDATE metadata SET value='main' WHERE key='phase'")
            c.execute(
                "UPDATE metadata SET value=? WHERE key='mode'",
                ("two" if p.get("two_person") else "three",),
            )
        return {"ok": True}

    @app.get("/api/export")
    def export(request: Request):
        role(request, True)
        return Response(
            json.dumps(store.export(), ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="research-review-export.json"'},
        )

    @app.post("/api/portable/export/{reviewer}")
    def portable_export(reviewer: str, request: Request, batch: str | None = None):
        role(request, True)
        from .portable import export_package

        folder = workspace / "portable"
        folder.mkdir(exist_ok=True)
        target = folder / (
            "Kalibrierung_Reviewer_" + reviewer + "_" + secrets.token_hex(6) + ".zip"
        )
        export_package(
            store, reviewer, target, ROOT / "generated/portable-source-cache-v1", batch=batch
        )
        return FileResponse(target, media_type="application/zip", filename=target.name)

    @app.post("/api/portable/import")
    async def portable_import(request: Request):
        role(request, True)
        from .portable import import_result

        return import_result(store, await request.json())

    @app.post("/api/study/method-freeze")
    async def method_freeze(request: Request):
        role(request, True)
        if not study or simulation:
            raise ValueError("Actual prospective study required")
        data = await request.json()
        main_ids = set(bundle["selection"]["main"])
        if any(e["task"] in main_ids for e in store.events(kind="lock")):
            raise ValueError("Main judgments already exposed; no retrospective method freeze")
        if (
            data.get("schema") != "bavaria-study-method-outputs-v1"
            or data.get("selection_sha256") != digest(bundle["selection"])
            or data.get("snapshot_id") != bundle["snapshot_id"]
        ):
            raise ValueError("Method output study binding mismatch")
        from .bavaria_study.evaluate import validate_outputs

        h = validate_outputs(data, bundle)
        target = workspace / ("method-outputs-" + h + ".json")
        if target.exists():
            raise ValueError("Method outputs already registered")
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        if shutil.disk_usage(workspace).free < RESERVE + len(raw.encode()) + 32 * 1024**2:
            raise ValueError("Storage reserve")
        with target.open("x") as f:
            f.write(raw)
        target.chmod(0o400)
        with store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if any(
                r[0] in main_ids for r in c.execute("SELECT task FROM events WHERE kind='lock'")
            ):
                raise ValueError("Concurrent main judgment: retrospective freeze refused")
            store.event(
                c,
                "C",
                "",
                "methods",
                "method_outputs_freeze",
                {"outputs_sha256": h, "selection_sha256": digest(bundle["selection"])},
            )
        return {"outputs_sha256": h, "status": "FROZEN_BEFORE_MAIN_JUDGMENTS"}

    @app.get("/api/evaluation")
    def evaluation(request: Request):
        role(request, True)
        versions = sorted(workspace.glob("review-version-*.json"), key=lambda p: p.stat().st_mtime)
        if not versions:
            return {
                "status": "HUMAN_GOLD_PENDING",
                "metrics": None,
                "reason": "Keine geprüfte menschliche Reviewversion; keine Methodenauswertung geöffnet.",
            }
        if study:
            from .bavaria_study.analysis import frozen_summary
            from .bavaria_study.evaluate import evaluate as study_evaluate

            study_summary = frozen_summary(versions[-1], bundle)
            frozen = json.loads(versions[-1].read_text())
            registered = {
                e["payload"]["outputs_sha256"]
                for e in frozen.get("events", [])
                if e["kind"] == "method_outputs_freeze"
            }
            outputs = [
                p
                for p in sorted(workspace.glob("method-outputs-*.json"))
                if p.stem.removeprefix("method-outputs-") in registered
            ]
            study_summary["method_evaluations"] = [
                study_evaluate(versions[-1], p, bundle) for p in outputs
            ]
            return study_summary
        from research.bavaria.completion.metrics import evaluate

        from .prepare import load, sha

        version = load(versions[-1])
        destination = workspace / ("evaluation-" + versions[-1].stem)
        if (destination / "metrics.json").exists():
            return load(destination / "metrics.json")
        if simulation:
            raise ValueError("Techniksimulationen werden nicht wissenschaftlich ausgewertet")
        destination.mkdir(exist_ok=True)
        questions = load(PACKET / "questions.json")
        ids = {x["task_id"] for x in questions}
        for name in ["questions.json", "source_manifest.json"]:
            shutil.copy2(PACKET / name, destination / name)
        # Reuse exact local originals; no upload and no byte copy.
        if not (destination / "originals").exists():
            (destination / "originals").symlink_to(PACKET / "originals", target_is_directory=True)
        binding = {
            "files_sha256": {
                name: sha(destination / name) for name in ["questions.json", "source_manifest.json"]
            }
        }
        (destination / "PACKET.json").write_text(json.dumps(binding))
        for reviewer in ["A", "B"]:
            (destination / reviewer).mkdir(exist_ok=True)
            rows = [x for x in version["forms"][reviewer] if x["task_id"] in ids]
            (destination / reviewer / "annotations.jsonl").write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)
            )
        judgments = [
            {**x, "dependency_clearance": True}
            for x in version["adjudication"]
            if x["task_id"] in ids
        ]
        (destination / "adjudication.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in judgments)
        )
        evaluate(
            destination,
            ROOT / "generated/bavaria/completion/relations02/evidence.sqlite",
            ROOT / "generated/bavaria/completion/retrieval03/results.jsonl",
            destination / "metrics.json",
        )
        from research.bavaria.continuation.ie_compare import summary

        refs = {
            x["task_id"].removeprefix("ie:"): x["final_reference"]["fields"]
            for x in version["adjudication"]
            if x["task_id"].startswith("ie:") and x.get("final_reference")
        }
        ie = {}
        for modelrun in ["stronger-ie01", "instruct-ie01"]:
            rows = [
                json.loads(line)
                for line in (
                    ROOT / "research/bavaria/completion/release" / modelrun / "results.jsonl"
                )
                .read_text()
                .splitlines()
            ]
            groups: dict[str, list] = {}
            for row in rows:
                if row["case_id"] not in refs:
                    continue
                f = refs[row["case_id"]]
                row = {
                    **row,
                    "reference": {
                        "actor": f.get("actor"),
                        "role": f.get("actor_role"),
                        "status": f.get("status"),
                        "reference_time": f.get("valid_time"),
                        "amount": f.get("amount"),
                        "unit": f.get("unit"),
                        "amount_kind": f.get("amount_kind"),
                    },
                }
                groups.setdefault(row["method"], []).append(row)
            ie[modelrun] = {m: summary(v) for m, v in groups.items()}
        result = load(destination / "metrics.json")
        result["ie_on_declared_adjudicated_fields"] = ie
        from research.bavaria.completion.metrics import claim_metrics

        claim_report = {}
        for judgment in version["adjudication"]:
            ref = judgment.get("final_reference")
            if not ref:
                continue
            for aid, answer in ref.get("candidate_answer_claims", {}).items():
                claims = []
                for claim in answer["claims"]:
                    claims.append(
                        {
                            "assertion_or_question": claim["type"],
                            "supported": True
                            if claim["support"] == "supported"
                            else False
                            if claim["support"] in ["contradicted", "not_proven"]
                            else None,
                            "citation_attribution_correct": True
                            if claim["citation"] == "supported"
                            else False
                            if claim["citation"] in ["contradicted", "not_proven"]
                            else None,
                        }
                    )
                claim_report[aid] = {
                    **claim_metrics(claims),
                    "complete": answer["complete"],
                    "abstention_appropriate": answer["abstention"],
                    "task_id": judgment["task_id"],
                    "component_ids": ref["component_ids"],
                }
        result["atomic_claim_and_answer_metrics"] = claim_report
        result["interpretation"] = (
            "Source-exposed IE/graph tasks remain developmental; old 200 confirmation runs were not generated. Human labels do not erase prior task exposure."
        )
        (destination / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return load(destination / "metrics.json")

    @app.post("/api/freeze")
    async def freeze(request: Request):
        role(request, True)
        p = await request.json()
        store.audit()
        if simulation or store.meta("phase") != "main" or store.meta("mode") != "three":
            raise ValueError(
                "Keine unabhängige Goldfreigabe im Technik-, Kalibrierungs- oder Zwei-Personen-Modus"
            )
        if (
            not p.get("owner_verified_identities")
            or not p.get("dependency_clearance")
            or not p.get("rights_confirmed")
        ):
            raise ValueError("Owner-Gates fehlen")
        profiles = [store.profile(r) for r in ["A", "B", "C"]]
        if any(
            not x
            or not x.get("human")
            or ((i < 2 or not study) and (not x.get("independent") or x.get("developer")))
            for i, x in enumerate(profiles)
        ):
            raise ValueError("Unabhängige tatsächliche Fachreviewer erforderlich")
        if len({x["name"].strip().casefold() for x in profiles}) != 3:
            raise ValueError("Drei unterschiedliche Identitäten erforderlich")
        if not all(store.locked(r) for r in ["A", "B"]):
            raise ValueError("Reviews unvollständig")
        if any(
            not x["resolution"] or x["resolution"]["payload"]["choice"] == "unresolved"
            for x in store.conflicts()
        ):
            raise ValueError("Offene/unaufgelöste Konflikte")
        # Freeze a review version, never silently relabel quarantined benchmark data.
        data = store.export()
        if any(
            not row.get("source_scope_review_complete")
            for rows in data["forms"].values()
            for row in rows
            if "source" in required_stages(tasks[row["task_id"]])
        ):
            raise ValueError("Vollständige Quelleninventare noch nicht bestätigt")
        data["release"] = "OWNER_ATTESTED_HUMAN_REVIEW_VERSION_NOT_AUTOMATIC_RESEARCH_GOLD"
        data["owner_gates"] = p
        data["adjudicator_role_disclosure"] = {
            "developer": profiles[2].get("developer"),
            "independent": profiles[2].get("independent"),
            "not_a_third_independent_review": bool(study),
        }
        data.pop("content_sha256", None)
        data["content_sha256"] = digest(data)
        version = data["content_sha256"]
        dest = workspace / ("review-version-" + version + ".json")
        if dest.exists():
            raise ValueError("Version existiert bereits")
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        if shutil.disk_usage(workspace).free < RESERVE + len(serialized.encode()) + 32 * 1024**2:
            raise ValueError(
                "Speicherreserve verhindert neuen Snapshot; bisherige Urteile bleiben erhalten"
            )
        with dest.open("x") as handle:
            handle.write(serialized)
        dest.chmod(0o600)
        return {"version": version, "status": data["release"]}

    return app
