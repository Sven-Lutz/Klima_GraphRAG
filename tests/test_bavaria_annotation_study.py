"""Only synthetic software fixtures; never actual human research judgments."""

import json

import pytest

from research.gold_studio.bavaria_study.analysis import (
    dependency_graph,
    paired_cluster_interval,
    workload,
)
from research.gold_studio.bavaria_study.inventory import population_class, select
from research.gold_studio.store import Store, prerequisites, required_stages


def test_focus_locator_belongs_to_original_not_allowed_source_order():
    from research.gold_studio.prepare import focus_source

    task = {
        "allowed_original_sha256": ["html", "pdf"],
        "focus": {"text": "SYNTHETIC PDF PAGE 59", "page_ref": "59"},
        "pool": [{"source_sha256": "pdf", "text": "SYNTHETIC PDF PAGE 59"}],
    }
    assert focus_source(task) == "pdf"
    task["pool"].append({"source_sha256": "html", "text": "SYNTHETIC PDF PAGE 59"})
    assert focus_source(task) is None  # Ambiguity is never guessed.


def test_technical_unavailability_is_missing_not_negative(tmp_path):
    store = Store(tmp_path / "annotations.sqlite", bundle(), True)
    task = store.tasks()[0]
    payload = {"technically_unreviewable": True, "comment": "SYNTHETIC unavailable PDF"}
    store.save("A", task["task_id"], "source", payload)
    assert store.latest("A", task["task_id"], "source") is None
    assert store.report()["technically_unreviewable_drafts"]["A"] == [
        {"task": "0", "stage": "source"}
    ]
    with pytest.raises(ValueError, match="Technisch"):
        store.validate("source", payload, task)


def test_historical_uncompressed_source_reuses_verified_bytes(tmp_path, monkeypatch):
    import hashlib

    from research.gold_studio.bavaria_study import bundle as adapter

    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    root = tmp_path / "research/experiments/e2_v11_acquisition_20261024/export/raw"
    root.mkdir(parents=True)
    original = root / "technical-original"
    original.write_bytes(b"SYNTHETIC ORIGINAL TEST")
    record = {
        "raw_path": str(original),
        "raw_bytes": original.stat().st_size,
        "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "file": "test.html",
    }
    assert adapter.source_path(record) == original
    assert not (tmp_path / "generated/gold-studio-original-cache").exists()
    original.write_bytes(b"CHANGED")
    with pytest.raises(ValueError, match="mismatch"):
        adapter.source_path(record)


def bundle():
    tasks = [
        {
            "task_id": str(i),
            "question": "SYNTHETIC TEST ONLY",
            "required_stages": [stage],
            "allowed_original_sha256": ["a" * 64],
            "pool": [],
            "components": [str(i)],
            "cohort": "technical",
            "effective_split": "DEVELOPMENT_EXPOSED",
        }
        for i, stage in enumerate(["source", "fields", "retrieval"])
    ]
    return {
        "selection": {"calibration": [t["task_id"] for t in tasks], "main": []},
        "tasks": tasks,
        "sources": [],
        "answers": [],
    }


def payload(stage, authority="unclear"):
    p = {
        "original_inspected": True,
        "source_sha256": "a" * 64,
        "comment": "SYNTHETIC TECHNICAL TEST",
    }
    if stage == "source":
        p.update(
            authority=authority, scope="unclear", policy="unclear", answerability="UNANSWERABLE"
        )
    if stage == "fields":
        p.update(temporal="UNKNOWN", judgment="not_proven")
    if stage == "retrieval":
        p.update(relevance={}, complete="unclear")
    return p


def test_adaptive_independence_conflicts_and_missing(tmp_path):
    b = bundle()
    s = Store(tmp_path / "a.sqlite", b, True)
    assert not s.locked("A")
    for role in ["A", "B"]:
        with s.conn() as c:
            s.event(
                c,
                role,
                "",
                "profile",
                "profile",
                {"name": "TECH-" + role, "human": False, "independent": False},
            )
        # A field-first case does not require unrelated source cases to be completed.
        for task in reversed(b["tasks"]):
            stage = required_stages(task)[0]
            s.save(
                role,
                task["task_id"],
                stage,
                payload(stage, "official" if role == "A" else "unclear"),
                True,
            )
    assert s.locked("A") and s.locked("B")
    assert len(s.conflicts()) == 1
    assert s.report()["metrics"]["fields.judgment"]["missing_pairs"] == 0
    assert s.report()["metrics"]["source.authority"]["missing_pairs"] == 0
    s.adjudicate("0", "source", {"choice": "A", "rationale": "SYNTHETIC TEST"})
    assert s.conflicts()[0]["resolution"]
    with pytest.raises(ValueError):
        s.save("A", "1", "source", payload("source"), True)
    with pytest.raises(ValueError):
        s.save("A", "1", "fields", payload("fields"), True)
    assert workload(s)["status"] == "PENDING_REAL_HUMAN_CALIBRATION"
    assert s.audit()


