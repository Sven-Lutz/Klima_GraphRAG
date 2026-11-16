"""Anonymous local versioned notes, explicitly not independent Human Gold."""

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

LABELS = [
    "Supported",
    "Partially supported",
    "Unsupported",
    "Contradicted",
    "Insufficient evidence",
    "Ambiguous / Needs review",
]
KINDS = ["source", "claim", "extraction", "relation", "answer_evidence"]

LABELS_BY_KIND = {
    "claim": LABELS,
    "source": [
        "Relevant authoritative source",
        "Relevant authority unclear",
        "Irrelevant",
        "Wrong municipality or source version",
        "Insufficient evidence",
        "Ambiguous / Needs review",
    ],
    "extraction": [
        "Correct extraction",
        "Partially correct",
        "Incorrect extraction",
        "Unknown fields",
        "Insufficient evidence",
        "Ambiguous / Needs review",
    ],
    "relation": [
        "Supported relation",
        "Partially supported relation",
        "Unsupported relation",
        "Wrong endpoints or roles",
        "Insufficient evidence",
        "Ambiguous / Needs review",
    ],
    "answer_evidence": [
        "Relevant and complete",
        "Relevant but incomplete",
        "Irrelevant",
        "Incorrect scope or time",
        "Insufficient evidence",
        "Ambiguous / Needs review",
    ],
}


class NoteInput(BaseModel):
    conversation_id: str = Field(max_length=100)
    turn_id: str = Field(max_length=100)
    claim_id: str | None = Field(default=None, max_length=100)
    kind: str = Field(max_length=30)
    label: str | None = Field(default=None, max_length=40)
    comment: str = Field(default="", max_length=2000)
    state: str = Field(default="draft", max_length=20)
    origin: Literal["UNVERIFIED_LOCAL_INTERACTION", "AUTOMATED_TECHNICAL_TEST"] = (
        "UNVERIFIED_LOCAL_INTERACTION"
    )


class LocalEvents:
    def __init__(self, directory, budget):
        self.directory, self.budget = Path(directory), budget
        self.lock = threading.Lock()

    def path(self, owner):
        return self.directory / hashlib.sha256(owner.encode()).hexdigest()

    def read(self, owner):
        path = self.path(owner)
        if not path.exists():
            return []
        if path.is_symlink():
            raise ValueError("Regular event directory required")
        files = sorted(path.glob("*.json"))
        if len(files) > 4096:
            raise ValueError("Local event budget exceeded")
        events = []
        for file in files:
            if file.is_symlink() or file.stat().st_size > 1024**2:
                raise ValueError("Invalid local event")
            events.append(json.loads(file.read_text()))
        return events

    def append(self, owner, event):
        with self.lock:
            self.budget()
            previous = self.read(owner)
            if len(previous) >= 4096:
                raise HTTPException(429, "Local event budget reached; export first")
            raw = json.dumps(event, ensure_ascii=False).encode()
            if len(raw) > 1024**2:
                raise HTTPException(413, "Bounded event budget exceeded")
            total = sum(p.stat().st_size for p in self.directory.glob("*/*.json"))
            if total + len(raw) > 64 * 1024**2:
                raise HTTPException(429, "Local event storage budget reached")
            path = self.path(owner)
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = path / f"{len(previous):04}-{secrets.token_hex(8)}.json"
            pending = target.with_suffix(".pending")
            fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(pending, target)  # Atomic publication, never overwrite an existing version.
            finally:
                pending.unlink(missing_ok=True)
            return event


def attach_local_notes(app, store, actor):
    events = LocalEvents(store.directory / "local-notes", store.budget)
    app.state.local_notes = events

    @app.get("/api/chat/local-review")
    def queue(request: Request):
        owner = actor(request)
        items = []
        with store.conn() as conn:
            chats = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM chats WHERE owner=? AND snapshot=?", (owner, store.snapshot)
                )
            ]
        for cid in chats:
            for turn in store.turns(owner, cid):
                for claim in turn["claims"]:
                    items.append(
                        {
                            "conversation_id": cid,
                            "turn_id": turn["turn_id"],
                            "claim": claim,
                            "question": turn["question"],
                            "answer": turn["answer"],
                            "snapshot": store.snapshot,
                        }
                    )
        return {
            "items": items[:200],
            "cutoff": len(items) > 200,
            "labels": LABELS_BY_KIND,
            "kinds": KINDS,
            "events": events.read(owner),
            "human_gold": False,
            "validation": "LOCAL_SINGLE_REVIEWER_UNVALIDATED",
        }

    @app.post("/api/chat/local-review")
    def note(data: NoteInput, request: Request):
        from crawler.web.conversation import now

        owner = actor(request)
        if (
            data.kind not in KINDS
            or data.state not in {"draft", "judgment"}
            or (data.label is not None and data.label not in LABELS_BY_KIND.get(data.kind, []))
        ):
            raise HTTPException(422, "Known note contract required")
        turn = next(
            (t for t in store.turns(owner, data.conversation_id) if t["turn_id"] == data.turn_id),
            None,
        )
        claim = (
            next((c for c in turn["claims"] if c["claim_id"] == data.claim_id), None)
            if turn
            else None
        )
        if not turn or not claim:
            raise HTTPException(404, "Own source-bound claim required")
        if data.kind == "relation" and not turn["graph"]["edges"]:
            raise HTTPException(422, "No source-bound relation available")
        if data.state == "judgment" and not data.label:
            raise HTTPException(422, "Explicit label required")
        return events.append(
            owner,
            {
                "id": secrets.token_hex(16),
                "at": now(),
                "schema": "local-note-v1",
                **data.model_dump(),
                "snapshot": store.snapshot,
                "evidence": claim["evidence"],
                "human_gold": False,
                "validation": "LOCAL_SINGLE_REVIEWER_UNVALIDATED",
            },
        )

    @app.get("/api/chat/local-review/export")
    def export(request: Request):
        return {
            "schema": "local-notes-export-v1",
            "snapshot": store.snapshot,
            "events": events.read(actor(request)),
            "human_gold": False,
            "validation": "LOCAL_SINGLE_REVIEWER_UNVALIDATED",
        }
