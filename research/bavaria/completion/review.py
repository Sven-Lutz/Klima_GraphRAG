# ruff: noqa: E501
"""Source-only double-review packet and fail-closed independent-label intake.

No command creates Human Research Gold or certifies reviewer identities.
"""

import argparse
import gzip
import hashlib
import json
import os
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

from research.offline_evaluation.evaluate import ANSWERABILITY

RESERVE = 15 * 1024**3
LIMIT = 256 * 1024**2


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(
    output,
    dataset="generated/bavaria/completion/relations01",
    selection="generated/bavaria/completion/selection-v2.json",
    reuse_packet=None,
):
    root = Path(output)
    if root.exists():
        raise ValueError("Review packets are immutable; use a fresh directory")
    if shutil.disk_usage(root.parent).free < RESERVE + LIMIT:
        raise RuntimeError("Review storage reserve")
    old = [
        json.loads(line)
        for line in Path("research/bavaria/release/reviewer-packet/A/tasks.jsonl")
        .read_text()
        .splitlines()
    ]
    new = [json.loads(line) for line in (Path(dataset) / "tasks.jsonl").read_text().splitlines()]
    selected = json.loads(Path(selection).read_text())["selection"]
    sources = {}
    for x in old:
        p = Path(x["original_path"])
        sources[p.stem] = {"path": str(p), "url": x["original_url"], "frame_ags": [x["ags"]]}
    for x in selected:
        sources[x["sha256"]] = {"path": x["raw_path"], "url": x["url"], "frame_ags": [x["ags"]]}
    tasks = [
        {
            "task_id": x["id"],
            "question": x["question"],
            "allowed_original_sha256": [Path(x["original_path"]).stem],
            "reference_status": "UNANNOTATED_HUMAN_GOLD_PENDING",
            "cohort": "existing_200",
        }
        for x in old
    ]
    tasks += [
        {
            "task_id": "completion:" + x["task_id"],
            "question": x["question"],
            "allowed_original_sha256": [s["sha256"] for s in selected],
            "reference_status": "UNANNOTATED_HUMAN_GOLD_PENDING",
            "cohort": "new_relations",
        }
        for x in new
    ]
    root.mkdir(parents=True)
    (root / "originals").mkdir()
    originals = []
    total = 0
    for sha, record in sorted(sources.items()):
        p = Path(record["path"])
        body = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError("Original SHA mismatch")
        total += len(body)
        if total > LIMIT or shutil.disk_usage(root).free < RESERVE + 32 * 1024**2:
            raise RuntimeError("Packet size/reserve gate; partial packet not complete")
        suffix = ".pdf" if body.startswith(b"%PDF") else ".html"
        relative = "originals/" + sha + suffix
        reusable = Path(reuse_packet) / relative if reuse_packet else None
        if reusable and reusable.exists():
            if digest(reusable) != sha:
                raise ValueError("Reusable original integrity mismatch")
            os.link(reusable, root / relative)
        else:
            (root / relative).write_bytes(body)
        originals.append(
            {
                "sha256": sha,
                "file": relative,
                "url": record["url"],
                "frame_ags": record["frame_ags"],
                "claim_scope": "REVIEW_REQUIRED_DO_NOT_INHERIT_FRAME",
            }
        )
    write(root / "source_manifest.json", originals)
    write(
        root / "RUBRIC.json",
        {
            "answerability": sorted(ANSWERABILITY),
            "evidence_spans": {
                "source_sha256": "required",
                "physical_page": "1-based PDF page or null",
                "verbatim_quote": "exact original text, whitespace normalization only",
                "relevance": "0/1/2",
                "required_bundle": "same ID for acceptable alternatives",
            },
            "temporal_distinctions": [
                "PLAN",
                "ADOPTION",
                "REPORTED_IMPLEMENTATION",
                "REPORTED_INVENTORY_ESTIMATE",
                "MEASUREMENT",
                "UNKNOWN",
            ],
            "authority": "Verify evidence of publisher/mandate separately from hosting municipality.",
            "source_then_answer_order": "Lock source references before opening method-blind answer candidates.",
            "gold_release": "Manual owner verification only; never auto-promoted by software.",
        },
    )
    write(root / "questions.json", tasks)
    for reviewer, seed in [("A", 42), ("B", 71)]:
        folder = root / reviewer
        folder.mkdir()
        ordered = list(tasks)
        random.Random(seed).shuffle(ordered)
        form = []
        for t in ordered:
            form.append(
                {
                    "task_id": t["task_id"],
                    "reviewer_id": "",
                    "reviewer_qualification": "",
                    "independent_human_attestation": False,
                    "originals_inspected": [],
                    "source_authority": None,
                    "claim_scope_ags": [],
                    "document_type": None,
                    "policy_relevance": None,
                    "project_identity_notes": "",
                    "entities": [],
                    "relations": [],
                    "fields": {},
                    "temporal_status": None,
                    "answerability": None,
                    "evidence_spans": [],
                    "source_scope_review_complete": False,
                    "candidate_answer_claims": [],
                    "answer_completeness": None,
                    "citation_correctness": None,
                    "minutes": None,
                    "rationale": "",
                }
            )
        (folder / "annotations.jsonl").write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in form)
        )
    adjudication = [
        {
            "task_id": t["task_id"],
            "adjudicator_id": "",
            "reviewer_A_record_sha256": "",
            "reviewer_B_record_sha256": "",
            "resolution": None,
            "final_reference": None,
            "dependency_clearance": False,
            "rationale": "",
        }
        for t in tasks
    ]
    (root / "adjudication.jsonl").write_text("".join(json.dumps(x) + "\n" for x in adjudication))
    (
        root / "README.md"
    ).write_text("""# Independent original-first review — local source-only packet

No human has been recruited or assigned. The coordinator gives reviewers only
this packet, never neighbouring provisional labels or method outputs. A and B
work independently in their respective annotations.jsonl files. An adjudicator
works only after both forms are locked. Do not inspect the other reviewer’s form.

1. Read questions.json and open the exact original files listed in source_manifest.json.
   The coordinator must confirm authorized access/sharing before external delivery.
   These are verified, regenerable local decompressions, not a new authoritative archive.
   HTML is untrusted: inspect offline/source view with active content disabled;
   do not follow external links as though they were the frozen original.
2. Record real identity, qualifications, independence attestation and minutes.
3. Read complete originals, including headings, tables, legends, footnotes and dates.
   Municipality frame is not claim jurisdiction. Hof’s status table needs visual
   colour inspection; text alone does not encode statuses. Velden hosts a Vorra report.
4. Record source authority, true scope, entity/project identity and temporal modality.
   Planned targets and forecasts are not implementation or measured effects.
5. evidence_spans entries: source_sha256, physical_page (PDF, 1-based), verbatim_quote,
   relevance (0/1/2), required_bundle and rationale. Record alternative acceptable
   spans, ambiguity and representation loss. Do not invent opaque pipeline unit IDs.
6. fields/relations/entities contain independently read values, explicit UNKNOWNs,
   evidence-span references and uncertainty. Answerability is bounded to the
   task’s allowed originals. Set source_scope_review_complete only after full review.
7. candidate_answer_claims, when assigned in a later method-blind answer round,
   use neutral answer/claim IDs, supported true/false/unknown, source references,
   role/time/scope/numerical correctness, completeness and citation attribution.
   Source references must be locked before seeing method outputs.
8. Lock A and B separately. Adjudicator records both record hashes, disagreements,
   final reference, unresolved exclusions and dependency clearance. Never infer
   a Human Research Gold release from filled forms alone. Owner approval and
   actual independence verification remain separate manual requirements.

The existing 200 tasks retain the 70/130 planning split and confirmation quarantine;
no method outputs for those tasks are generated by this package. Project, institution,
source-version and paraphrase dependencies need adjudicated clearance before any
confirmatory analysis. New relation tasks remain exploratory. Time the first 20
annotations before committing reviewer workload. No agreement/power score exists
until actual independent forms have been completed.
""")
    manifest = {
        "schema": "bavaria-independent-review-packet-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tasks": len(tasks),
        "originals": len(originals),
        "original_bytes": total,
        "human_judgments": 0,
        "scientific_release": "NOT_AUTHORIZED",
        "source_scope": "Existing 200 bounded original tasks plus new 28-source relation cohort",
        "rights": "LOCAL_OWNER_REVIEW_ONLY_EXTERNAL_SHARING_OWNER_DECISION",
        "restoration": "Regenerate from owner-held gzip originals with this command; never delete authoritative archives.",
        "files_sha256": {
            str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()
        },
    }
    write(root / "PACKET.json", manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "files_sha256"}, indent=2))