def test_legacy_rounds_unchanged():
    t = {"task_id": "old"}
    assert len(required_stages(t)) == 4
    assert prerequisites([t], t, "fields") == [("old", "source")]
    for stages in [[], ["unknown"], ["source", "source"], "fields"]:
        with pytest.raises(ValueError):
            required_stages({"required_stages": stages})


def test_sampling_source_uniqueness_and_determinism():
    frame = {str(i): {"region": str(i % 7)} for i in range(450)}
    rows = [
        {
            "sha256": str(i),
            "ags": str(i),
            "region": str(i % 7),
            "status": "CANDIDATE",
            "historical_exposure": i < 10,
            "families": ["source", "fields", "relation", "retrieval"],
        }
        for i in range(450)
    ]
    result = select(rows, frame)
    assert len(result) == 400 and len({r["ags"] for r in result}) == 400
    assert len({r["sha256"] for r in result}) == 400
    assert not any(r["historical_exposure"] for r in result)
    assert result == select(list(reversed(rows)), frame)
    assert population_class(None) == "UNKNOWN"


def test_dependence_and_paired_clusters():
    cases = [
        {
            "task_id": str(i),
            "components": ["same" if i < 2 else "other"],
            "source_record": {"host_proxy": "one"},
        }
        for i in range(3)
    ]
    assert dependency_graph(cases)["component_count"] == 2
    assert dependency_graph(cases, True)["component_count"] == 1
    result = paired_cluster_interval(
        [
            {"a": 1, "b": 0, "component": "x"},
            {"a": 1, "b": 0, "component": "y"},
            {"a": None, "b": 0, "component": "z"},
        ]
    )
    assert result["interval"] == [1.0, 1.0] and result["missing_pairs"] == 1
    assert paired_cluster_interval([{"a": 1, "b": 0, "component": "x"}])["interval"] is None


def test_focused_export_retains_own_evidence(tmp_path):
    b = bundle()
    s = Store(tmp_path / "e.sqlite", b, True)
    for role in ["A", "B"]:
        with s.conn() as c:
            s.event(
                c,
                role,
                "",
                "profile",
                "profile",
                {"name": "TECH-" + role, "human": False, "independent": False},
            )
        for t in b["tasks"]:
            stage = required_stages(t)[0]
            p = payload(stage)
            if stage == "fields":
                p["quote"] = "SYNTHETIC TEST TEXT"
                p["actor"] = "TEST ACTOR"
            s.save(role, t["task_id"], stage, p, True)
    exported = s.export()
    row = next(r for r in exported["forms"]["A"] if r["task_id"] == "1")
    assert row["evidence_spans"][0]["verbatim_quote"] == "SYNTHETIC TEST TEXT"
    assert row["originals_inspected"] == ["a" * 64]
    assert row["required_stages"] == ["fields"]
    assert exported["report"]["gold_status"] == "NOT_RELEASED"


def test_strict_scoring_and_output_identity():
    from research.gold_studio.bavaria_study.evaluate import cells, counts, scores, validate_outputs
    from research.gold_studio.store import digest

    b = bundle()
    b["selection"]["main"] = ["0"]
    b["snapshot_id"] = "TECHNICAL-SNAPSHOT"
    outputs = [{"task_id": "0", "fields": {"actor": "TEST ACTOR"}}]
    data = {
        "schema": "bavaria-study-method-outputs-v1",
        "selection_sha256": digest(b["selection"]),
        "snapshot_id": b["snapshot_id"],
        "methods": {
            "TECHNICAL": {
                "config": {"version": "synthetic"},
                "outputs": outputs,
                "output_sha256": digest(outputs),
            }
        },
    }
    assert validate_outputs(data, b) == digest(data)
    assert cells({"actor": " UNKNOWN "}) == set()
    assert cells({"unit": "MW"}) != cells({"unit": "mW"})
    assert scores(*counts({("actor", "x")}, {("actor", "x"), ("unit", "eur")}))["precision"] == 0.5
    data["methods"]["TECHNICAL"]["outputs"].append(outputs[0])
    with pytest.raises(ValueError):
        validate_outputs(data, b)


