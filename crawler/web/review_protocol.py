"""Independent local annotation protocol over the existing append-only audit contract.

This store is separate from exploratory feedback and every frozen study. Technical
workspaces can exercise the full lifecycle but can never emit Human Gold.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from research.bavaria.completion.metrics import kappa
from research.gold_studio.store import RESERVE, Store, canonical, digest

SCHEMA = "klima-independent-review-v1"
KINDS = {
    "source": "Originalquelle und Metadaten",
    "extraction": "Text- oder Tabellenextraktion",
    "municipality": "Gemeindezuordnung",
    "temporal": "Zeitliche Geltung",
    "entity": "Entität",
    "relation": "Graphrelation",
    "claim": "Einzelne Tatsachenbehauptung",
    "finding": "Abgeleiteter Befund",
    "answer_evidence": "Antwort-Evidenz-Zuordnung",
    "source_conflict": "Konflikt zwischen Quellen",
}
DECISIONS = {
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
    "WRONG_MUNICIPALITY",
    "WRONG_TEMPORAL_SCOPE",
    "EXTRACTION_ERROR",
    "AMBIGUOUS",
    "INSUFFICIENT_EVIDENCE",
    "NEEDS_FURTHER_REVIEW",
}
SPLITS = {
    "development",
    "regression",
    "human_annotation_candidate",
    "independent_holdout",
    "adversarial_negative",
}
SIGNALS = {
    "uncertainty",
    "research_relevance",
    "usage",
    "contradiction",
    "graph_centrality",
    "coverage_gap",
    "information_gain",
}
CLAIM_METRICS = {"supported", "citation_correct", "scope_correct", "temporal_valid", "hallucinated"}


def metric_judgments(form):
    values = form.get("metric_judgments", {})
    if (
        not isinstance(values, dict)
        or set(values) - CLAIM_METRICS
        or any(value is not None and type(value) is not bool for value in values.values())
    ):
        raise ValueError("Claim metrics require explicit booleans or unjudged null values")
    if (
        any(value is not None for value in values.values())
        and form.get("evidence_inspected") is not True
    ):
        raise ValueError("Claim metric judgments require actual source inspection")
    return {key: values.get(key) for key in sorted(CLAIM_METRICS)}


SESSION_SECONDS = 12 * 3600


def _bounded(value, limit=500000):
    if len(canonical(value).encode()) > limit:
        raise ValueError("Review object exceeds the bounded local review budget")
    return copy.deepcopy(value)


def _password(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,64}", value):
        raise ValueError("Account name: 3–64 letters, digits, dot, underscore or hyphen")
    return value


class ProtocolStore(Store):
    @contextmanager
    def conn(self):
        with closing(super().conn()) as connection:
            with connection:
                yield connection


class ReviewProtocol:
    """Bounded individual-account, double-blind review and adjudication state machine."""

    def __init__(self, directory, snapshot, *, technical=False, source_resolver=None):
        self.directory = Path(directory).resolve()
        self.snapshot, self.technical = snapshot, technical
        self.source_resolver = source_resolver
        self.on_finalized = None
        self.store = ProtocolStore(
            self.directory / "protocol.sqlite",
            {"selection": {"snapshot": snapshot, "schema": SCHEMA}, "tasks": [], "sources": []},
            simulation=technical,
        )
        with self.store.conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS protocol_accounts(
                  username TEXT PRIMARY KEY,identity TEXT UNIQUE NOT NULL,role TEXT NOT NULL,
                  salt TEXT NOT NULL,password_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
                  failures INTEGER NOT NULL DEFAULT 0,locked_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS protocol_sessions(
                  token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL);
            """)
            if not c.execute("SELECT 1 FROM protocol_accounts LIMIT 1").fetchone():
                password, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
                c.execute(
                    "INSERT INTO protocol_accounts(username,identity,role,salt,password_hash) "
                    "VALUES(?,?,?,?,?)",
                    ("owner", "local-operator", "admin", salt, _password(password, salt)),
                )
                self.store.event(
                    c,
                    "SYSTEM",
                    "owner",
                    "accounts",
                    "account_created",
                    {"username": "owner", "role": "admin"},
                )
                path = self.directory / "owner-credentials.txt"
                path.write_text(
                    "Local annotation owner account\nusername: owner\npassword: "
                    + password
                    + "\nKeep private. No external access authorized.\n"
                )
                path.chmod(0o600)

    def _write_budget(self):
        if shutil.disk_usage(self.directory).free < RESERVE + 2 * 1024**2:
            raise ValueError("System reserve reached; review writes paused")

    def account(self, username):
        with self.store.conn() as c:
            row = c.execute(
                "SELECT username,role,active FROM protocol_accounts WHERE username=?", (username,)
            ).fetchone()
        if not row or not row["active"]:
            raise PermissionError("Active individual account required")
        return dict(row)

    def require(self, actor, *roles):
        account = self.account(actor)
        if account["role"] not in roles:
            raise PermissionError("This operation requires a different review role")
        return account

    def create_account(self, actor, username, password, *, identity, role="reviewer"):
        self.require(actor, "admin")
        _name(username)
        if role not in {"reviewer", "adjudicator", "admin"}:
            raise ValueError("Unknown account role")
        if not isinstance(identity, str) or not 3 <= len(identity.strip()) <= 160:
            raise ValueError("Distinct reviewer identity required")
        if not isinstance(password, str) or not 12 <= len(password) <= 200:
            raise ValueError("A password of 12–200 characters is required")
        salt = secrets.token_hex(16)
        self._write_budget()
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "INSERT INTO protocol_accounts(username,identity,role,salt,password_hash) "
                    "VALUES(?,?,?,?,?)",
                    (username, identity.strip().casefold(), role, salt, _password(password, salt)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Account or reviewer identity already exists") from exc
            self.store.event(
                c,
                actor,
                username,
                "accounts",
                "account_created",
                {"username": username, "role": role},
            )
        return {"username": username, "role": role, "identity_assurance": "OPERATOR_DECLARED"}

    def accounts(self, actor):
        self.require(actor, "admin")
        with self.store.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT username,role,active FROM protocol_accounts ORDER BY username"
                )
            ]

    def update_account(self, actor, username, *, active=None, password=None):
        self.require(actor, "admin")
        with self.store.conn() as c:
            if not c.execute(
                "SELECT 1 FROM protocol_accounts WHERE username=?", (username,)
            ).fetchone():
                raise ValueError("Unknown account")
        if username == actor and active is False:
            raise ValueError("Do not deactivate the active owner account")
        if active is not None and not isinstance(active, bool):
            raise ValueError("Explicit account active state required")
        if password is not None and (
            not isinstance(password, str) or not 12 <= len(password) <= 200
        ):
            raise ValueError("A password of 12–200 characters is required")
        self._write_budget()
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if active is not None:
                c.execute(
                    "UPDATE protocol_accounts SET active=? WHERE username=?",
                    (int(active), username),
                )
            if password is not None:
                salt = secrets.token_hex(16)
                c.execute(
                    "UPDATE protocol_accounts SET salt=?,password_hash=?,failures=0,locked_until=0 "
                    "WHERE username=?",
                    (salt, _password(password, salt), username),
                )
            c.execute("DELETE FROM protocol_sessions WHERE username=?", (username,))
            self.store.event(
                c,
                actor,
                username,
                "accounts",
                "account_updated",
                {"username": username, "active": active, "password_reset": password is not None},
            )
        return {"username": username, "sessions_revoked": True}

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 200:
            raise PermissionError("Invalid credentials")
        self._write_budget()
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM protocol_accounts WHERE username=?", (username,)
            ).fetchone()
            # Constant expensive work for unknown names prevents a cheap account oracle.
            salt = row["salt"] if row else "0" * 32
            valid = hmac.compare_digest(
                _password(password, salt), row["password_hash"] if row else ""
            )
            if not row or not row["active"] or row["locked_until"] > time.time() or not valid:
                if row:
                    c.execute(
                        "UPDATE protocol_accounts SET failures=failures+1,locked_until=? "
                        "WHERE username=?",
                        (time.time() + 60 if row["failures"] >= 4 else 0, username),
                    )
                # Commit the failed-attempt counter before raising.
                c.commit()
                raise PermissionError("Invalid credentials or temporary login lock")
            c.execute(
                "UPDATE protocol_accounts SET failures=0,locked_until=0 WHERE username=?",
                (username,),
            )
            c.execute("DELETE FROM protocol_sessions WHERE expires<?", (time.time(),))
            token = secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO protocol_sessions VALUES(?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    username,
                    time.time() + SESSION_SECONDS,
                ),
            )
        return token

    def session(self, token):
        if not token:
            raise PermissionError("Individual reviewer login required")
        with self.store.conn() as c:
            row = c.execute(
                "SELECT username FROM protocol_sessions WHERE token_hash=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        if not row:
            raise PermissionError("Reviewer session expired")
        return self.account(row["username"])

    def logout(self, token):
        self._write_budget()
        with self.store.conn() as c:
            c.execute(
                "DELETE FROM protocol_sessions WHERE token_hash=?",
                (hashlib.sha256((token or "").encode()).hexdigest(),),
            )

    def _events(self, kind=None, oid=None):
        return [e for e in self.store.events(kind=kind) if oid is None or e["task"] == oid]

    def _event(self, actor, oid, kind, payload):
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            single = {"protocol_assignment", "protocol_object", "protocol_adjudication"}
            if kind in single | {"protocol_review"}:
                sql = "SELECT 1 FROM events WHERE task=? AND kind=?"
                args = [oid, kind]
                if kind == "protocol_review":
                    sql += " AND role=?"
                    args.append(actor)
                if c.execute(sql, args).fetchone():
                    raise ValueError("Immutable event already exists; refresh this object")
            self.store.event(c, actor, oid, "independent_review", kind, _bounded(payload))
        return self.store.audit()

    def objects(self):
        return {e["task"]: e["payload"] for e in self._events("protocol_object")}

    def register(
        self,
        actor,
        *,
        kind,
        key,
        payload,
        sources,
        split="human_annotation_candidate",
        version=1,
        signals=None,
        task_id=None,
        _prepare_only=False,
    ):
        self.require(actor, "admin")
        if kind not in KINDS or split not in SPLITS or not isinstance(version, int) or version < 1:
            raise ValueError("Known review kind, split and positive version required")
        if not isinstance(key, str) or not key.strip() or len(key) > 300:
            raise ValueError("Stable review key required")
        if (
            not isinstance(payload, dict)
            or not isinstance(sources, list)
            or not 1 <= len(sources) <= 30
        ):
            raise ValueError("Structured object and 1–30 explicit source anchors required")
        if task_id is not None and (not isinstance(task_id, str) or len(task_id) > 200):
            raise ValueError("Bounded evaluation task identifier required")
        validated = []
        for source in sources:
            if not isinstance(source, dict) or not re.fullmatch(
                r"[0-9a-f]{64}", source.get("sha256", "")
            ):
                raise ValueError("Each anchor requires its source SHA256")
            if not self.source_resolver:
                raise ValueError(
                    "Bound source resolver required; unverified anchors cannot be imported"
                )
            if kind != "source" and not (source.get("unit_id") or source.get("quote")):
                raise ValueError("A passage anchor is required for non-source review objects")
            validated.append(self.source_resolver(copy.deepcopy(source)))
        if kind == "claim" and ("cited_source_ids" in payload or "context_source_ids" in payload):
            from research.gold_studio.source_bindings import claim_source_partition

            claim_source_partition(payload, validated)
        values = {s: 0.0 for s in SIGNALS}
        if signals:
            if not isinstance(signals, dict) or set(signals) - SIGNALS:
                raise ValueError("Unknown priority signal")
            for k, v in signals.items():
                if (
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                ):
                    raise ValueError("Priority signals must be finite numbers in [0,1]")
                values[k] = float(v)
        obj = _bounded(
            dict(
                kind=kind,
                key=key,
                payload=payload,
                sources=validated,
                split=split,
                version=version,
                snapshot_id=self.snapshot,
                signals=values,
                task_id=task_id,
                status="NOT_JUDGED",
                human_gold=False,
            )
        )
        oid = "annotation:" + digest(obj)
        for old in self.objects().values():
            if (old["key"], old["kind"]) == (key, kind) and (
                old["split"] != split or old["task_id"] != task_id
            ):
                raise ValueError(
                    "Review split and evaluation task binding cannot change across versions"
                )
            if (old["key"], old["kind"], old["version"]) == (key, kind, version) and old != obj:
                raise ValueError(
                    "Object version is immutable; increment version for changed material"
                )
        if _prepare_only:
            return oid, obj
        self._commit_objects(actor, [(oid, obj)])
        return oid

    def _commit_objects(self, actor, prepared):
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            known = {
                r["task"]: json.loads(r["payload"])
                for r in c.execute("SELECT task,payload FROM events WHERE kind='protocol_object'")
            }
            versions = {(o["kind"], o["key"], o["version"]): oid for oid, o in known.items()}
            for oid, obj in prepared:
                binding = (obj["kind"], obj["key"], obj["version"])
                if any(
                    (old["key"], old["kind"]) == (obj["key"], obj["kind"])
                    and (old["split"] != obj["split"] or old["task_id"] != obj["task_id"])
                    for old in known.values()
                ):
                    raise ValueError("Review split and task binding cannot change across versions")
                if binding in versions and versions[binding] != oid:
                    raise ValueError(
                        "Object version is immutable; increment version for changed material"
                    )
                if oid not in known:
                    self.store.event(c, actor, oid, "independent_review", "protocol_object", obj)
                    known[oid] = obj
                    versions[binding] = oid

    def import_candidates(self, actor, rows):
        self.require(actor, "admin")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ValueError("Import 1–200 source-bound objects at a time")
        prepared = []
        for row in rows:
            if not isinstance(row, dict) or "_prepare_only" in row:
                raise ValueError("Structured candidate material required")
            prepared.append(self.register(actor, **row, _prepare_only=True))
        self._commit_objects(actor, prepared)
        return [oid for oid, _ in prepared]

    def assignment(self, oid):
        events = self._events("protocol_assignment", oid)
        return events[-1]["payload"] if events else None

    def assign(self, actor, oid, reviewers, adjudicator):
        self.require(actor, "admin")
        if oid not in self.objects():
            raise ValueError("Unknown annotation object")
        if not isinstance(reviewers, list) or len(reviewers) != 2 or len(set(reviewers)) != 2:
            raise ValueError("Two distinct individual reviewers required")
        for name in reviewers:
            self.require(name, "reviewer")
        self.require(adjudicator, "adjudicator")
        if self.assignment(oid):
            raise ValueError(
                "Assignment already exists; new material requires a new object version"
            )
        self._event(
            actor, oid, "protocol_assignment", dict(reviewers=reviewers, adjudicator=adjudicator)
        )
        return self.state(oid)

    def _latest(self, kind, oid):
        result = {}
        for e in self._events(kind, oid):
            result[e["role"]] = e
        return result

    def state(self, oid):
        assignment = self.assignment(oid)
        if not assignment:
            return "UNASSIGNED"
        locks = self._latest("protocol_review", oid)
        if not all(name in locks for name in assignment["reviewers"]):
            return "AWAITING_INDEPENDENT_REVIEWS"
        reviews = [locks[name]["payload"] for name in assignment["reviewers"]]
        if not all(r["independent"] for r in reviews):
            return "INDEPENDENCE_NOT_ESTABLISHED"

        def comparable(r):
            return r["decision"], canonical(r["corrections"]), canonical(metric_judgments(r))

        if comparable(reviews[0]) == comparable(reviews[1]):
            return "CONSENSUS"
        return "ADJUDICATED" if self._events("protocol_adjudication", oid) else "CONFLICT"

    def _access(self, actor, oid):
        account = self.account(actor)
        obj = self.objects().get(oid)
        if obj is None:
            raise ValueError("Unknown annotation object")
        assignment = self.assignment(oid)
        if account["role"] != "admin" and (
            not assignment or actor not in [*assignment["reviewers"], assignment["adjudicator"]]
        ):
            raise PermissionError("Object is not assigned to this account")
        return account, obj, assignment

    def view(self, actor, oid):
        account, obj, assignment = self._access(actor, oid)
        reviews = self._latest("protocol_review", oid)
        history = self._events(oid=oid)
        if account["role"] == "reviewer":
            # No partner names, judgments, conflict state or event hashes/chain side channel.
            history = [
                {"kind": e["kind"], "at": e["at"], "payload": e["payload"]}
                for e in history
                if e["role"] == actor and e["kind"] in {"protocol_draft", "protocol_review"}
            ]
            status = "SUBMITTED" if actor in reviews else "PENDING"
            shown_assignment = {"own_assignment": True, "blind": True}
        else:
            status = self.state(oid)
            shown_assignment = assignment
            if account["role"] == "adjudicator" and status == "AWAITING_INDEPENDENT_REVIEWS":
                history = [
                    e for e in history if e["kind"] not in {"protocol_review", "protocol_draft"}
                ]
        consolidated = self.state(oid) in {"CONSENSUS", "ADJUDICATED"}
        original_confirmed = False
        if consolidated and assignment:
            original_confirmed = (
                self._events("protocol_adjudication", oid)[-1]["payload"].get(
                    "evidence_inspected", False
                )
                if self.state(oid) == "ADJUDICATED"
                else all(
                    reviews[name]["payload"].get("evidence_inspected", False)
                    for name in assignment["reviewers"]
                )
            )
        return dict(
            object_id=oid,
            **obj,
            review_state=status,
            assignment=shown_assignment,
            history=history,
            technical=self.technical,
            evidence_status=(
                "TECHNICAL_FIXTURE"
                if self.technical
                else "INDEPENDENTLY_HUMAN_REVIEWED"
                if consolidated and original_confirmed
                else "HUMAN_REVIEW_DEFERRED"
                if consolidated
                else "NOT_INDEPENDENTLY_VALIDATED"
            ),
            decisions=sorted(DECISIONS),
        )

    def queue(self, actor, *, kind="", status="", q="", limit=100, offset=0):
        account = self.account(actor)
        if kind and kind not in KINDS:
            raise ValueError("Unknown review kind")
        if not 1 <= limit <= 200 or not 0 <= offset <= 100000 or len(q) > 200:
            raise ValueError("Bounded queue window required")
        # Reviewer queues expose only assignment membership and the actor's own submission.
        # Read each event projection once; do not rebuild all object/history views per row.
        assignments = {}
        submitted = set()
        if account["role"] == "reviewer":
            assignments = {e["task"]: e["payload"] for e in self._events("protocol_assignment")}
            submitted = {e["task"] for e in self._events("protocol_review") if e["role"] == actor}
        rows = []
        for oid, obj in self.objects().items():
            if account["role"] == "reviewer":
                if actor not in assignments.get(oid, {}).get("reviewers", []):
                    continue
                own_state = "SUBMITTED" if oid in submitted else "PENDING"
            else:
                try:
                    own_state = self.view(actor, oid)["review_state"]
                except PermissionError:
                    continue
            if (kind and obj["kind"] != kind) or (status and own_state != status):
                continue
            if q and q.casefold() not in canonical(obj).casefold():
                continue
            # Heuristic preparation only, never scientific information gain or confidence.
            priority = sum(obj["signals"].values()) / len(SIGNALS)
            rows.append(
                dict(
                    object_id=oid,
                    kind=obj["kind"],
                    key=obj["key"],
                    label=obj["payload"].get("label", obj["key"]),
                    state=own_state,
                    split=obj["split"],
                    priority=priority,
                    priority_basis=obj["signals"],
                    version=obj["version"],
                )
            )
        rows.sort(key=lambda r: (-r["priority"], r["object_id"]))
        return dict(
            items=rows[offset : offset + limit],
            total=len(rows),
            offset=offset,
            progress=dict(
                submitted=sum(
                    r["state"] in {"SUBMITTED", "CONSENSUS", "ADJUDICATED"} for r in rows
                ),
                total=len(rows),
            ),
            role=account["role"],
            priority_semantics="heuristic_operator_signals_not_measured_information_gain",
        )

    def save(self, actor, oid, form, *, draft=False):
        account, obj, assignment = self._access(actor, oid)
        if account["role"] != "reviewer" or actor not in assignment["reviewers"]:
            raise PermissionError("Only an assigned independent reviewer may submit")
        if actor in self._latest("protocol_review", oid):
            raise ValueError("Submitted first judgments are immutable; create a new object version")
        if not isinstance(form, dict):
            raise ValueError("Structured review required")
        _bounded(form, 30000)
        if draft:
            self._event(actor, oid, "protocol_draft", form)
            return {"state": "DRAFT", "human_gold": False}
        decision = form.get("decision")
        if decision not in DECISIONS:
            raise ValueError("Explicit review decision required")
        if not isinstance(form.get("comment", ""), str) or len(form.get("comment", "")) > 8000:
            raise ValueError("Bounded review rationale required")
        if decision != "SUPPORTED" and not form.get("comment", "").strip():
            raise ValueError("Explain partial, negative, ambiguous or deferred decisions")
        if not isinstance(form.get("corrections", {}), dict):
            raise ValueError("Corrections must be structured values")
        if not self.technical and form.get("human_confirmed") is not True:
            raise ValueError("Actual personal source inspection must be confirmed")
        independent = form.get("independence_confirmed") is True
        payload = dict(
            decision=decision,
            comment=form.get("comment", ""),
            corrections=form.get("corrections", {}),
            independent=independent,
            human=not self.technical,
            human_gold=False,
            object_version=obj["version"],
            evidence_inspected=form.get("evidence_inspected") is True,
            metric_judgments=metric_judgments(form),
        )
        deferred = decision in {"INSUFFICIENT_EVIDENCE", "NEEDS_FURTHER_REVIEW"}
        if not payload["evidence_inspected"] and not deferred:
            raise ValueError("Explicit confirmation of source inspection required")
        self._event(actor, oid, "protocol_review", payload)
        result = {"state": "SUBMITTED", "human_gold": False}
        feedback = self._notify_finalized(oid)
        if feedback is not None:
            result["review_feedback"] = feedback
        return result

    def adjudicate(self, actor, oid, form):
        account, _, assignment = self._access(actor, oid)
        if account["role"] != "adjudicator" or actor != assignment["adjudicator"]:
            raise PermissionError("Only the assigned distinct adjudicator may resolve a conflict")
        if self.state(oid) != "CONFLICT":
            raise ValueError("Two independent conflicting judgments required")
        if not isinstance(form, dict) or form.get("decision") not in DECISIONS:
            raise ValueError("Explicit adjudication decision required")
        if not isinstance(form.get("comment"), str) or not form["comment"].strip():
            raise ValueError("Adjudication rationale required")
        if not isinstance(form.get("corrections", {}), dict):
            raise ValueError("Structured adjudication corrections required")
        if not self.technical and form.get("human_confirmed") is not True:
            raise ValueError("Personal adjudication confirmation required")
        inspected = form.get("evidence_inspected") is True
        if not inspected and form["decision"] not in {
            "INSUFFICIENT_EVIDENCE",
            "NEEDS_FURTHER_REVIEW",
        }:
            raise ValueError("Explicit confirmation of source inspection required for adjudication")
        self._event(
            actor,
            oid,
            "protocol_adjudication",
            dict(
                decision=form["decision"],
                comment=form["comment"],
                corrections=form.get("corrections", {}),
                human=not self.technical,
                evidence_inspected=inspected,
                metric_judgments=metric_judgments(form),
            ),
        )
        result = {"state": self.state(oid)}
        feedback = self._notify_finalized(oid)
        if feedback is not None:
            result["review_feedback"] = feedback
        return result

    def _notify_finalized(self, oid):
        if self.on_finalized is None or self.state(oid) not in {"CONSENSUS", "ADJUDICATED"}:
            return None
        try:
            result = self.on_finalized(oid)
            if (
                isinstance(result, dict)
                and result.get("status") == "SEALED_HOLDOUT_NO_DEVELOPMENT_SYNC"
            ):
                return "HELD_FOR_INDEPENDENT_EVALUATION"
        except Exception as exc:
            # The independently submitted judgment is already durable. Derived
            # feedback can be retried; never pretend it succeeded or erase a review.
            self._event(
                "system:review-feedback",
                oid,
                "protocol_feedback_failed",
                {"error_class": type(exc).__name__, "review_preserved": True},
            )
            return "DEFERRED"
        return "SYNCED"

    def agreement(self, actor):
        self.require(actor, "admin", "adjudicator")
        pairs, by_kind = [], {}
        for oid, obj in self.objects().items():
            assignment = self.assignment(oid)
            reviews = self._latest("protocol_review", oid)
            if not assignment or not all(n in reviews for n in assignment["reviewers"]):
                continue
            values = [reviews[n]["payload"] for n in assignment["reviewers"]]
            if not all(v["independent"] for v in values):
                continue
            pair = tuple(v["decision"] for v in values)
            pairs.append(pair)
            by_kind.setdefault(obj["kind"], []).append(pair)
        return dict(
            technical=self.technical,
            label_origin="synthetic_fixture"
            if self.technical
            else "independent_human_self_declared",
            overall=kappa(pairs),
            by_kind={kind: kappa(pairs) for kind, pairs in by_kind.items()},
            limits=(
                "Cohen kappa on paired categorical decisions; "
                "identity and independence operator/self-declared"
            ),
        )

    def export_gold(self, actor, *, purpose="evaluation"):
        self.require(actor, "admin")
        return self._publish_gold(actor, purpose=purpose)

    def _publish_gold(self, actor, *, purpose):
        """Trusted materialization; only the public wrapper grants export access."""
        if purpose not in {"evaluation", "development"}:
            raise ValueError("Explicit evaluation or development purpose required")
        labels = []
        objects = self.objects()
        latest_versions = {}
        for obj in objects.values():
            pair = (obj["kind"], obj["key"])
            latest_versions[pair] = max(obj["version"], latest_versions.get(pair, 0))
        for oid, obj in objects.items():
            if obj["version"] != latest_versions[(obj["kind"], obj["key"])]:
                continue
            if obj["split"] == "independent_holdout" and purpose != "evaluation":
                continue
            state = self.state(oid)
            if state not in {"CONSENSUS", "ADJUDICATED"}:
                continue
            assignment = self.assignment(oid)
            reviews = self._latest("protocol_review", oid)
            final = (
                self._events("protocol_adjudication", oid)[-1]
                if state == "ADJUDICATED"
                else reviews[assignment["reviewers"][0]]
            )
            labels.append(
                dict(
                    object_id=oid,
                    kind=obj["kind"],
                    key=obj["key"],
                    payload=obj["payload"],
                    version=obj["version"],
                    task_id=obj["task_id"],
                    split=obj["split"],
                    sources=obj["sources"],
                    decision=final["payload"]["decision"],
                    corrections=final["payload"]["corrections"],
                    metric_judgments=metric_judgments(final["payload"]),
                    final_event_hash=final["hash"],
                    review_hashes=[reviews[n]["hash"] for n in assignment["reviewers"]],
                    original_inspection_confirmed=(
                        final["payload"].get("evidence_inspected", False)
                        if state == "ADJUDICATED"
                        else all(
                            reviews[n]["payload"].get("evidence_inspected", False)
                            for n in assignment["reviewers"]
                        )
                    ),
                    human_gold=not self.technical,
                    label_origin="synthetic_fixture" if self.technical else "independent_human",
                )
            )
        # Never call insufficient/deferred decisions a positive relevance label.
        pending = {"AMBIGUOUS", "INSUFFICIENT_EVIDENCE", "NEEDS_FURTHER_REVIEW"}
        for label in labels:
            label["relevance"] = (
                {"SUPPORTED": 2, "PARTIALLY_SUPPORTED": 1}.get(
                    label["decision"], None if label["decision"] in pending else 0
                )
                if label["kind"] == "answer_evidence"
                else None
            )
        content = dict(
            schema=SCHEMA,
            snapshot_id=self.snapshot,
            purpose=purpose,
            technical=self.technical,
            human_gold=bool(labels) and not self.technical,
            labels=labels,
            label_count=len(labels),
            evaluation_binding="task_id + object_id + immutable source anchors",
            unresolved_relevance=sum(
                x["kind"] == "answer_evidence" and x["relevance"] is None for x in labels
            ),
            non_relevance_objects=sum(x["kind"] != "answer_evidence" for x in labels),
        )
        with self.store.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            version = (
                c.execute(
                    "SELECT count(*) FROM events WHERE kind='protocol_gold_version'"
                ).fetchone()[0]
                + 1
            )
            latest = c.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            content.update(
                gold_version=version, source_audit_head=latest[0] if latest else "GENESIS"
            )
            content["content_sha256"] = digest(content)
            self.store.event(
                c,
                actor,
                "gold:" + str(version),
                "independent_review",
                "protocol_gold_version",
                content,
            )
        return content

    def adjudication_draft(self, actor, oid, form):
        account, _, assignment = self._access(actor, oid)
        if account["role"] != "adjudicator" or actor != assignment["adjudicator"]:
            raise PermissionError("Only the assigned adjudicator may save this draft")
        if self.state(oid) != "CONFLICT":
            raise ValueError("A current independent conflict is required")
        if not isinstance(form, dict):
            raise ValueError("Structured adjudication draft required")
        self._event(actor, oid, "protocol_adjudication_draft", _bounded(form, 30000))
        return {"state": "DRAFT", "human_gold": False}

    def audit_export(self, actor):
        self.require(actor, "admin")
        return dict(
            schema=SCHEMA,
            snapshot_id=self.snapshot,
            technical=self.technical,
            audit_head=self.store.audit(),
            events=self.store.events(),
            credentials_included=False,
        )

    def diagnostics(self, actor):
        account = self.account(actor)
        own = self.queue(actor, limit=1)
        return dict(
            schema=SCHEMA,
            snapshot_id=self.snapshot,
            account=account,
            technical=self.technical,
            kinds=KINDS,
            decisions=sorted(DECISIONS),
            splits=sorted(SPLITS),
            signals=sorted(SIGNALS),
            queue_total=own["total"],
            identity_assurance="LOCAL_OPERATOR_DECLARED_INDIVIDUAL_ACCOUNTS",
            external_access="BLOCKED_UNTIL_SEPARATE_RIGHTS_AND_PROVIDER_APPROVAL",
            automatic_feedback=self.on_finalized is not None,
        )


def attach_review_protocol(app, studio):
    """Compose formal annotation without changing legacy exploratory semantics."""
    from research.bavaria.federation import source_connection

    def resolve(source):
        sha, unit_id, quote = source["sha256"], source.get("unit_id"), source.get("quote", "")
        with studio.index.conn() as index_connection:
            known = index_connection.execute(
                "SELECT 1 FROM sources WHERE sha256=?", (sha,)
            ).fetchone()
        if not known:
            raise ValueError("Source is outside the bound immutable snapshot")
        if not isinstance(quote, str) or len(quote) > 12000:
            raise ValueError("Bounded verbatim source quote required")
        result = dict(
            sha256=sha,
            original_url="/api/review-protocol/original/" + sha,
            source_status="NOT_INDEPENDENTLY_VALIDATED",
        )
        with closing(
            source_connection(
                studio.service.workspace.db, studio.service.workspace.source_databases
            )
        ) as c:
            if unit_id:
                row = c.execute(
                    "SELECT unit_id,text,heading,page_ref,ordinal FROM units "
                    "WHERE sha256=? AND unit_id=?",
                    (sha, unit_id),
                ).fetchone()
                if not row or (quote and quote not in row["text"]):
                    raise ValueError(
                        "Anchor is not a verbatim passage of the specified source unit"
                    )
                result.update(dict(row))
                result["quote"] = quote or row["text"]
            elif quote:
                row = c.execute(
                    "SELECT unit_id,text,heading,page_ref,ordinal FROM units "
                    "WHERE sha256=? AND instr(text,?)>0 ORDER BY ordinal LIMIT 1",
                    (sha, quote),
                ).fetchone()
                if not row:
                    raise ValueError("Quote is absent from the immutable source")
                result.update(dict(row))
                result["quote"] = quote
            else:
                result["quote"] = ""
        return result

    protocol = ReviewProtocol(
        studio.directory / "independent",
        studio.snapshot,
        technical=studio.technical,
        source_resolver=resolve,
    )
    app.state.review_protocol = protocol

    def actor(request):
        try:
            return protocol.session(request.cookies.get("review_protocol_session"))["username"]
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)[:250]) from exc

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Structured JSON required") from exc
        if not isinstance(value, dict):
            raise HTTPException(422, "JSON object required")
        return value

    @app.get("/review")
    async def review_page():
        return FileResponse(Path(__file__).with_name("static") / "review_protocol.html")

    @app.post("/api/review-protocol/session")
    async def login(request: Request):
        value = await body(request)
        try:
            token = protocol.login(value.get("username"), value.get("password"))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        response = JSONResponse(protocol.diagnostics(protocol.session(token)["username"]))
        response.set_cookie(
            "review_protocol_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=SESSION_SECONDS,
        )
        return response

    @app.delete("/api/review-protocol/session")
    async def logout(request: Request):
        protocol.logout(request.cookies.get("review_protocol_session"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("review_protocol_session")
        return response

    @app.get("/api/review-protocol/status")
    async def status(request: Request):
        return call(protocol.diagnostics, actor(request))

    @app.get("/api/review-protocol/accounts")
    async def accounts(request: Request):
        return {"items": call(protocol.accounts, actor(request))}

    @app.post("/api/review-protocol/accounts")
    async def account_create(request: Request):
        return call(protocol.create_account, actor(request), **await body(request))

    @app.post("/api/review-protocol/account-update")
    async def account_update(request: Request):
        value = await body(request)
        return call(protocol.update_account, actor(request), **value)

    @app.get("/api/review-protocol/sources")
    async def source_catalog(request: Request, q: str = "", offset: int = 0):
        call(protocol.require, actor(request), "admin")
        if len(q) > 200 or not 0 <= offset <= 100000:
            raise HTTPException(422, "Bounded source query required")
        with studio.index.conn() as c:
            params = ["%" + q + "%"] * 2
            where = " WHERE url LIKE ? OR sha256 LIKE ?" if q else ""
            params = params if q else []
            total = c.execute("SELECT count(*) FROM sources" + where, params).fetchone()[0]
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT sha256,url,kind,status,unit_count FROM sources"
                    + where
                    + " ORDER BY sha256 LIMIT 30 OFFSET ?",
                    [*params, offset],
                )
            ]
        return {"items": rows, "total": total, "offset": offset}

    @app.get("/api/review-protocol/units/{sha}")
    async def source_units(sha: str, request: Request, offset: int = 0):
        call(protocol.require, actor(request), "admin")
        if not 0 <= offset <= 100000:
            raise HTTPException(422, "Bounded unit window required")
        with closing(
            source_connection(
                studio.service.workspace.db, studio.service.workspace.source_databases
            )
        ) as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT unit_id,ordinal,text,heading,page_ref,kind FROM units WHERE sha256=? "
                    "ORDER BY ordinal,unit_id LIMIT 20 OFFSET ?",
                    (sha, offset),
                )
            ]
        return {"source_sha256": sha, "items": rows, "offset": offset}

    @app.get("/api/review-protocol/queue")
    async def queue(
        request: Request,
        kind: str = "",
        status: str = "",
        q: str = "",
        limit: int = 100,
        offset: int = 0,
    ):
        return call(
            protocol.queue,
            actor(request),
            kind=kind,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )

    @app.post("/api/review-protocol/handoff")
    async def research_handoff(request: Request):
        from crawler.core.answer_generation import AnswerResult

        who = actor(request)
        call(protocol.require, who, "admin")
        value = await body(request)
        if value.get("kind") not in {"claim", "answer_evidence", "source_conflict"}:
            raise HTTPException(422, "Claim, answer-evidence or source-conflict review required")
        result_id = value.get("result_id")
        if not isinstance(result_id, str):
            raise HTTPException(422, "Stored research result required")
        stored = studio.service.store.get(result_id)
        if stored is None:
            raise HTTPException(404, "Result expired; repeat the research question")
        canonical_result = stored.canonical
        retrieval = (
            canonical_result.retrieval_result
            if isinstance(canonical_result, AnswerResult)
            else canonical_result
        )
        if retrieval.database_snapshot_id != studio.snapshot:
            raise HTTPException(422, "Result belongs to another snapshot")
        selected = list(retrieval.items)
        payload = {
            "question": retrieval.query,
            "research_result_id": result_id,
            "preparation": "MACHINE_PREPARED_NOT_HUMAN_VALIDATED",
        }
        if isinstance(canonical_result, AnswerResult):
            payload["answer_status"] = canonical_result.status.value
            blocks = [
                part
                for part in re.split(r"\n\s*\n", canonical_result.answer_text)
                if re.search(r"\[C[0-9]+\]", part)
            ]
            index = value.get("claim_index", 0 if value["kind"] == "claim" else None)
            if index is not None and (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(blocks)
            ):
                raise HTTPException(
                    422, "A citation-bearing claim from this actual answer is required"
                )
            proposed = blocks[index] if index is not None else canonical_result.answer_text
            labels = set(re.findall(r"\[(C[0-9]+)\]", proposed))
            cited_ids = {
                citation.evidence_id
                for citation in canonical_result.citations
                if citation.label in labels
            }
            cited = [item for item in selected if item.evidence_id in cited_ids]
            required = {
                source_id
                for item in cited
                for assertion in item.assertions
                for source_id in assertion.required_source_ids
            }
            if not required <= {item.source_id for item in selected}:
                raise HTTPException(422, "Required graph context is missing from stored retrieval")
            selected = [
                item
                for item in selected
                if item.evidence_id in cited_ids or item.source_id in required
            ]
            cited_source_ids = {item.source_id for item in cited}
            payload.update(
                cited_source_ids=sorted(cited_source_ids),
                context_source_ids=sorted({item.source_id for item in selected} - cited_source_ids),
            )
            if value["kind"] == "claim":
                from crawler.web.evidence_synthesis import claim_id_for

                claim_text = re.sub(r"\s*\[C[0-9]+\]", "", proposed).strip().rstrip(".")
                payload["claim_id"] = claim_id_for(claim_text, cited_ids)
            payload["output_sha256"] = hashlib.sha256(
                canonical_result.answer_text.encode()
            ).hexdigest()
            payload.update(
                proposed_text=proposed,
                claim_index=index,
                citation_labels=sorted(labels),
                label=proposed[:180],
            )
        elif value["kind"] == "claim":
            raise HTTPException(
                422, "A claim requires an actual stored answer, not a retrieval result"
            )
        else:
            payload.update(
                label=retrieval.query,
                proposed_text="Prüfung der Evidenzzuordnung zur Forschungsfrage",
            )
        if not selected:
            raise HTTPException(422, "No source-bound evidence is available for this review")
        sources = []
        for item in selected:
            sha = item.document.document_id.removeprefix("source-version:")
            source = {"sha256": sha, "unit_id": item.source_id}
            # Verify source IDs against the actual source units. Never use a
            # model's claim text as if it were a verbatim source passage.
            call(resolve, source)
            if source not in sources:
                sources.append(source)
        if value["kind"] == "source_conflict" and len(sources) < 2:
            raise HTTPException(422, "A conflict candidate requires at least two source passages")
        feedback = getattr(app.state, "review_feedback", None)
        research = getattr(app.state, "research_product", None)
        if value["kind"] == "claim" and feedback is not None and research is not None:
            # Research history is session-owned. Never accept a browser-provided
            # replacement answer, namespace token or synthetic metric judgment.
            saved = research.history.get(
                request.cookies.get("klima_research_session", ""), result_id
            )
            if saved is not None:
                payload["review_observation_id"] = call(feedback.observations.capture, saved)
        oid = call(
            protocol.register,
            who,
            kind=value["kind"],
            key="research:" + digest({"payload": payload, "sources": sources}),
            payload=payload,
            sources=sources,
            signals={
                "uncertainty": 1,
                "research_relevance": 1,
                "contradiction": 1 if value["kind"] == "source_conflict" else 0,
            },
        )
        return {"object_id": oid, "human_gold": False, "review_state": "UNASSIGNED"}

    @app.post("/api/review-protocol/objects")
    async def register(request: Request):
        return {"object_id": call(protocol.register, actor(request), **await body(request))}

    @app.post("/api/review-protocol/import")
    async def import_objects(request: Request):
        value = await body(request)
        who = actor(request)
        call(protocol.require, who, "admin")
        ids = call(protocol.import_candidates, who, value.get("objects"))
        return {"object_ids": ids, "human_gold": False}

    @app.get("/api/review-protocol/object/{oid:path}")
    async def object_view(oid: str, request: Request):
        return call(protocol.view, actor(request), oid)

    @app.post("/api/review-protocol/assignment")
    async def assignment(request: Request):
        value = await body(request)
        return {
            "state": call(
                protocol.assign,
                actor(request),
                value.get("object_id"),
                value.get("reviewers"),
                value.get("adjudicator"),
            )
        }

    @app.post("/api/review-protocol/review")
    async def review(request: Request):
        value = await body(request)
        return call(
            protocol.save,
            actor(request),
            value.get("object_id"),
            value.get("form"),
            draft=value.get("draft") is True,
        )

    @app.post("/api/review-protocol/adjudication")
    async def adjudication(request: Request):
        value = await body(request)
        function = (
            protocol.adjudication_draft if value.get("draft") is True else protocol.adjudicate
        )
        return call(function, actor(request), value.get("object_id"), value.get("form"))

    @app.get("/api/review-protocol/agreement")
    async def agreement(request: Request):
        return call(protocol.agreement, actor(request))

    @app.post("/api/review-protocol/gold-export")
    async def export(request: Request):
        value = await body(request)
        return call(
            protocol.export_gold, actor(request), purpose=value.get("purpose", "evaluation")
        )

    @app.get("/api/review-protocol/audit")
    async def audit(request: Request):
        return call(protocol.audit_export, actor(request))

    @app.get("/api/review-protocol/original/{sha}")
    async def original(sha: str, request: Request):
        who = actor(request)
        account = protocol.account(who)
        if account["role"] != "admin":
            permitted = False
            for oid, obj in protocol.objects().items():
                try:
                    protocol._access(who, oid)
                except PermissionError:
                    continue
                if any(s["sha256"] == sha for s in obj["sources"]):
                    permitted = True
                    break
            if not permitted:
                raise HTTPException(403, "Original is outside assigned review objects")
        from crawler.web.bavaria import read_original

        with studio.index.conn() as c:
            record = c.execute(
                "SELECT raw_path,raw_bytes FROM sources WHERE sha256=?", (sha,)
            ).fetchone()
        if not record:
            raise HTTPException(404, "Unknown source")
        try:
            data, media, _ = read_original(record, sha, studio.service.original_roots)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "Original unavailable or integrity failed") from exc
        return Response(
            data,
            media_type=media,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                "X-Original-SHA256": sha,
            },
        )
