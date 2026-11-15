"""Synthetic technical transport tests. No human labels or productive gold database."""

import copy
import hashlib

import pytest

from research.gold_studio.portable import ENVELOPE, SCHEMA, decode, freeze_main, import_result
from research.gold_studio.prepare import prepare
from research.gold_studio.store import STAGES, Store, canonical, digest


def envelope(data):
    raw = canonical(data)
    return {"schema": ENVELOPE, "content": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}


@pytest.fixture
def setup(tmp_path):
    b = prepare()
    b = copy.deepcopy(b)
    tid = b["selection"]["calibration"][0]
    t = next(t for t in b["tasks"] if t["task_id"] == tid)
    b["selection"]["calibration"] = [tid]
    s = Store(tmp_path / "annotations.sqlite", b, True)
    binding = {
        "schema": SCHEMA,
        "package_id": "TECH_ONLY",
        "reviewer": "A",
        "assignment_token": "TECH_ONLY_NONCE",
        "task_schema": "gold-studio-forms-v1",
        "phase": "calibration",
        "task_ids": [tid],
        "selection_sha256": digest(b["selection"]),
        "sources": t["allowed_original_sha256"],
        "simulation": True,
    }
    with s.conn() as c:
        s.event(c, "C", "", "portable", "portable_issue", {"binding": binding})
    data = {
        "binding": binding,
        "profile": {
            "name": "TECH-A",
            "qualification": "technical test",
            "human": False,
            "developer": True,
            "independent": False,
        },
        "events": [],
    }
    source = {
        "source_sha256": t["allowed_original_sha256"][0],
        "original_inspected": True,
        "authority": "unclear",
        "scope": "unclear",
        "policy": "unclear",
        "answerability": "UNANSWERABLE",
        "comment": "SYNTHETIC_TECHNICAL_TEST",
    }
    return s, data, t, source


def event(t, stage, p, kind="lock", id="e1"):
    return {"id": id, "task": t["task_id"], "stage": stage, "kind": kind, "payload": p}


def test_partial_import_idempotence_and_immutable_original(setup):
    s, d, t, p = setup
    d["events"] = [event(t, "source", p)]
    r = import_result(s, envelope(d))
    assert r["missing"] == 3 and r["gold_status"] == "NOT_RELEASED"
    assert import_result(s, envelope(d))["status"] == "ALREADY_IMPORTED"
    assert len(s.events("A", "lock")) == 1
    d["events"][0]["payload"] = {**p, "authority": "official"}
    with pytest.raises(ValueError, match="overwrite"):
        import_result(s, envelope(d))
    assert s.latest("A", t["task_id"], "source")["payload"] == p
    assert s.profile("A")["human"] is False
    s.audit()


@pytest.mark.parametrize(
    "mutation",
    ["reviewer", "version", "source", "task", "duplicate", "duplicate_id", "checksum", "order"],
)
def test_foreign_and_manipulated_imports_rejected_atomically(setup, mutation):
    s, d, t, p = setup
    d["events"] = [event(t, "source", p)]
    if mutation == "reviewer":
        d["binding"]["reviewer"] = "B"
    if mutation == "version":
        d["binding"]["task_schema"] = "fake"
    if mutation == "source":
        d["events"][0]["payload"] = {**p, "source_sha256": "0" * 64}
    if mutation == "task":
        d["events"][0]["task"] = "FOREIGN"
    if mutation == "duplicate":
        d["events"].append(event(t, "source", p, id="e2"))
    if mutation == "duplicate_id":
        d["events"].append(copy.deepcopy(d["events"][0]))
    if mutation == "order":
        d["events"] = [event(t, "fields", {**p, "temporal": "UNKNOWN", "judgment": "not_proven"})]
    e = envelope(d)
    if mutation == "checksum":
        e["content"] += " "
    with pytest.raises(ValueError):
        import_result(s, e)
    assert not s.events("A", "lock") and not s.profile("A")


def test_duplicate_json_keys_and_simulation_cannot_release_main(setup):
    s, d, t, p = setup
    raw = '{"events":[],"events":[1]}'
    with pytest.raises(ValueError, match="Duplicate"):
        decode(
            {"schema": ENVELOPE, "content": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
        )
    with pytest.raises(ValueError, match="Actual"):
        freeze_main(s, {})
    assert s.report()["gold_status"] == "NOT_RELEASED"


def test_complete_reviews_and_existing_adjudication_contract(setup):
    s, d, t, p = setup
    for role in ["A", "B"]:
        data = copy.deepcopy(d)
        data["binding"]["reviewer"] = role
        data["binding"]["package_id"] = "TECH_" + role
        data["profile"]["name"] = "TECH-" + role
        with s.conn() as c:
            s.event(
                c,
                "C",
                "",
                "portable",
                "portable_issue",
                {"binding": copy.deepcopy(data["binding"])},
            )
        forms = [
            {**p, "authority": "official" if role == "A" else "institutional"},
            {**p, "temporal": "UNKNOWN", "judgment": "not_proven"},
            {**p, "relevance": {x["id"]: "unclear" for x in t["pool"]}, "complete": "unclear"},
            {**p, "answers": {}},
        ]
        # selected first task is a relation task without generated answer variants
        data["events"] = [
            event(t, stage, form, id=str(i)) for i, (stage, form) in enumerate(zip(STAGES, forms))
        ]
        import_result(s, envelope(data))
    conflicts = s.conflicts()
    assert len(conflicts) == 1
    before = [s.latest(r, t["task_id"], "source")["hash"] for r in ["A", "B"]]
    cdata = copy.deepcopy(d)
    cdata["binding"]["reviewer"] = "C"
    cdata["binding"]["package_id"] = "TECH_C"
    cdata["profile"]["name"] = "TECH-C"
    with s.conn() as c:
        s.event(c, "C", "", "portable", "portable_issue", {"binding": cdata["binding"]})
    cdata["events"] = [
        event(
            t, "source", {"choice": "unresolved", "rationale": "TECHNICAL TEST"}, kind="adjudicate"
        )
    ]
    assert import_result(s, envelope(cdata))["missing"] == 0
    assert before == [s.latest(r, t["task_id"], "source")["hash"] for r in ["A", "B"]]
    assert s.report()["gold_status"] == "NOT_RELEASED"
    s.audit()


def test_nested_relation_evidence_cannot_reference_foreign_original(setup):
    s, d, t, p = setup
    p = {
        **p,
        "relations": [
            {
                "subject": "TECH",
                "predicate": "FUNDS",
                "object": "TECH",
                "evidence_spans": [{"source_sha256": "0" * 64}],
            }
        ],
    }
    d["events"] = [event(t, "source", p)]
    with pytest.raises(ValueError, match="Foreign source"):
        import_result(s, envelope(d))
    assert not s.events("A", "lock")