def test_adaptive_portable_missing_and_stage_binding(tmp_path):
    import hashlib

    from research.gold_studio.portable import ENVELOPE, SCHEMA, import_result
    from research.gold_studio.prepare import PACKET
    from research.gold_studio.store import canonical, digest

    sources = json.loads((PACKET / "source_manifest.json").read_text())
    source = next(s for s in sources if s["file"].endswith(".html"))
    task = {
        "task_id": "TECH_FIELDS",
        "question": "SYNTHETIC TEST",
        "required_stages": ["fields"],
        "allowed_original_sha256": [source["sha256"]],
        "pool": [],
    }
    b = {
        "selection": {"calibration": [task["task_id"]], "main": []},
        "tasks": [task],
        "sources": [source],
        "answers": [],
    }
    s = Store(tmp_path / "transport.sqlite", b, True)
    binding = {
        "schema": SCHEMA,
        "package_id": "TECH",
        "reviewer": "A",
        "assignment_token": "TECH_ONLY",
        "task_schema": "gold-studio-forms-v1",
        "phase": "calibration",
        "task_ids": [task["task_id"]],
        "selection_sha256": digest(b["selection"]),
        "sources": [source["sha256"]],
        "simulation": True,
    }
    with s.conn() as c:
        s.event(c, "C", "", "portable", "portable_issue", {"binding": binding})
    p = payload("fields")
    p["source_sha256"] = source["sha256"]
    data = {
        "binding": binding,
        "profile": {
            "name": "TECH-A",
            "qualification": "SOFTWARE TEST",
            "human": False,
            "independent": False,
            "developer": True,
        },
        "events": [
            {
                "id": "technical-event",
                "at": "2026-11-10T12:00:00Z",
                "task": task["task_id"],
                "stage": "fields",
                "kind": "lock",
                "payload": p,
            }
        ],
    }
    raw = canonical(data)
    e = {"schema": ENVELOPE, "content": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    assert import_result(s, e)["missing"] == 0
    assert s.locked("A")
    assert import_result(s, e)["status"] == "ALREADY_IMPORTED"
    data["events"][0]["stage"] = "source"
    raw = canonical(data)
    e = {"schema": ENVELOPE, "content": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    with pytest.raises(ValueError):
        import_result(s, e)


def test_main_package_refuses_unfrozen_batch(tmp_path):
    from research.gold_studio.portable import export_package

    b = bundle()
    s = Store(tmp_path / "g.sqlite", b, True)
    with s.conn() as c:
        c.execute("UPDATE metadata SET value='main' WHERE key='phase'")
    with pytest.raises(ValueError, match="Main export requires"):
        export_package(s, "A", tmp_path / "forbidden.zip", tmp_path / "cache", batch="B01")
    assert not (tmp_path / "forbidden.zip").exists()


def test_bounded_focal_pdf_preview_keeps_full_original_contract():
    from research.gold_studio.portable import preview_pages

    t = {
        "task_id": "TECH",
        "allowed_original_sha256": ["h"],
        "focal_source_sha256": "h",
        "focus": {"page_ref": "133"},
    }
    assert preview_pages({"tasks": [t]}, ["TECH"], "h", 400) == [1, 132, 133, 134]
    t["focus"]["page_ref"] = "401"
    with pytest.raises(ValueError):
        preview_pages({"tasks": [t]}, ["TECH"], "h", 400)
    t.pop("focal_source_sha256")
    assert preview_pages({"tasks": [t]}, ["TECH"], "h", 3) == [1, 2, 3]


def test_explicit_source_quality_requires_own_decisions(tmp_path):
    b = bundle()
    t = b["tasks"][0]
    t["quality_review"] = True
    s = Store(tmp_path / "quality.sqlite", b, True)
    p = payload("source")
    with pytest.raises(ValueError, match="Originaltreue"):
        s.validate("source", p, t)
    p.update(fidelity="unclear", document_type="unclear", temporal_basis="unclear")
    s.validate("source", p, t)


def test_starter_opens_only_after_http_ready(monkeypatch):
    import http.client

    from research.gold_studio import __main__ as starter

    statuses = iter([503, 200])
    opened = []
    closed = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            pass

        def getresponse(self):
            class Response:
                status = next(statuses)

            return Response()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(starter.time, "sleep", lambda _: None)
    monkeypatch.setattr(starter.webbrowser, "open", opened.append)
    starter.open_when_ready(8765)
    assert opened == ["http://127.0.0.1:8765"] and len(closed) == 2


def test_sampling_removes_same_purpose_identical_focus_only():
    frame = {"a": {"region": "R"}, "b": {"region": "R"}, "c": {"region": "R"}}
    rows = [
        {
            "sha256": str(i),
            "ags": a,
            "region": "R",
            "status": "CANDIDATE",
            "historical_exposure": False,
            "families": ["source"],
            "focus": {"text": " SAME  TEXT " if i < 2 else "different"},
        }
        for i, a in enumerate(frame)
    ]
    assert len(select(rows, frame)) == 3
    strict = select(rows, frame, deduplicate_focus=True)
    assert len(strict) == 2
    assert strict == select(list(reversed(rows)), frame, deduplicate_focus=True)


def test_adjudicated_stage_evidence_replaces_previous_quote():
    from research.gold_studio.store import evidence_from_forms

    forms = {
        "source": {"source_sha256": "a", "quote": "SOURCE CONTEXT"},
        "fields": {"source_sha256": "a", "quote": "CORRECTED TECHNICAL EXAMPLE", "page": 2},
    }
    result = evidence_from_forms(forms, "TECH_ONLY")
    assert [r["verbatim_quote"] for r in result] == [
        "SOURCE CONTEXT",
        "CORRECTED TECHNICAL EXAMPLE",
    ]
    assert result[1]["physical_page"] == 2
    assert evidence_from_forms({"fields": None}, "TECH_ONLY") == []
