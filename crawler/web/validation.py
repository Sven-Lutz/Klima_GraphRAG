"""Dynamic exploratory evidence review over the existing Gold Studio audit store.

This adapter never opens a frozen study or promotes exploratory judgments to human gold.
Original evidence stays immutable; reviews/corrections are append-only derived events.
"""

import copy
import json
import secrets
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from crawler.core.retrieval import RetrievalError
from crawler.web.bavaria import read_original
from research.bavaria.federation import source_connection
from research.gold_studio.store import Store, digest

RUBRIC = "evidence-validation-exploratory-v1"
DIMENSIONS = {
    "source": [
        "publisher",
        "municipality",
        "document_identity",
        "temporal",
        "document_type",
        "provenance",
        "duplicates",
        "extraction",
        "scientific_suitability",
    ],
    "entity": ["identity", "type", "geography", "temporal", "evidence_support"],
    "relation": [
        "entity_roles",
        "relation_type",
        "evidence_support",
        "geography",
        "temporal",
        "missing_relations",
    ],
    "retrieval": [
        "relevance",
        "completeness",
        "factual_correctness",
        "citation_support",
        "temporal",
        "municipality",
        "contradictions",
        "missing_evidence",
        "unsupported_claims",
    ],
}
RATINGS = {
    "NOT_JUDGED",
    "SUPPORTED",
    "CONTRADICTED",
    "UNCERTAIN",
    "NOT_APPLICABLE",
    "RELEVANT",
    "PARTIAL",
    "IRRELEVANT",
    "VALID",
    "INVALID",
    "MISSING",
}
DECISIONS = {"NOT_JUDGED", "CONFIRM", "REJECT", "UNCERTAIN"}
EVENT_FIELDS = {"at", "role", "task", "stage", "kind", "previous", "payload", "hash"}


def validate_event_shapes(data):
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ValueError("Structured review snapshot with events required")
    for event in data["events"]:
        if (
            not isinstance(event, dict)
            or not EVENT_FIELDS <= event.keys()
            or not isinstance(event["payload"], dict)
            or any(not isinstance(event[k], str) for k in EVENT_FIELDS - {"payload"})
        ):
            raise ValueError("Complete typed audit event required")


COMMON = ["NOT_JUDGED", "SUPPORTED", "CONTRADICTED", "UNCERTAIN", "NOT_APPLICABLE"]
OPTIONS = {
    kind: {dimension: list(COMMON) for dimension in dimensions}
    for kind, dimensions in DIMENSIONS.items()
}
OPTIONS["retrieval"]["relevance"] = ["NOT_JUDGED", "RELEVANT", "PARTIAL", "IRRELEVANT", "UNCERTAIN"]
OPTIONS["retrieval"]["completeness"] = [
    "NOT_JUDGED",
    "COMPLETE",
    "INCOMPLETE",
    "UNCERTAIN",
    "NOT_APPLICABLE",
]
for kind, dimensions in OPTIONS.items():
    for dimension in dimensions:
        if dimension in {
            "duplicates",
            "contradictions",
            "missing_evidence",
            "unsupported_claims",
            "missing_relations",
        }:
            dimensions[dimension] = [
                "NOT_JUDGED",
                "DETECTED",
                "NOT_DETECTED",
                "UNCERTAIN",
                "NOT_APPLICABLE",
            ]
OPTIONS["source"]["scientific_suitability"] = ["NOT_JUDGED", "VALID", "INVALID", "UNCERTAIN"]
RATINGS |= {"COMPLETE", "INCOMPLETE", "DETECTED", "NOT_DETECTED"}


class ExploratoryStore(Store):
    """Reuse the existing audit contract with deterministic connection cleanup."""

    @contextmanager
    def conn(self):
        with closing(super().conn()) as connection:
            with connection:
                yield connection


