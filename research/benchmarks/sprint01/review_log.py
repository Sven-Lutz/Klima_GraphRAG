"""Verified append-only storage, with semantic retry identity and batch preflight."""

from __future__ import annotations

import fcntl
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from research.offline_evaluation.contracts import canonical, json_bytes, require


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_log(data: bytes) -> list[dict]:
    """Check the complete chain before returning events or allowing any append."""
    require(not data or data.endswith(b"\n"), "Review log has partial final record")
    events: list[dict] = []
    previous = None
    identities: set[str] = set()
    for line in data.splitlines():
        event = json_bytes(line)
        require(isinstance(event, dict), "Review log event must be an object")
        payload = {k: v for k, v in event.items() if k != "event_sha256"}
        require(
            event.get("event_sha256") == digest(canonical(payload))
            and event.get("previous_event_sha256") == previous,
            "Review log integrity/chain mismatch",
        )
        require(
            event.get("schema_version") in {"klima-human-review-log/1", "klima-human-review-log/2"},
            "Unsupported review log schema",
        )
        require(isinstance(event.get("decision"), dict), "Missing logged decision")
        identity = digest(canonical(event["decision"]))
        require(identity not in identities, "Duplicate semantic decision in review log")
        if event["schema_version"] == "klima-human-review-log/2":
            require(event.get("decision_sha256") == identity, "Decision identity mismatch")
        identities.add(identity)
        previous = event["event_sha256"]
        events.append(event)
    return events


def append_decisions(
    log: Path, decisions: list[dict], manifest: dict, supplied_sha256: str
) -> dict:
    """Persist prevalidated decisions; retries are no-ops, conflicts fail as a batch.

    Candidate validation belongs to the caller. A single locked write is preceded
    by full chain and batch validation. A truncated/crashed write is detected on
    the next read, never repaired by dropping evidence. This is not a trusted
    timestamp or proof of the identity of a human reviewer.
    """
    require(decisions, "Empty decision batch")
    identities = [digest(canonical(record)) for record in decisions]
    require(len(identities) == len(set(identities)), "Duplicate decision within input batch")
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        before = stream.read()
        existing = verify_log(before)
        by_identity = {digest(canonical(e["decision"])): e for e in existing}
        latest = {
            (e["decision"]["reviewer_id"], e["decision"]["candidate_id"]): e for e in existing
        }
        previous = existing[-1]["event_sha256"] if existing else None
        pending: list[dict] = []
        results = []
        for record, identity in zip(decisions, identities):
            if identity in by_identity:
                results.append({"status": "ALREADY_IMPORTED", "event": by_identity[identity]})
                continue
            key = (record["reviewer_id"], record["candidate_id"])
            old = latest.get(key)
            if record.get("schema_version") == "klima-human-decision/2" and old:
                require(
                    record.get("supersedes_event_sha256") == old["event_sha256"],
                    "Changed decision requires explicit supersedes_event_sha256",
                )
            event = {
                "schema_version": "klima-human-review-log/2",
                "decision": record,
                "decision_sha256": identity,
                "decision_file_sha256": supplied_sha256,
                "candidate_file_sha256": manifest["candidate_file_sha256"],
                "previous_event_sha256": previous,
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "human_identity_and_actual_work_not_machine_certified": True,
            }
            event["event_sha256"] = digest(canonical(event))
            previous = event["event_sha256"]
            pending.append(event)
            latest[key] = event
            by_identity[identity] = event
            results.append({"status": "IMPORTED", "event": event})
        appended = b"".join(canonical(e) + b"\n" for e in pending)
        verify_log(before + appended)
        if appended:
            stream.write(appended)
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        after = stream.read()
        require(after == before + appended, "Review log post-write verification failed")
        return {
            "schema_version": "klima-review-import-receipt/2",
            "imported": len(pending),
            "already_imported": len(results) - len(pending),
            "before_sha256": digest(before),
            "after_sha256": digest(after),
            "event_count": len(verify_log(after)),
            "chain_tip": previous,
            "candidate_file_sha256": manifest["candidate_file_sha256"],
            "input_file_sha256": supplied_sha256,
            "results": results,
            "gold_release": "NOT_AUTHORIZED",
            "m4_m5": "NOT_AUTHORIZED",
        }