def intake(packet):
    root = Path(packet)
    frozen = json.loads((root / "PACKET.json").read_text())
    for name in ["questions.json", "source_manifest.json"]:
        if digest(root / name) != frozen["files_sha256"][name]:
            raise ValueError("Immutable review task/source manifest changed")
    tasks = {t["task_id"]: t for t in json.loads((root / "questions.json").read_text())}
    result = {}
    identities = {}
    for reviewer in ["A", "B"]:
        rows = [
            json.loads(line)
            for line in (root / reviewer / "annotations.jsonl").read_text().splitlines()
        ]
        if len({r["task_id"] for r in rows}) != len(rows):
            raise ValueError("Duplicate annotation task")
        if set(r["task_id"] for r in rows) != set(tasks):
            raise ValueError("Incomplete/foreign task universe")
        completed = []
        for r in rows:
            if not r["reviewer_id"]:
                continue
            if r["independent_human_attestation"] is not True or not r["reviewer_qualification"]:
                raise ValueError("Actual independent human attestation/qualification required")
            if not r["originals_inspected"] or r["answerability"] not in ANSWERABILITY:
                raise ValueError("Original inspection and bounded answerability required")
            if not set(r["originals_inspected"]) <= set(
                tasks[r["task_id"]]["allowed_original_sha256"]
            ):
                raise ValueError("Reference points outside frozen allowed sources")
            completed.append(r)
        identities[reviewer] = {r["reviewer_id"] for r in completed}
        result[reviewer] = {"completed": len(completed), "expected": len(tasks)}
    if identities["A"] & identities["B"]:
        raise ValueError("Reviewers A and B must be distinct")
    return {
        "status": "INDEPENDENT_REVIEW_PENDING"
        if not any(r["completed"] for r in result.values())
        else "HUMAN_DECLARATIONS_IMPORTED_OWNER_VERIFICATION_PENDING",
        "forms": result,
        "metrics": None,
        "reason": "No accepted adjudicated independent references. This command never promotes Research Gold.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "intake"])
    p.add_argument("--packet", required=True)
    p.add_argument("--dataset", default="generated/bavaria/completion/relations01")
    p.add_argument("--selection", default="generated/bavaria/completion/selection-v2.json")
    p.add_argument("--reuse-packet")
    a = p.parse_args()
    if a.command == "prepare":
        prepare(a.packet, a.dataset, a.selection, a.reuse_packet)
    else:
        print(json.dumps(intake(a.packet), indent=2))