class ValidationWorkspace:
    def __init__(self, service, index, directory, technical=False):
        self.service, self.index = service, index
        self.snapshot = service.database_snapshot_id
        self.technical = technical
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        selection = {
            "snapshot_id": self.snapshot,
            "scope": "EXPLORATORY_ONLY",
            "rubric": RUBRIC,
            "calibration": [],
            "main": [],
        }
        self.store = ExploratoryStore(
            self.directory / "reviews.sqlite",
            {"selection": selection, "tasks": [], "sources": []},
            simulation=technical,
        )
        self.feedback_store = ExploratoryStore(
            self.directory / "pilot-feedback.sqlite",
            {"selection": selection, "tasks": [], "sources": []},
            simulation=technical,
        )
        self.keys = json.loads(self.store.meta("keys"))
        codes = self.directory / "reviewer-codes.txt"
        if not codes.exists():
            codes.write_text(
                "Local exploratory workspace. A/B independent views; C adjudication.\n"
                + "\n".join(k + ": " + v for k, v in self.keys.items())
            )
            codes.chmod(0o600)
        self.sessions = {}
        self.verified_sources = set()

    def events(self, kind, oid=None, role=None):
        filters = ["kind=?"]
        parameters = [kind]
        if oid is not None:
            filters.append("task=?")
            parameters.append(oid)
        if role is not None:
            filters.append("role=?")
            parameters.append(role)
        with self.store.conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE " + " AND ".join(filters) + " ORDER BY seq", parameters
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def objects(self):
        return {e["task"]: e["payload"] for e in self.events("validation_object")}

    def register(self, kind, key, payload):
        if kind not in DIMENSIONS:
            raise ValueError("Unknown validation object")
        obj = dict(
            kind=kind,
            key=key,
            snapshot_id=self.snapshot,
            payload=payload,
            split="exploratory",
            gold=False,
        )
        oid = "review-object:" + digest({"kind": kind, "key": key, "snapshot": self.snapshot})
        if len(json.dumps(obj)) > 2 * 1024**2:
            raise ValueError("Evidence object exceeds review budget; narrow the search")
        existing = self.events("validation_object", oid)
        current = existing[-1]["payload"] if existing else None
        if current and current != obj:
            raise ValueError("Stable object changed; create an explicit new evidence version")
        if not current:
            with self.store.conn() as c:
                c.execute("BEGIN IMMEDIATE")
                self.store.event(c, "SYSTEM", oid, kind, "validation_object", obj)
        return oid

    def source_payload(self, sha):
        with self.index.conn() as c:
            row = c.execute(
                "SELECT sha256,url,raw_bytes,status,kind,unit_count FROM sources WHERE sha256=?",
                (sha,),
            ).fetchone()
            frames = [
                r[0]
                for r in c.execute(
                    "SELECT ags FROM source_frames WHERE sha256=? ORDER BY ags", (sha,)
                )
            ]
        if not row:
            raise ValueError("Unknown source")
        payload = {
            **dict(row),
            "municipality_ids": frames,
            "original_url": "/api/validation/original/" + sha,
            "technical_integrity": "NOT_CHECKED_UNTIL_ORIGINAL_OPENED",
            "scientific_validation": "NOT_JUDGED",
            "external_rights": "NOT_ASSESSED_LOCAL_ONLY",
        }
        return payload

    def source(self, sha):
        return self.register("source", sha, self.source_payload(sha))

    def handoff(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("kind"), str):
            raise ValueError("Explicit handoff object required")
        if body["kind"] in {"source", "entity", "relation"} and not isinstance(
            body.get("key"), str
        ):
            raise ValueError("Stable object key required")
        if body["kind"] == "retrieval" and not isinstance(body.get("result_id"), str):
            raise ValueError("Stored result identity required")
        kind = body.get("kind")
        w = self.service.workspace
        if kind == "source":
            return self.source(body["key"])
        if kind == "entity":
            key = body["key"]
            if key not in w.entities:
                raise ValueError("Unknown entity")
            related = [r for r in w.relation_rows if key in (r["subject_id"], r["object_id"])]
            return self.register(
                kind,
                key,
                {
                    "entity": w.entities[key],
                    "relation_ids": [r["relation_id"] for r in related],
                    "evidence": [w.evidence_rows[r["evidence_id"]] for r in related],
                },
            )
        if kind == "relation":
            relation = next((r for r in w.relation_rows if r["relation_id"] == body["key"]), None)
            if relation is None:
                raise ValueError("Unknown relation")
            evidence_ids = [
                relation["evidence_id"],
                *relation["record"].get("context_evidence_ids", []),
            ]
            return self.register(
                kind,
                body["key"],
                {
                    "relation": relation,
                    "evidence": [w.evidence_rows[k] for k in evidence_ids],
                    "subject": w.entities[relation["subject_id"]],
                    "object": w.entities[relation["object_id"]],
                },
            )
        if kind == "retrieval":
            raw = self.service.export(body["result_id"], "json")
            if raw is None:
                raise ValueError("Result expired; repeat the search")
            result = json.loads(raw)
            # Content-address the entire immutable canonical result, not a transient browser ID.
            return self.register(kind, digest(result), result)
        raise ValueError("Unsupported handoff")

    def reviews(self, oid, role):
        return self.events("validation_review", oid, None if role == "C" else role)

    def latest_reviews(self, oid):
        latest = {}
        for event in self.events("validation_review", oid):
            latest[event["role"]] = event
        return latest

    def conflict(self, oid):
        rows = self.latest_reviews(oid)
        if not {"A", "B"} <= rows.keys():
            return False
        a, b = [rows[r]["payload"] for r in ("A", "B")]
        # NOT_JUDGED means missingness, never a negative label or automatic conflict.
        mismatch = a["decision"] != b["decision"] and "NOT_JUDGED" not in {
            a["decision"],
            b["decision"],
        }
        mismatch |= any(
            a["ratings"][k] != b["ratings"][k]
            and "NOT_JUDGED" not in {a["ratings"][k], b["ratings"][k]}
            for k in a["ratings"]
        )
        return mismatch

    def save(self, oid, role, form, adjudicate=False):
        if not isinstance(form, dict):
            raise ValueError("Structured review form required")
        obj = self.objects().get(oid)
        if not obj:
            raise ValueError("Unknown review object")
        if adjudicate and (role != "C" or not self.conflict(oid)):
            raise ValueError("Adjudication requires role C and an actual conflict")
        if not isinstance(form.get("decision"), str) or not isinstance(form.get("ratings"), dict):
            raise ValueError("Typed decision and rubric ratings required")
        if form.get("decision") not in DECISIONS or set(form.get("ratings", {})) != set(
            DIMENSIONS[obj["kind"]]
        ):
            raise ValueError(
                "Complete explicit rubric required; use NOT_JUDGED for missing judgments"
            )
        if any(v not in OPTIONS[obj["kind"]][k] for k, v in form["ratings"].items()):
            raise ValueError("Unknown rubric rating")
        comment = form.get("comment", "")
        corrections = form.get("corrections", "")
        if not isinstance(form.get("reviewer"), str):
            raise ValueError("Reviewer identity required")
        reviewer = form["reviewer"].strip()
        if (
            not reviewer
            or len(reviewer) > 100
            or not isinstance(comment, str)
            or len(comment) > 8000
            or not isinstance(corrections, str)
            or len(corrections) > 8000
        ):
            raise ValueError("Reviewer identity and bounded comment/corrections required")
        if adjudicate and not comment.strip():
            raise ValueError("Adjudication rationale required")
        human = form.get("human_confirmed") is True and not self.technical
        if not self.technical and not human:
            raise ValueError(
                "Confirm personal inspection; automated testing requires "
                "a separate technical workspace"
            )
        quote = form.get("evidence_quote", "")
        if not isinstance(quote, str) or len(quote) > 4000:
            raise ValueError("Bounded original evidence quote required")
        if quote:
            if obj["kind"] == "source":
                with closing(
                    source_connection(
                        self.service.workspace.db, self.service.workspace.source_databases
                    )
                ) as c:
                    supported = c.execute(
                        "SELECT 1 FROM units WHERE sha256=? AND instr(text,?)>0 LIMIT 1",
                        (obj["key"], quote),
                    ).fetchone()
            else:

                def texts(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if key in {"text", "evidence_text", "quote"} and isinstance(item, str):
                                yield item
                            else:
                                yield from texts(item)
                    elif isinstance(value, list):
                        for item in value:
                            yield from texts(item)

                supported = any(quote in text for text in texts(obj["payload"]))
            if not supported:
                raise ValueError("Evidence quote is not contained in this object's source text")
        payload = dict(
            evidence_quote=quote,
            rubric_version=RUBRIC,
            split="exploratory",
            human=human,
            gold=False,
            reviewer=reviewer,
            independence=form.get("independence", "NOT_DECLARED"),
            decision=form["decision"],
            ratings=form["ratings"],
            comment=comment,
            corrections=corrections,
        )
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self.store.event(
                c,
                role,
                oid,
                obj["kind"],
                "validation_adjudication" if adjudicate else "validation_review",
                payload,
            )
        return self.store.audit()

    def export(self):
        return dict(
            schema="klima-exploratory-review-v1",
            snapshot_id=self.snapshot,
            rubric_version=RUBRIC,
            technical=self.technical,
            gold=False,
            split="exploratory",
            audit_head=self.store.audit(),
            events=self.store.events(),
        )

    def import_snapshot(self, data):
        validate_event_shapes(data)
        if not isinstance(data, dict):
            raise ValueError("Structured review snapshot required")
        if (
            data.get("schema") != "klima-exploratory-review-v1"
            or data.get("snapshot_id") != self.snapshot
            or data.get("technical") is not self.technical
            or data.get("gold") is not False
            or data.get("split") != "exploratory"
            or data.get("rubric_version") != RUBRIC
        ):
            raise ValueError("Wrong snapshot, split, rubric or technical/human boundary")
        if (
            not isinstance(data.get("events"), list)
            or len(data["events"]) > 10000
            or len(json.dumps(data)) > 8 * 1024**2
        ):
            raise ValueError("Bounded review snapshot required")
        previous = "GENESIS"
        known = {}
        for event in data["events"]:
            record = {
                k: event[k] for k in ("at", "role", "task", "stage", "kind", "previous", "payload")
            }
            if event["previous"] != previous or digest(record) != event["hash"]:
                raise ValueError("Imported audit chain invalid")
            if event["kind"] not in {
                "validation_object",
                "validation_review",
                "validation_adjudication",
            }:
                raise ValueError("Not an exploratory evidence event")
            payload = event["payload"]
            if payload.get("split") != "exploratory" or payload.get("gold") is not False:
                raise ValueError("Cannot import confirmation or gold events")
            if event["kind"] == "validation_object":
                expected = "review-object:" + digest(
                    {
                        "kind": payload.get("kind"),
                        "key": payload.get("key"),
                        "snapshot": self.snapshot,
                    }
                )
                if (
                    payload.get("snapshot_id") != self.snapshot
                    or payload.get("kind") not in DIMENSIONS
                    or event["task"] != expected
                    or event["role"] != "SYSTEM"
                    or event["stage"] != payload["kind"]
                ):
                    raise ValueError("Invalid evidence object binding")
                if payload["kind"] == "source":
                    if digest(payload["payload"]) != digest(self.source_payload(payload["key"])):
                        raise ValueError("Imported source metadata differs from immutable snapshot")
                elif payload["kind"] == "entity":
                    key = payload["key"]
                    if (
                        key not in self.service.workspace.entities
                        or payload["payload"].get("entity") != self.service.workspace.entities[key]
                    ):
                        raise ValueError("Imported entity differs from immutable snapshot")
                elif payload["kind"] == "relation":
                    stored = next(
                        (
                            r
                            for r in self.service.workspace.relation_rows
                            if r["relation_id"] == payload["key"]
                        ),
                        None,
                    )
                    if stored is None or digest(payload["payload"].get("relation")) != digest(
                        stored
                    ):
                        raise ValueError("Imported relation differs from immutable snapshot")
                elif digest(payload["payload"]) != payload["key"]:
                    raise ValueError("Imported result does not match its content identity")
                if event["task"] in known and known[event["task"]] != payload:
                    raise ValueError("Conflicting immutable evidence object")
                known[event["task"]] = payload
            else:
                obj = known.get(event["task"])
                if (
                    not obj
                    or event["role"] not in {"A", "B", "C"}
                    or event["stage"] != obj["kind"]
                    or payload.get("rubric_version") != RUBRIC
                    or payload.get("decision") not in DECISIONS
                    or set(payload.get("ratings", {})) != set(DIMENSIONS[obj["kind"]])
                    or any(v not in OPTIONS[obj["kind"]][k] for k, v in payload["ratings"].items())
                    or not isinstance(payload.get("human"), bool)
                    or (self.technical and payload["human"])
                ):
                    raise ValueError("Invalid review rubric or technical/human boundary")
                if event["kind"] == "validation_adjudication" and (
                    event["role"] != "C" or not payload.get("comment", " ").strip()
                ):
                    raise ValueError("Invalid adjudication")
            previous = event["hash"]
        if previous != data["audit_head"]:
            raise ValueError("Import audit head mismatch")
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = list(c.execute("SELECT hash FROM events ORDER BY seq"))
            hashes = [e["hash"] for e in data["events"]]
            if [r[0] for r in existing] != hashes[: len(existing)]:
                raise ValueError(
                    "Divergent history: import into a fresh workspace; do not overwrite decisions"
                )
            for e in data["events"][len(existing) :]:
                c.execute(
                    "INSERT INTO events(at,role,task,stage,kind,payload,previous,hash) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    tuple(e[k] for k in ("at", "role", "task", "stage", "kind"))
                    + (
                        json.dumps(
                            e["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        ),
                        e["previous"],
                        e["hash"],
                    ),
                )
        return self.store.audit()


def attach_validation(app, service, index, directory, technical=False):
    studio = ValidationWorkspace(service, index, directory, technical)
    app.state.validation = studio

    def role(request):
        value = studio.sessions.get(request.cookies.get("validation_session"))
        if not value:
            raise HTTPException(401, "Start a local review session")
        return value

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"error": {"code": "invalid_review", "message": str(exc)[:250]}}, status_code=422
        )

    @app.post("/api/validation/session")
    async def session(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "Object required")
        code = body.get("code")
        selected = (
            next(
                (r for r, key in studio.keys.items() if secrets.compare_digest(str(code), key)),
                None,
            )
            if code
            else "A"
        )
        if selected is None:
            raise HTTPException(403, "Invalid reviewer code")
        token = secrets.token_urlsafe(32)
        if len(studio.sessions) >= 30:
            studio.sessions.pop(next(iter(studio.sessions)))
        studio.sessions[token] = selected
        response = JSONResponse(
            dict(
                role=selected,
                technical=technical,
                rubric_version=RUBRIC,
                dimensions=DIMENSIONS,
                options=OPTIONS,
                ratings=sorted(RATINGS),
                decisions=sorted(DECISIONS),
            )
        )
        response.set_cookie("validation_session", token, httponly=True, samesite="strict")
        return response

    @app.get("/api/validation/catalog")
    async def catalog(
        request: Request,
        q: str = "",
        ags: str = "",
        region: str = "",
        kind: str = "",
        suspect: bool = False,
        offset: int = 0,
    ):
        role(request)
        if not 0 <= offset <= 10000 or len(q) > 200:
            raise HTTPException(422, "Invalid catalog scope")
        filters, params = [], []
        if q:
            filters.append("(s.url LIKE ? OR s.sha256 LIKE ?)")
            params += ["%" + q + "%"] * 2
        if ags and ags not in service.workspace.municipalities:
            raise HTTPException(422, "Unknown municipality")
        if region and region not in {
            v.get("region") for v in service.workspace.municipalities.values()
        }:
            raise HTTPException(422, "Unknown region")
        frames = (
            [ags]
            if ags
            else [
                k for k, v in service.workspace.municipalities.items() if v.get("region") == region
            ]
            if region
            else []
        )
        if frames:
            filters.append(
                "s.sha256 IN (SELECT sha256 FROM source_frames WHERE ags IN ("
                + ",".join("?" for _ in frames)
                + "))"
            )
            params += frames
        if kind:
            filters.append(
                "CASE WHEN s.kind='pdf_page' THEN 'pdf' "
                "WHEN s.kind IN ('heading','paragraph','list_item','html') "
                "THEN 'html' ELSE 'unknown' END=?"
            )
            params.append(kind)
        if suspect:
            filters.append("(s.unit_count=0 OR s.status NOT IN ('TEXT_EXTRACTED','PARSED'))")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with index.conn() as c:
            total = c.execute("SELECT count(*) FROM sources s" + where, params).fetchone()[0]
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT sha256,url,kind,status,unit_count FROM sources s"
                    + where
                    + " ORDER BY sha256 LIMIT 30 OFFSET ?",
                    [*params, offset],
                )
            ]
        for row in rows:
            row["document_type_hint"] = (
                "pdf"
                if row["kind"] == "pdf_page"
                else "html"
                if row["kind"] in {"heading", "paragraph", "list_item", "html"}
                else "unknown"
            )
        return dict(
            total=total,
            items=rows,
            offset=offset,
            sampling="deterministic_sha_order_not_representative_random_sample",
        )

    @app.post("/api/feedback")
    async def pilot_feedback(request: Request):
        reviewer_role = role(request)
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("message"), str):
            raise HTTPException(422, "Feedback message required")
        if not body["message"].strip() or len(body["message"]) > 4000:
            raise HTTPException(422, "Feedback message must contain 1–4000 characters")
        if body.get("category") not in {
            "bug",
            "ux",
            "source",
            "fact",
            "feature",
            "science",
            "relation",
            "application",
        }:
            raise HTTPException(422, "Unknown feedback category")
        if body.get("snapshot_id") != studio.snapshot or body.get("research_gold") is not False:
            raise HTTPException(422, "Feedback must match this snapshot and remain non-Gold")
        context = body.get("context") if body.get("context_included") is True else None
        if context is not None and (
            not isinstance(context, dict) or len(json.dumps(context)) > 12000
        ):
            raise HTTPException(422, "Bounded feedback context required")
        payload = dict(
            message=body["message"].strip(),
            category=body["category"],
            snapshot_id=studio.snapshot,
            software=app.state.release_id,
            context=context,
            research_gold=False,
            split="pilot_feedback",
            human_gold=False,
            technical=technical,
        )
        feedback_id = "feedback:" + secrets.token_hex(16)
        with studio.feedback_store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            studio.feedback_store.event(
                c, reviewer_role, feedback_id, "pilot", "pilot_feedback", payload
            )
        return {"feedback_id": feedback_id, "stored": True, "research_gold": False}

    @app.get("/api/feedback")
    async def own_feedback(request: Request):
        reviewer_role = role(request)
        return {
            "research_gold": False,
            "items": [
                e
                for e in studio.feedback_store.events()
                if reviewer_role == "C" or e["role"] == reviewer_role
            ],
        }

    @app.post("/api/validation/handoff")
    async def handoff(request: Request):
        role(request)
        return {"object_id": studio.handoff(await request.json())}

    @app.get("/api/validation/object/{oid:path}")
    async def object_view(oid: str, request: Request):
        r = role(request)
        obj = studio.objects().get(oid)
        if obj is None:
            raise HTTPException(404, "Unknown object")
        return dict(
            object_id=oid,
            **obj,
            history=studio.reviews(oid, r),
            technical_integrity=(
                "SHA256_VERIFIED_THIS_PROCESS"
                if obj["kind"] == "source" and obj["key"] in studio.verified_sources
                else "NOT_CHECKED_THIS_PROCESS"
            ),
            conflict=studio.conflict(oid) if r == "C" else None,
            adjudications=[
                e for e in studio.store.events(kind="validation_adjudication") if e["task"] == oid
            ]
            if r == "C"
            else [],
        )

    @app.post("/api/validation/review/{oid:path}")
    async def save(oid: str, request: Request):
        r = role(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "Structured review form required")
        return {"audit_head": studio.save(oid, r, body, adjudicate=body.get("adjudicate", False))}

    @app.get("/api/validation/queue")
    async def queue(
        request: Request,
        status: str = "",
        kind: str = "",
        ags: str = "",
        region: str = "",
        document_type: str = "",
    ):
        r = role(request)
        rows = []
        reviews_by_object: dict[str, dict[str, dict]] = {}
        for event in studio.events("validation_review"):
            reviews_by_object.setdefault(event["task"], {})[event["role"]] = event
        for oid, obj in studio.objects().items():
            latest = reviews_by_object.get(oid, {})
            own = sorted(
                [e for rr, e in latest.items() if r == "C" or rr == r], key=lambda e: e["seq"]
            )
            last = own[-1]["payload"] if own else None
            state = "NOT_JUDGED" if not last or last["decision"] == "NOT_JUDGED" else "REVIEWED"
            due = None
            if r == "C" and studio.conflict(oid):
                due = max(
                    datetime.fromisoformat(e["at"]) for e in studio.latest_reviews(oid).values()
                ) + timedelta(days=7)
                state = "OVERDUE" if datetime.now(timezone.utc) > due else "CONFLICT"
                adjud = [
                    e
                    for e in studio.store.events(kind="validation_adjudication")
                    if e["task"] == oid
                ]
                if adjud and adjud[-1]["seq"] > max(
                    e["seq"] for e in studio.latest_reviews(oid).values()
                ):
                    state = "ADJUDICATED"
            if (not status or state == status) and (not kind or obj["kind"] == kind):
                rows.append(
                    dict(
                        object_id=oid,
                        kind=obj["kind"],
                        key=obj["key"],
                        status=state,
                        adjudication_due=due.isoformat() if due else None,
                    )
                )
        registered = studio.objects()
        if status in {"", "NOT_JUDGED"}:
            w = service.workspace
            for target_kind, keys in (
                ("entity", sorted(w.entities)),
                ("relation", [x["relation_id"] for x in w.relation_rows]),
            ):
                if kind and kind != target_kind:
                    continue
                for key in keys:
                    oid = "review-object:" + digest(
                        {"kind": target_kind, "key": key, "snapshot": studio.snapshot}
                    )
                    if oid not in registered:
                        rows.append(
                            dict(
                                object_id=oid,
                                kind=target_kind,
                                key=key,
                                status="NOT_JUDGED",
                                registered=False,
                            )
                        )
        if ags or region or document_type:
            frames = (
                [ags]
                if ags
                else [
                    key
                    for key, m in service.workspace.municipalities.items()
                    if m.get("region") == region
                ]
                if region
                else []
            )
            if (ags and ags not in service.workspace.municipalities) or (region and not frames):
                raise HTTPException(422, "Unknown municipality or region")
            conditions, parameters = [], []
            if frames:
                conditions.append("f.ags IN (" + ",".join("?" for _ in frames) + ")")
                parameters.extend(frames)
            if document_type:
                conditions.append(
                    "CASE WHEN s.kind='pdf_page' THEN 'pdf' "
                    "WHEN s.kind IN ('heading','paragraph','list_item','html') "
                    "THEN 'html' ELSE 'unknown' END=?"
                )
                parameters.append(document_type)
            with index.conn() as c:
                eligible = {
                    x[0]
                    for x in c.execute(
                        "SELECT DISTINCT s.sha256 FROM sources s "
                        "JOIN source_frames f USING(sha256) WHERE " + " AND ".join(conditions),
                        parameters,
                    )
                }

            def shas(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "source_sha256" and isinstance(item, str):
                            yield item
                        elif (
                            key == "document_id"
                            and isinstance(item, str)
                            and item.startswith("source-version:")
                        ):
                            yield item.removeprefix("source-version:")
                        else:
                            yield from shas(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from shas(item)

            related = {
                rr["relation_id"]: {rr["record"]["source_sha256"]}
                for rr in service.workspace.relation_rows
            }
            for rr in service.workspace.relation_rows:
                for key in (rr["subject_id"], rr["object_id"]):
                    related.setdefault(key, set()).add(rr["record"]["source_sha256"])
            objects = studio.objects()
            rows = [
                row
                for row in rows
                if (
                    (
                        {row["key"]}
                        if row["kind"] == "source"
                        else set(shas(objects.get(row["object_id"], {})))
                        | related.get(row["key"], set())
                    )
                    & eligible
                )
            ]
        relations = {row["relation_id"]: row for row in service.workspace.relation_rows}
        for row in rows:
            payload = registered.get(row["object_id"], {}).get("payload", {})
            if row["kind"] == "entity":
                label = service.workspace.entities.get(row["key"], {}).get("label")
            elif row["kind"] == "relation" and row["key"] in relations:
                relation = relations[row["key"]]
                entities = service.workspace.entities
                label = (
                    entities[relation["subject_id"]]["label"]
                    + " → "
                    + relation["predicate"]
                    + " → "
                    + entities[relation["object_id"]]["label"]
                )
            else:
                label = payload.get("url") or payload.get("query")
            row["label"] = (label or row["key"])[:240]
        return dict(
            items=rows,
            split="exploratory",
            gold=False,
            ordering="registration_order; no scientific sampling claim",
        )

    @app.get("/api/validation/export")
    async def export(request: Request):
        r = role(request)
        data = studio.export()
        if r != "C":
            previous = "GENESIS"
            selected: list[dict] = []
            for source in data["events"]:
                if source["role"] not in {"SYSTEM", r}:
                    continue
                e = copy.deepcopy(source)
                e["source_audit_hash"] = e["hash"]
                e["previous"] = previous
                e["seq"] = len(selected) + 1
                e["hash"] = digest(
                    {
                        k: e[k]
                        for k in ("at", "role", "task", "stage", "kind", "previous", "payload")
                    }
                )
                previous = e["hash"]
                selected.append(e)
            data.update(events=selected, audit_head=previous, view_role=r)
        return data

    @app.post("/api/validation/import")
    async def import_reviews(request: Request):
        r = role(request)
        data = await request.json()
        validate_event_shapes(data)
        if r != "C" and any(e["role"] not in {"SYSTEM", r} for e in data.get("events", [])):
            raise HTTPException(403, "Cannot import another reviewer's events")
        return {"audit_head": studio.import_snapshot(data)}

    @app.get("/api/validation/original/{sha}")
    async def original(sha: str, request: Request):
        role(request)
        with index.conn() as c:
            record = c.execute(
                "SELECT raw_path,raw_bytes FROM sources WHERE sha256=?", (sha,)
            ).fetchone()
        if not record:
            raise HTTPException(404, "Unknown source")
        try:
            data, media, filename = read_original(record, sha, service.original_roots)
        except (ValueError, OSError, RetrievalError) as exc:
            raise HTTPException(404, "Original unavailable or integrity failed") from exc
        studio.verified_sources.add(sha)
        return Response(
            data,
            media_type=media,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                "X-Original-SHA256": sha,
            },
        )

    @app.get("/api/validation/status")
    async def review_status(request: Request, kind: str, key: str):
        r = role(request)
        oid = "review-object:" + digest({"kind": kind, "key": key, "snapshot": studio.snapshot})
        rows = studio.reviews(oid, r)
        return dict(
            object_id=oid,
            decision=rows[-1]["payload"]["decision"] if rows else "NOT_JUDGED",
            gold=False,
            split="exploratory",
        )

    @app.get("/api/validation/coverage")
    async def coverage(request: Request):
        role(request)
        with index.conn() as c:
            stats = {
                r["ags"]: dict(r)
                for r in c.execute(
                    "SELECT f.ags,count(*) sources,sum(s.unit_count>0) sources_with_text,"
                    "sum(s.unit_count) indexed_units FROM source_frames f "
                    "JOIN sources s USING(sha256) GROUP BY f.ags"
                )
            }
        available: dict[str, int] = {}
        verified: dict[str, int] = {}
        with index.conn() as c:
            for row in c.execute(
                "SELECT s.sha256,s.raw_path,f.ags FROM sources s JOIN source_frames f USING(sha256)"
            ):
                if Path(row["raw_path"]).is_file():
                    available[row["ags"]] = available.get(row["ags"], 0) + 1
                if row["sha256"] in studio.verified_sources:
                    verified[row["ags"]] = verified.get(row["ags"], 0) + 1
        human: dict[str, int] = {}
        graph_sources: dict[str, set[str]] = {}
        graph_claims: dict[str, int] = {}
        for relation in service.workspace.relation_rows:
            record = relation["record"]
            for ags in record.get("source_frame_ags", []):
                graph_sources.setdefault(ags, set()).add(record["source_sha256"])
            for ags in record.get("claim_scope_ags") or []:
                graph_claims[ags] = graph_claims.get(ags, 0) + 1
        for oid, obj in studio.objects().items():
            if obj["kind"] == "source":
                latest = studio.latest_reviews(oid)
                if any(
                    e["payload"]["human"] and e["payload"]["decision"] == "CONFIRM"
                    for e in latest.values()
                ):
                    for ags in obj["payload"]["municipality_ids"]:
                        human[ags] = human.get(ags, 0) + 1
        rows = [
            dict(
                ags=ags,
                name=m["name"],
                region=m.get("region"),
                municipality_present=True,
                sources=stats.get(ags, {}).get("sources", 0),
                archived_versions=stats.get(ags, {}).get("sources", 0),
                text_sources=stats.get(ags, {}).get("sources_with_text", 0),
                indexed_units=stats.get(ags, {}).get("indexed_units", 0),
                searchable=stats.get(ags, {}).get("indexed_units", 0) > 0,
                human_source_reviews=human.get(ags, 0),
                graph_source_versions=len(graph_sources.get(ags, set())),
                graph_claim_relations=graph_claims.get(ags, 0),
                archive_files_present=available.get(ags, 0),
                hash_verified_this_process=verified.get(ags, 0),
                source_discovery_status=m.get("crawl_status", "UNKNOWN"),
                external_rights="NOT_ASSESSED_LOCAL_ONLY",
                original_runtime_integrity="NOT_CHECKED_UNTIL_OPENED",
            )
            for ags, m in service.workspace.municipalities.items()
        ]
        with index.conn() as c:
            source_types = [
                dict(row)
                for row in c.execute(
                    "SELECT CASE WHEN kind='pdf_page' THEN 'pdf' "
                    "WHEN kind IN ('heading','paragraph','list_item','html') THEN 'html' "
                    "ELSE 'unknown' END document_type,count(*) source_versions "
                    "FROM sources GROUP BY document_type"
                )
            ]
        return dict(
            snapshot_id=studio.snapshot,
            municipalities=rows,
            index=index.path.name,
            method="normalized-term FTS5 BM25, global IDF",
            independent_gold=False,
            source_types=source_types,
            graph_projection=service.projection_id,
            graph_scope="source_frame_associations_and_provisional_claim_scope_are_distinct",
        )

    @app.get("/api/validation/units/{sha}")
    async def source_units(sha: str, request: Request, offset: int = 0):
        role(request)
        if not 0 <= offset <= 20000:
            raise HTTPException(422, "Invalid unit offset")
        with closing(
            source_connection(service.workspace.db, service.workspace.source_databases)
        ) as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT unit_id,ordinal,text,heading,page_ref,kind FROM units "
                    "WHERE sha256=? ORDER BY ordinal,unit_id LIMIT 10 OFFSET ?",
                    (sha, offset),
                )
            ]
        return dict(source_sha256=sha, units=rows, offset=offset, validation="NOT_JUDGED")

    # Separate individual-account protocol; exploratory reviews never become Human Gold.
    from crawler.web.review_protocol import attach_review_protocol

    attach_review_protocol(app, studio)
