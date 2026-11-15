"""Automatic consolidated review feedback; never changes canonical research results.

No holdout export, no synthetic-to-human promotion, no claim-truth-to-relevance mapping.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from research.bavaria.workspace.core import write_guard
from research.gold_studio.source_bindings import claim_source_partition, required_claim_context_ids
from research.gold_studio.store import digest
from research.product_evaluation.catalog import load_questions, public_manifest
from research.product_evaluation.review_bridge import reference_from_review_export

OPEN_SPLITS = ("development", "regression", "human_candidates", "adversarial")


def private_json(path, value):
    path = Path(path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    write_guard(path, len(raw.encode()))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class ReviewFeedback:
    def __init__(self, protocol, catalog=None):
        self.protocol = protocol
        self.catalog = Path(catalog) if catalog is not None else None
        self.directory = protocol.directory / "feedback"
        self._sync_lock = threading.RLock()
        from crawler.web.review_observations import ReviewObservations

        self.observations = ReviewObservations(
            self.directory / "observations", protocol.snapshot, private_json
        )
        protocol.on_finalized = self.sync

    def sync(self, oid):
        # Individual reviewers can submit concurrently. One derived snapshot writer
        # owns the atomic pointer; other processes fail explicitly for later retry.
        with self._sync_lock:
            lock_path = self.directory / "writer.lock"
            write_guard(lock_path, 0)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+") as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    return self._sync(oid)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _sync(self, oid):
        p = self.protocol
        obj = p.objects()[oid]
        if obj["split"] == "independent_holdout":
            return {"status": "SEALED_HOLDOUT_NO_DEVELOPMENT_SYNC"}
        state = p.state(oid)
        if state not in {"CONSENSUS", "ADJUDICATED"}:
            raise ValueError("Independent final consolidation is required")
        reviews = p._latest("protocol_review", oid)
        final = (
            p._events("protocol_adjudication", oid)[-1]
            if state == "ADJUDICATED"
            else reviews[p.assignment(oid)["reviewers"][0]]
        )
        applied = p._events("protocol_feedback_applied", oid)
        if applied and applied[-1]["payload"]["final_event_hash"] == final["hash"]:
            # The audit event alone does not prove that derived files survived a
            # crash. A retry must restore a missing/corrupt pointer from durable
            # judgments instead of reporting a synchronization that is absent.
            try:
                current = self.status()
                if current.get("state") == "READY":
                    self.observations.report(current["gold_sha256"])
            except (OSError, ValueError, KeyError):
                current = {}
            if current.get("state") == "READY":
                return applied[-1]["payload"]
        # Machine actor performs publication, never a human judgment. Development
        # purpose excludes all sealed holdout material at the source.
        export = p._publish_gold("system:review-feedback", purpose="development")
        gold_path = self.directory / "gold" / (export["content_sha256"] + ".json")
        private_json(gold_path, export)
        references = {}
        if self.catalog is not None:
            manifest = public_manifest(self.catalog)
            if manifest["snapshot_id"] != p.snapshot:
                raise ValueError("Review feedback/catalog snapshot differs")
            for split in OPEN_SPLITS:
                rows = load_questions(self.catalog, split)
                refs = reference_from_review_export(
                    export, rows, allow_technical=p.technical, skip_other_tasks=True
                )
                if not refs:
                    continue
                bundle = dict(
                    operator="system:review-feedback",
                    split=split,
                    references=refs,
                    origin="synthetic" if p.technical else "independent_human",
                    source_gold_version=export["gold_version"],
                    source_export_sha256=export["content_sha256"],
                    complete_universe=False,
                )
                ref_id = digest(bundle)
                private_json(self.catalog / "review-references" / (ref_id + ".json"), bundle)
                references[split] = dict(
                    reference_id=ref_id,
                    tasks=len(refs),
                    origin=bundle["origin"],
                    question_ids=sorted(refs),
                )
        observations = self.observations.refresh(export)
        receipt = dict(
            observations=observations,
            schema="consolidated-review-feedback-v1",
            object_id=oid,
            final_event_hash=final["hash"],
            gold_version=export["gold_version"],
            gold_sha256=export["content_sha256"],
            snapshot_id=p.snapshot,
            references=references,
            human_gold=export["human_gold"],
            technical=p.technical,
            holdout_excluded=True,
        )
        private_json(self.directory / "latest.json", receipt)
        p._event("system:review-feedback", oid, "protocol_feedback_applied", receipt)
        return receipt

    def status(self):
        path = self.directory / "latest.json"
        if not path.exists():
            return dict(state="NO_CONSOLIDATED_FEEDBACK", references={}, human_gold=False)
        receipt = json.loads(path.read_text())
        gold_hash = receipt["gold_sha256"]
        if len(gold_hash) != 64 or any(c not in "0123456789abcdef" for c in gold_hash):
            raise ValueError("Invalid feedback identity")
        gold = json.loads((self.directory / "gold" / (gold_hash + ".json")).read_text())
        if digest({k: v for k, v in gold.items() if k != "content_sha256"}) != gold_hash:
            raise ValueError("Feedback Gold snapshot integrity differs")
        latest = {}
        for oid, obj in self.protocol.objects().items():
            key = (obj["kind"], obj["key"])
            if key not in latest or obj["version"] > latest[key][1]:
                latest[key] = (oid, obj["version"])
        stale = any(
            latest.get((label["kind"], label["key"])) != (label["object_id"], label["version"])
            for label in gold["labels"]
        )
        return {
            **receipt,
            "state": "STALE_MATERIAL_REVIEW_REQUIRED" if stale else "READY",
            "references": {} if stale else receipt["references"],
            "automatic_references_current": not stale,
        }

    def overlay(self, payload, *, show_decisions=False):
        """Return a separate review layer; immutable answer/provenance stay intact."""
        result = copy.deepcopy(payload)
        answer = result.get("answer_result") or {}
        if result.get("snapshot") != self.protocol.snapshot:
            return result
        if not answer.get("answer_text"):
            return result
        output_hash = hashlib.sha256(answer["answer_text"].encode()).hexdigest()
        objects = self.protocol.objects()
        latest = {}
        for oid, obj in objects.items():
            key = (obj["kind"], obj["key"])
            if key not in latest or obj["version"] > latest[key][1]["version"]:
                latest[key] = (oid, obj)
        for claim in (result.get("answer_assessment") or {}).get("claims", []):
            matched = []
            for oid, obj in latest.values():
                body = obj["payload"]
                if (
                    obj["kind"] != "claim"
                    or body.get("claim_id") != claim["id"]
                    or body.get("output_sha256") != output_hash
                ):
                    continue
                proposed = body.get("proposed_text")
                if not isinstance(proposed, str):
                    continue
                reviewed_text = re.sub(r"\s*\[C[0-9]+\]", "", proposed).strip().rstrip(".")
                try:
                    cited_ids, context_ids = claim_source_partition(body, obj["sources"])
                except ValueError:
                    continue
                if (
                    reviewed_text != claim.get("text")
                    or set(cited_ids) != set(claim.get("source_ids", []))
                    or context_ids
                    != required_claim_context_ids(
                        claim.get("source_ids", []),
                        (result.get("retrieval_result") or {}).get("items", []),
                    )
                    or body.get("question") != (result.get("retrieval_result") or {}).get("query")
                ):
                    continue
                state = self.protocol.state(oid)
                if state not in {"CONSENSUS", "ADJUDICATED"}:
                    continue
                reviews = self.protocol._latest("protocol_review", oid)
                final = (
                    self.protocol._events("protocol_adjudication", oid)[-1]
                    if state == "ADJUDICATED"
                    else reviews[self.protocol.assignment(oid)["reviewers"][0]]
                )
                matched.append(
                    dict(
                        object_id=oid,
                        version=obj["version"],
                        decision=final["payload"]["decision"],
                        final_event_hash=final["hash"],
                        human=final["payload"]["human"],
                    )
                )
            if matched:
                conflicting = len({r["decision"] for r in matched}) > 1
                human = not self.protocol.technical and all(r["human"] for r in matched)
                claim["review"] = dict(
                    state="CONFLICTING_CONSOLIDATED_REVIEWS"
                    if conflicting
                    else "REVIEW_DEFERRED"
                    if all(
                        r["decision"] in {"INSUFFICIENT_EVIDENCE", "NEEDS_FURTHER_REVIEW"}
                        for r in matched
                    )
                    else "INDEPENDENT_HUMAN_REVIEW"
                    if human
                    else "TECHNICAL_REVIEW_NOT_HUMAN_GOLD",
                    human_gold=human and not conflicting,
                    source_status_unchanged=True,
                    decision=matched[0]["decision"] if show_decisions and not conflicting else None,
                    provenance=matched if show_decisions else [],
                )
        return result
