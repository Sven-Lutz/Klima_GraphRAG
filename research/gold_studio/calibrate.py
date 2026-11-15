# ruff: noqa: E501
"""Synthetic HTTP calibration of the 12-task protocol; never human annotation."""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .prepare import prepare
from .store import STAGES


def run(workspace, endpoint, leave_one=False):
    import sqlite3

    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ["127.0.0.1", "localhost"]
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ["", "/"]
    ):
        raise ValueError("Technical calibration accepts only a local HTTP endpoint")
    workspace = Path(workspace)
    with sqlite3.connect(workspace / "annotations.sqlite") as c:
        config = json.loads(
            c.execute("SELECT value FROM metadata WHERE key='config'").fetchone()[0]
        )
        if config.get("simulation") is not True:
            raise ValueError("Never apply synthetic calibration to a human review workspace")
        keys = json.loads(c.execute("SELECT value FROM metadata WHERE key='keys'").fetchone()[0])
    bundle = prepare()
    tasks = {t["task_id"]: t for t in bundle["tasks"]}
    durations = []
    with httpx.Client(base_url=endpoint, timeout=60) as client:

        def post(url, p):
            start = time.perf_counter()
            r = client.post(url, json=p)
            durations.append(time.perf_counter() - start)
            r.raise_for_status()
            return r.json()

        assert client.get("/api/state").status_code == 403
        for role in ["A", "B"]:
            post("/api/login", {"role": role, "code": keys[role]})
            if not client.get("/api/state").json()["profile"]:
                post(
                    "/api/profile",
                    {
                        "name": "TECHNICAL_SIMULATION_" + role,
                        "qualification": "Software protocol test, not expert annotation",
                        "human": True,
                        "developer": True,
                        "independent": False,
                    },
                )
            state = client.get("/api/state").json()
            assert state["simulation"]
            assert client.get("/api/conflicts").status_code == 403
            for stage in STAGES:
                for i, item in enumerate(state["tasks"]):
                    t = tasks[item["id"]]
                    url = "/api/judgment/" + t["task_id"] + "/" + stage
                    view = client.get("/api/task/" + t["task_id"], params={"stage": stage})
                    view.raise_for_status()
                    if view.json()["locked"]:
                        continue
                    p = {
                        "original_inspected": True,
                        "comment": "SYNTHETIC_TECHNICAL_INPUT_NOT_A_HUMAN_JUDGMENT",
                        "seconds": 0,
                        "source_sha256": t["allowed_original_sha256"][0],
                    }
                    if stage == "source":
                        p.update(
                            authority="official" if role == "A" or i else "institutional",
                            scope="unclear",
                            policy="unclear",
                            answerability="UNANSWERABLE",
                        )
                    elif stage == "fields":
                        p.update(temporal="UNKNOWN", judgment="unclear")
                    elif stage == "retrieval":
                        p.update(
                            relevance={x["id"]: "unclear" for x in t["pool"]}, complete="unclear"
                        )
                    else:
                        p.update(
                            answers={
                                a["neutral_answer_id"]: {
                                    "complete": "unclear",
                                    "abstention": "unclear",
                                    "claims": [
                                        {
                                            "text": a["candidate_answer"],
                                            "type": "assertion",
                                            "support": "unclear",
                                            "citation": "unclear",
                                            "scope_time_numbers": "unclear",
                                        }
                                    ],
                                }
                                for a in bundle["answers"]
                                if a["question"] == t["question"]
                            }
                        )
                    post(url, {"form": p, "lock": False})
                    assert (
                        client.get("/api/task/" + t["task_id"], params={"stage": stage}).json()[
                            "draft"
                        ]["payload"]
                        == p
                    )
                    post(url, {"form": p, "lock": True})
                    assert client.post(url, json={"form": p, "lock": True}).status_code == 409
            post("/api/logout", {})
        post("/api/login", {"role": "C", "code": keys["C"]})
        post(
            "/api/profile",
            {
                "name": "TECHNICAL_SIMULATION_C",
                "qualification": "Technical adjudication test",
                "human": True,
                "developer": True,
                "independent": False,
            },
        )
        conflicts = client.get("/api/conflicts")
        conflicts.raise_for_status()
        conflicts = conflicts.json()
        for index, x in enumerate(conflicts):
            if not x["resolution"] and not (leave_one and index == 0):
                post(
                    "/api/adjudicate/" + x["task_id"] + "/" + x["stage"],
                    {
                        "choice": "A",
                        "rationale": "Synthetic conflict resolution, no scientific content judgment.",
                    },
                )
        report = client.get("/api/report").json()
        assert not report["profiles"]["A"]["human"]
        assert (
            client.post(
                "/api/freeze",
                json={
                    "owner_verified_identities": True,
                    "dependency_clearance": True,
                    "rights_confirmed": True,
                },
            ).status_code
            == 409
        )
        export = client.get("/api/export")
        export.raise_for_status()
        data = export.json()
        assert all(
            not row["independent_human_attestation"]
            for rows in data["forms"].values()
            for row in rows
        )
        (workspace / "technical-export.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        source = next(
            s
            for s in bundle["sources"]
            if s["file"].endswith(".pdf")
            and any(
                s["sha256"] in t["allowed_original_sha256"]
                for t in tasks.values()
                if t["task_id"] in bundle["selection"]["calibration"]
            )
        )
        image = client.get("/api/original/" + source["sha256"] + "/page/1")
        image.raise_for_status()
        assert image.content.startswith(b"\x89PNG")
        original = client.get("/api/original/" + source["sha256"] + "/pdf")
        assert original.content.startswith(b"%PDF")
        remaining = sum(not x["resolution"] for x in client.get("/api/conflicts").json())
        result = {
            "status": "HTTP_PHASE_COMPLETE_BROWSER_ADJUDICATION_PENDING"
            if remaining
            else "TECHNICAL_CALIBRATION_COMPLETE_NOT_HUMAN_REVIEW",
            "adjudication_remaining": remaining,
            "tasks": 12,
            "roles": ["A", "B", "C"],
            "locked_forms": sum(v["completed_forms"] for v in report["timing"].values()),
            "conflicts_exercised": len(conflicts),
            "original_pdf_page_rendered": True,
            "resume_draft_verified": True,
            "immutable_lock_verified": True,
            "simulation_gold_release_rejected": True,
            "http_post_count": len(durations),
            "http_post_seconds_sum": sum(durations),
            "max_http_post_seconds": max(durations),
            "actual_human_duration": None,
            "actual_human_comprehension": None,
            "actual_human_queries": None,
            "independent_human_judgments": 0,
            "report": report,
        }
        (workspace / "TECHNICAL_CALIBRATION.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps({k: v for k, v in result.items() if k != "report"}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--leave-one", action="store_true")
    a = p.parse_args()
    run(a.workspace, a.endpoint, a.leave_one)
