# ruff: noqa: E501
"""Append-only annotation events; existing packet JSONL remains the export contract."""

import hashlib
import json
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from research.bavaria.completion.metrics import kappa

from .retrieval_benchmark.contract import LABELS as QREL_LABELS

RESERVE = 15 * 1024**3
STAGES = ["source", "fields", "retrieval", "answer"]
CHOICES = {
    "fidelity": ["faithful", "partial", "not_faithful", "unreadable", "unclear"],
    "document_type": [
        "strategy_plan",
        "report",
        "funding_information",
        "council_record",
        "news",
        "service_information",
        "mixed",
        "unclear",
    ],
    "temporal_basis": ["explicit_period", "publication_date_only", "undated", "unclear"],
    "authority": ["official", "institutional", "unclear", "not_authoritative"],
    "scope": ["local", "external", "shared", "unclear"],
    "policy": ["yes", "no", "unclear"],
    "answerability": ["ANSWERABLE", "PARTIALLY_ANSWERABLE", "UNANSWERABLE", "UNCLEAR"],
    "temporal": [
        "PLAN",
        "ADOPTION",
        "REPORTED_IMPLEMENTATION",
        "REPORTED_INVENTORY_ESTIMATE",
        "MEASUREMENT",
        "UNKNOWN",
    ],
    "judgment": ["supported", "contradicted", "not_proven", "unclear"],
    "relevance": ["relevant", "partial", "irrelevant", "unclear"],
    "qrel_relevance": QREL_LABELS,
    "complete": ["yes", "no", "unclear"],
}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def required_stages(task):
    """Legacy rounds remain unchanged; new cases declare their primary review purpose."""
    stages = task.get("required_stages", STAGES)
    if (
        not isinstance(stages, list)
        or not stages
        or len(set(stages)) != len(stages)
        or any(s not in STAGES for s in stages)
    ):
        raise ValueError("Invalid task stage plan")
    return stages


def prerequisites(tasks, task, stage):
    if stage not in required_stages(task):
        raise ValueError("Stage outside primary review purpose")
    if "required_stages" in task:
        return [
            (task["task_id"], s)
            for s in required_stages(task)[: required_stages(task).index(stage)]
        ]
    return [
        (t["task_id"], s)
        for s in STAGES[: STAGES.index(stage)]
        for t in tasks
        if s in required_stages(t)
    ]


def evidence_from_forms(forms, task_id):
    """Keep actual stage-bound evidence, including adjudicator corrections."""
    spans = []
    for form in forms.values():
        if not form:
            continue
        for span in form.get("spans", []):
            if span not in spans:
                spans.append(dict(span))
        if form.get("quote") and not any(
            x.get("verbatim_quote") == form["quote"]
            and x.get("source_sha256") == form.get("source_sha256")
            for x in spans
        ):
            spans.append(
                {
                    "source_sha256": form["source_sha256"],
                    "physical_page": form.get("page"),
                    "verbatim_quote": form["quote"],
                    "required_bundle": form.get("bundle") or "human:" + task_id,
                    "relevance": 2,
                }
            )
    return spans


class Store:
    def __init__(self, path, bundle, simulation=False):
        self.path = Path(path)
        self.bundle = bundle
        self.simulation = simulation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < RESERVE + 16 * 1024**2:
            raise ValueError("15-GiB reserve unavailable")
        with self.conn() as c:
            c.executescript("""
              CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at TEXT,role TEXT,
                task TEXT,stage TEXT,kind TEXT,payload TEXT,previous TEXT,hash TEXT UNIQUE);
              CREATE INDEX IF NOT EXISTS lookup ON events(role,task,stage,kind,seq);
              CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable'); END;
              CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable'); END;
            """)
            config = {"selection": bundle["selection"], "simulation": simulation}
            existing = c.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
            if existing and json.loads(existing[0]) != config:
                raise ValueError("Study binding changed; choose a fresh workspace")
            if not existing:
                c.execute("INSERT INTO metadata VALUES('config',?)", (canonical(config),))
                c.execute(
                    "INSERT INTO metadata VALUES('keys',?)",
                    (canonical({r: secrets.token_urlsafe(18) for r in ["A", "B", "C"]}),),
                )
                c.execute("INSERT INTO metadata VALUES('phase','calibration')")
                c.execute("INSERT INTO metadata VALUES('mode','three')")
        self.path.chmod(0o600)
        self.path.parent.chmod(0o700)
        self.audit()

    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def meta(self, key):
        with self.conn() as c:
            return c.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]

    def event(self, c, role, task, stage, kind, payload):
        if shutil.disk_usage(self.path.parent).free < RESERVE + 2 * 1024**2:
            raise ValueError("Systemreserve erreicht; keine weiteren Schreibzugriffe")
        last = c.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last[0] if last else "GENESIS"
        at = datetime.now(timezone.utc).isoformat()
        row = {
            "at": at,
            "role": role,
            "task": task,
            "stage": stage,
            "kind": kind,
            "payload": payload,
            "previous": prev,
        }
        c.execute(
            "INSERT INTO events(at,role,task,stage,kind,payload,previous,hash) VALUES(?,?,?,?,?,?,?,?)",
            (at, role, task, stage, kind, canonical(payload), prev, digest(row)),
        )

    def audit(self):
        prev = "GENESIS"
        with self.conn() as c:
            for r in c.execute("SELECT * FROM events ORDER BY seq"):
                obj = {k: r[k] for k in ["at", "role", "task", "stage", "kind", "previous"]}
                obj["payload"] = json.loads(r["payload"])
                if r["previous"] != prev or digest(obj) != r["hash"]:
                    raise ValueError("Event integrity failure")
                prev = r["hash"]
        return prev

    def events(self, role=None, kind=None):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in rows
            if (not role or r["role"] == role) and (not kind or r["kind"] == kind)
        ]

    def latest(self, role, task, stage, kind="lock"):
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM events WHERE role=? AND task=? AND stage=? AND kind=? ORDER BY seq DESC LIMIT 1",
                (role, task, stage, kind),
            ).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def profile(self, role):
        rows = self.events(role, "profile")
        return rows[-1]["payload"] if rows else None

    def tasks(self):
        ids = set(self.bundle["selection"][self.meta("phase")])
        return [t for t in self.bundle["tasks"] if t["task_id"] in ids]

    def locked(self, role):
        return all(
            self.latest(role, t["task_id"], s) for t in self.tasks() for s in required_stages(t)
        )

    def save(self, role, task, stage, payload, lock=False):
        if role not in ["A", "B"] or stage not in STAGES:
            raise ValueError("Not a reviewer stage")
        t = next((t for t in self.tasks() if t["task_id"] == task), None)
        if not t:
            raise ValueError("Task not available in this phase")
        if self.latest(role, task, stage):
            raise ValueError("Ein abgeschlossenes Originalurteil kann nicht überschrieben werden.")
        # Legacy reviews keep global rounds; focused cases only require their own prior steps.
        for tid, earlier in prerequisites(self.tasks(), t, stage):
            if not self.latest(role, tid, earlier):
                raise ValueError("Bitte zuerst die vorherige Runde vollständig abschließen.")
        if lock:
            if not self.profile(role):
                raise ValueError("Real reviewer profile required")
            self.validate(stage, payload, t)
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            # Recheck under write lock against concurrent browser tabs.
            exists = c.execute(
                "SELECT 1 FROM events WHERE role=? AND task=? AND stage=? AND kind='lock'",
                (role, task, stage),
            ).fetchone()
            if exists:
                raise ValueError("Dieses Urteil ist bereits abgeschlossen.")
            self.event(c, role, task, stage, "lock" if lock else "draft", payload)

    def validate(self, stage, p, t):
        if not isinstance(p, dict) or len(canonical(p)) > 256000:
            raise ValueError("Invalid form")
        if p.get("technically_unreviewable"):
            raise ValueError(
                "Technisch nicht prüfbare Fälle bleiben offene Entwürfe, keine negativen Goldlabels."
            )
        if p.get("original_inspected") is not True:
            raise ValueError("Bitte die Prüfung des Originals bestätigen.")
        if "required_stages" in t:
            if p.get("source_sha256") not in t["allowed_original_sha256"]:
                raise ValueError("Bitte eine passende Originalquelle für dieses Urteil auswählen.")
            if (
                stage == "fields"
                and p.get("judgment") == "supported"
                and not (p.get("quote") or p.get("spans"))
            ):
                raise ValueError("Belegte Felder benötigen einen ausdrücklichen Originalbeleg.")
        if p.get("escalate") and not p.get("comment", "").strip():
            raise ValueError("Explain uncertainty/escalation briefly")
        if stage == "source":
            if t.get("quality_review") or t.get("primary_purpose") == "source":
                for key in ["fidelity", "document_type", "temporal_basis"]:
                    if p.get(key) not in CHOICES[key]:
                        raise ValueError(
                            "Bitte Originaltreue, Dokumenttyp und zeitliche Einordnung beurteilen."
                        )
            for k in ["authority", "scope", "policy", "answerability"]:
                if p.get(k) not in CHOICES[k]:
                    raise ValueError("Missing decision: " + k)
            if p["source_sha256"] not in t["allowed_original_sha256"]:
                raise ValueError("Wrong source")
            if p["answerability"] in ["ANSWERABLE", "PARTIALLY_ANSWERABLE"] and not (
                p.get("quote") or p.get("spans")
            ):
                raise ValueError("Beantwortbare Referenz benötigt Originalbelege")
        elif stage == "fields":
            if p.get("temporal") not in CHOICES["temporal"]:
                raise ValueError("Bitte die zeitliche Aussageart wählen.")
            if p.get("judgment") not in CHOICES["judgment"]:
                raise ValueError("Bitte eine fachliche Entscheidung wählen.")
        elif stage == "retrieval":
            if set(p.get("relevance", {})) != {x["id"] for x in t["pool"]}:
                raise ValueError(
                    "Bitte jeden Kandidaten beurteilen. Ein fehlendes Urteil bedeutet nicht irrelevant."
                )
            allowed = (
                CHOICES["qrel_relevance"]
                if t.get("qrel_contract") == "retrieval-qrels-v1"
                else CHOICES["relevance"]
            )
            if any(v not in allowed for v in p["relevance"].values()):
                raise ValueError("Invalid relevance")
            if p.get("complete") not in CHOICES["complete"]:
                raise ValueError("Bitte die Evidenzvollständigkeit beurteilen.")
            if t.get("qrel_contract") == "retrieval-qrels-v1":
                from .retrieval_benchmark.contract import validate_qrels

                validate_qrels(p, t)
        else:
            answers = self.answers_for(t)
            if set(p.get("answers", {})) != {x["neutral_answer_id"] for x in answers}:
                raise ValueError("Bitte jede neutrale Antwort beurteilen.")
            for a in p.get("answers", {}).values():
                if (
                    a.get("complete") not in CHOICES["complete"]
                    or a.get("abstention") not in CHOICES["complete"]
                ):
                    raise ValueError("Overall answer judgment missing")
                if not a.get("claims"):
                    raise ValueError(
                        "Bitte mindestens einen einzelnen Claim oder eine Enthaltung beurteilen."
                    )
                for claim in a["claims"]:
                    if not claim.get("text", "").strip() or claim.get("type") not in [
                        "assertion",
                        "abstention",
                        "question",
                    ]:
                        raise ValueError("Atomic claim text/type missing")
                    if any(
                        claim.get(k) not in CHOICES["judgment"]
                        for k in ["support", "citation", "scope_time_numbers"]
                    ):
                        raise ValueError("Atomic claim decisions incomplete")

    def answers_for(self, t):
        # Match wording-independent explicit original citation access where possible.
        return [a for a in self.bundle["answers"] if a["question"] == t["question"]]

    def conflicts(self):
        if not all(self.locked(r) for r in ["A", "B"]):
            raise ValueError("Both complete individual reviews must be locked")
        result = []
        for t in self.tasks():
            for stage in required_stages(t):
                a = self.latest("A", t["task_id"], stage)
                b = self.latest("B", t["task_id"], stage)
                fields = [
                    k
                    for k in set(a["payload"]) | set(b["payload"])
                    if k
                    not in {
                        "seconds",
                        "comment",
                        "original_inspected",
                        "quote",
                        "page",
                        "source_sha256",
                    }
                    and a["payload"].get(k) != b["payload"].get(k)
                ]
                if fields or a["payload"].get("escalate") or b["payload"].get("escalate"):
                    result.append(
                        {
                            "task_id": t["task_id"],
                            "stage": stage,
                            "a": a,
                            "b": b,
                            "different_fields": fields,
                            "resolution": self.latest("C", t["task_id"], stage, "adjudicate"),
                        }
                    )
        return result

    def adjudication_payload(self, task, stage, p):
        conflict = next(
            (x for x in self.conflicts() if x["task_id"] == task and x["stage"] == stage), None
        )
        if not conflict or conflict["resolution"]:
            raise ValueError("No open conflict")
        if (
            p.get("choice") not in ["A", "B", "custom", "unresolved"]
            or not p.get("rationale", "").strip()
        ):
            raise ValueError("Bitte eine Auflösung und eine kurze Begründung erfassen.")
        if p["choice"] == "custom":
            task = next(t for t in self.tasks() if t["task_id"] == task)
            self.validate(stage, p.get("custom_payload", {}), task)
            task = task["task_id"]
        p = {**p, "a_hash": conflict["a"]["hash"], "b_hash": conflict["b"]["hash"]}
        return p

    def adjudicate(self, task, stage, p):
        p = self.adjudication_payload(task, stage, p)
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute(
                "SELECT 1 FROM events WHERE role='C' AND task=? AND stage=? AND kind='adjudicate'",
                (task, stage),
            ).fetchone():
                raise ValueError("Dieser Konflikt wurde bereits adjudiziert.")
            self.event(c, "C", task, stage, "adjudicate", p)

    def report(self):
        out = {
            "simulation": self.simulation,
            "phase": self.meta("phase"),
            "gold_status": "NOT_RELEASED",
            "metrics": {},
            "profiles": {},
            "audit_head": self.audit(),
        }
        for role in ["A", "B", "C"]:
            out["profiles"][role] = self.profile(role)
        profiles = [p for p in out["profiles"].values() if p]
        out["independence"] = {
            "declared_distinct_people": len({p["name"].strip().casefold() for p in profiles}),
            "developer_roles": [r for r, p in out["profiles"].items() if p and p.get("developer")],
            "mode": self.meta("mode"),
            "owner_verification_required": True,
            "simulation_is_not_human_evidence": self.simulation,
        }
        for stage in STAGES:
            keys = {
                "source": ["authority", "scope", "policy", "answerability"],
                "fields": ["temporal", "judgment"],
                "retrieval": ["complete"],
                "answer": [],
            }[stage]
            if stage == "source" and any(
                t.get("quality_review") or t.get("primary_purpose") == "source"
                for t in self.tasks()
            ):
                keys += ["fidelity", "document_type", "temporal_basis"]
            for key in keys:
                pairs = []
                matrix: dict[str, dict[str, int]] = {}
                missing = 0
                for t in self.tasks():
                    if stage not in required_stages(t):
                        continue
                    if key in ["fidelity", "document_type", "temporal_basis"] and not (
                        t.get("quality_review") or t.get("primary_purpose") == "source"
                    ):
                        continue
                    a = self.latest("A", t["task_id"], stage)
                    b = self.latest("B", t["task_id"], stage)
                    x = a["payload"].get(key) if a else None
                    y = b["payload"].get(key) if b else None
                    if x is None or y is None or x == "not_checkable" or y == "not_checkable":
                        missing += 1
                        continue
                    pairs.append((x, y))
                    matrix.setdefault(x, {})
                    matrix[x][y] = matrix[x].get(y, 0) + 1
                out["metrics"][stage + "." + key] = {
                    **kappa(pairs),
                    "missing_pairs": missing,
                    "confusion_matrix": matrix,
                    "scale": "nominal; UNKNOWN is a category, absent is missing",
                }
        # Candidate IDs are pooled and method-neutral; absent reviews stay Missing.
        pair_groups: dict[str, list[tuple[str, str]]] = {}
        missing_groups: dict[str, int] = {}
        for task in self.tasks():
            tid = task["task_id"]
            for stage in ["retrieval", "answer", "fields"]:
                if stage not in required_stages(task):
                    continue
                a = self.latest("A", tid, stage)
                b = self.latest("B", tid, stage)
                ap = a["payload"] if a else {}
                bp = b["payload"] if b else {}
                labels = []
                if stage == "retrieval":
                    labels = [
                        (
                            "retrieval.relevance",
                            ap.get("relevance", {}).get(x["id"]),
                            bp.get("relevance", {}).get(x["id"]),
                        )
                        for x in task["pool"]
                    ]
                elif stage == "fields":
                    labels = [
                        (
                            "fields." + key,
                            (ap.get(key) or "UNKNOWN") if a else None,
                            (bp.get(key) or "UNKNOWN") if b else None,
                        )
                        for key in [
                            "actor",
                            "actor_role",
                            "measure",
                            "status",
                            "amount",
                            "unit",
                            "amount_kind",
                            "valid_time",
                        ]
                    ]
                else:
                    for answer in self.answers_for(task):
                        aid = answer["neutral_answer_id"]
                        aa = ap.get("answers", {}).get(aid, {})
                        bb = bp.get("answers", {}).get(aid, {})
                        labels.extend(
                            ("answer." + key, aa.get(key), bb.get(key))
                            for key in ["complete", "abstention"]
                        )
                        am = {x["text"].strip(): x for x in aa.get("claims", [])}
                        bm = {x["text"].strip(): x for x in bb.get("claims", [])}
                        for text in set(am) | set(bm):
                            labels.extend(
                                (
                                    "claim." + key,
                                    am.get(text, {}).get(key),
                                    bm.get(text, {}).get(key),
                                )
                                for key in ["support", "citation", "scope_time_numbers"]
                            )
                for key, x, y in labels:
                    if x is None or y is None or x == "not_checkable" or y == "not_checkable":
                        missing_groups[key] = missing_groups.get(key, 0) + 1
                    else:
                        pair_groups.setdefault(key, []).append((str(x), str(y)))
        for key in set(pair_groups) | set(missing_groups):
            pairs = pair_groups.get(key, [])
            matrix = {}
            for x, y in pairs:
                matrix.setdefault(x, {})
                matrix[x][y] = matrix[x].get(y, 0) + 1
            out["metrics"][key] = {
                **kappa(pairs),
                "confusion_matrix": matrix,
                "missing_pairs": missing_groups.get(key, 0),
                "note": "Claims paired only by exact human segmentation text; unmatched segments stay missing.",
            }
        out["calibration_feedback"] = {
            r: [x["payload"] for x in self.events(r, "feedback")] for r in ["A", "B"]
        }
        out["technical_validation_errors"] = {
            r: len(self.events(r, "validation_error")) for r in ["A", "B"]
        }
        out["technically_unreviewable_drafts"] = {
            r: [
                {"task": t["task_id"], "stage": s}
                for t in self.tasks()
                for s in required_stages(t)
                if not self.latest(r, t["task_id"], s)
                and (self.latest(r, t["task_id"], s, "draft") or {})
                .get("payload", {})
                .get("technically_unreviewable")
            ]
            for r in ["A", "B"]
        }
        locks = self.events(kind="lock")
        out["timing"] = {
            r: {
                "completed_forms": sum(x["role"] == r for x in locks),
                "seconds": sum(x["payload"].get("seconds", 0) for x in locks if x["role"] == r),
                "escalations": sum(
                    bool(x["payload"].get("escalate")) for x in locks if x["role"] == r
                ),
            }
            for r in ["A", "B"]
        }
        return out

    def export(self):
        # Adapter output: stable packet IDs and existing A/B/adjudication shapes,
        # plus complete append-only history; not a second Gold-label schema.
        result = {
            "schema": "gold-studio-packet-adapter-v1",
            "selection": self.bundle["selection"],
            "report": self.report(),
            "events": self.events(),
            "forms": {},
        }
        for role in ["A", "B"]:
            from .prepare import PACKET, lines

            template = {r["task_id"]: r for r in lines(PACKET / role / "annotations.jsonl")}
            forms = []
            for t in self.tasks():
                tid = t["task_id"]
                row = dict(template.get(tid, next(iter(template.values()))))
                row["task_id"] = tid
                p = self.profile(role) or {}
                row.update(
                    reviewer_id=p.get("name", ""),
                    reviewer_qualification=p.get("qualification", ""),
                    independent_human_attestation=p.get("independent", False)
                    and p.get("human", False)
                    and not self.simulation,
                )
                stages = {s: self.latest(role, tid, s) for s in STAGES}
                src = stages["source"]["payload"] if stages["source"] else {}
                row.update(
                    source_authority=src.get("authority"),
                    document_type=src.get("document_type"),
                    claim_scope_ags=src.get("ags", []),
                    policy_relevance=src.get("policy"),
                    source_scope_review_complete=bool(stages["source"])
                    and src.get("inventory_complete") is True,
                    originals_inspected=sorted(
                        {
                            v["payload"]["source_sha256"]
                            for v in stages.values()
                            if v and v["payload"].get("source_sha256")
                        }
                    ),
                    answerability={
                        "ANSWERABLE": "FULL",
                        "PARTIALLY_ANSWERABLE": "PARTIAL",
                        "UNANSWERABLE": "UNSUPPORTED_IN_SCOPE",
                        "UNCLEAR": "CONFLICTED",
                    }.get(str(src.get("answerability") or "")),
                )
                row["required_stages"] = required_stages(t)
                row["primary_purpose"] = t.get("primary_purpose")
                row["studio_stages"] = {s: r["payload"] if r else None for s, r in stages.items()}
                row["minutes"] = (
                    sum(r["payload"].get("seconds", 0) for r in stages.values() if r) / 60
                )
                spans = evidence_from_forms(
                    {key: value["payload"] if value else None for key, value in stages.items()}, tid
                )
                row["evidence_spans"] = spans
                field = stages["fields"]["payload"] if stages["fields"] else {}
                row["fields"] = {
                    k: field.get(k) or None
                    for k in [
                        "actor",
                        "actor_role",
                        "measure",
                        "status",
                        "amount",
                        "unit",
                        "amount_kind",
                        "valid_time",
                    ]
                }
                row["temporal_status"] = field.get("temporal")
                row["relations"] = list(field.get("relations", [])) + (
                    [
                        {
                            "subject": field["relation_subject"],
                            "predicate": field.get("relation_predicate"),
                            "object": field.get("relation_object"),
                            "evidence_spans": spans,
                        }
                    ]
                    if field.get("relation_subject")
                    else []
                )
                answer_stage = stages["answer"]["payload"] if stages["answer"] else {}
                row["candidate_answer_claims"] = answer_stage.get("answers", {})
                forms.append(row)
            result["forms"][role] = forms
        result["adjudication"] = []
        if all(self.locked(r) for r in ["A", "B"]):
            conflicts = {(x["task_id"], x["stage"]): x for x in self.conflicts()}
            records = {r: {x["task_id"]: x for x in result["forms"][r]} for r in ["A", "B"]}
            for t in self.tasks():
                tid = t["task_id"]
                a = records["A"][tid]
                b = records["B"][tid]
                final = json.loads(canonical(a))
                unresolved = False
                for stage in required_stages(t):
                    x = conflicts.get((tid, stage))
                    resolution = x.get("resolution") if x else None
                    if x and (not resolution or resolution["payload"]["choice"] == "unresolved"):
                        unresolved = True
                        continue
                    r = resolution["payload"]["choice"] if resolution else "A"
                    selected = records["A" if r == "custom" else r][tid]
                    if r == "custom":
                        assert resolution is not None
                        selected = json.loads(canonical(selected))
                        custom = resolution["payload"]["custom_payload"]
                        selected["studio_stages"][stage] = custom
                        if stage == "source":
                            selected.update(
                                source_authority=custom["authority"],
                                document_type=custom.get("document_type"),
                                claim_scope_ags=custom.get("ags", []),
                                policy_relevance=custom["policy"],
                                answerability={
                                    "ANSWERABLE": "FULL",
                                    "PARTIALLY_ANSWERABLE": "PARTIAL",
                                    "UNANSWERABLE": "UNSUPPORTED_IN_SCOPE",
                                    "UNCLEAR": "CONFLICTED",
                                }[custom["answerability"]],
                                source_scope_review_complete=custom.get("inventory_complete")
                                is True,
                            )
                            selected["evidence_spans"] = list(custom.get("spans", []))
                            if custom.get("quote"):
                                selected["evidence_spans"].append(
                                    {
                                        "source_sha256": custom["source_sha256"],
                                        "physical_page": custom.get("page"),
                                        "verbatim_quote": custom["quote"],
                                        "required_bundle": custom.get("bundle") or "human:" + tid,
                                        "relevance": 2,
                                    }
                                )
                        if stage == "fields":
                            selected["fields"] = {
                                k: custom.get(k) or None
                                for k in [
                                    "actor",
                                    "actor_role",
                                    "measure",
                                    "status",
                                    "amount",
                                    "unit",
                                    "amount_kind",
                                    "valid_time",
                                ]
                            }
                            selected["temporal_status"] = custom.get("temporal")
                            selected["relations"] = list(custom.get("relations", [])) + (
                                [
                                    {
                                        "subject": custom["relation_subject"],
                                        "predicate": custom.get("relation_predicate"),
                                        "object": custom.get("relation_object"),
                                    }
                                ]
                                if custom.get("relation_subject")
                                else []
                            )
                        if stage == "answer":
                            selected["candidate_answer_claims"] = custom.get("answers", {})
                    final["studio_stages"][stage] = selected["studio_stages"][stage]
                    fields = {
                        "source": [
                            "source_authority",
                            "document_type",
                            "claim_scope_ags",
                            "policy_relevance",
                            "answerability",
                            "evidence_spans",
                            "originals_inspected",
                            "source_scope_review_complete",
                        ],
                        "fields": ["fields", "relations", "temporal_status"],
                        "retrieval": [],
                        "answer": ["candidate_answer_claims"],
                    }[stage]
                    for key in fields:
                        final[key] = selected[key]
                final["evidence_spans"] = evidence_from_forms(final["studio_stages"], tid)
                final["originals_inspected"] = sorted(
                    {
                        p["source_sha256"]
                        for p in final["studio_stages"].values()
                        if p and p.get("source_sha256")
                    }
                )
                field_evidence = evidence_from_forms(
                    {"fields": final["studio_stages"].get("fields")}, tid
                )
                for relation in final.get("relations", []):
                    if not relation.get("evidence_spans"):
                        relation["evidence_spans"] = field_evidence or final["evidence_spans"]
                final["component_ids"] = t["components"]
                final["task_class"] = t.get("stratum", {}).get("family", t["cohort"])

                def record_hash(row):
                    return hashlib.sha256(
                        json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()

                result["adjudication"].append(
                    {
                        "task_id": tid,
                        "adjudicator_id": (self.profile("C") or {}).get("name", ""),
                        "reviewer_A_record_sha256": record_hash(a),
                        "reviewer_B_record_sha256": record_hash(b),
                        "resolution": "unresolved" if unresolved else "stage_bound_resolution",
                        "final_reference": None if unresolved else final,
                        "dependency_clearance": False,
                        "rationale": "Studio stage-bound adjudication; actual owner dependency clearance required.",
                    }
                )
        # Conservative dependence audit, never a factual entity-identity assertion.
        # Shared inspected originals, declared scopes and exact actor/project labels
        # join references. Unknown and absent names never become hubs.
        from research.bavaria.relational.build import Components

        components = Components()
        dependencies = {}
        taskmap = {t["task_id"]: t for t in self.tasks()}
        for j in result["adjudication"]:
            if not j["final_reference"]:
                continue
            ref = j["final_reference"]
            t = taskmap[j["task_id"]]
            values = ["task:" + j["task_id"], *["prior:" + x for x in t["components"]]]
            values += ["source:" + x for x in ref.get("originals_inspected", [])]
            values += ["source:" + x["source_sha256"] for x in ref["evidence_spans"]]
            values += ["municipality:" + x for x in ref.get("claim_scope_ags", [])]
            for key in ["actor", "measure"]:
                value = ref.get("fields", {}).get(key)
                if value and str(value).strip().upper() not in ["UNKNOWN", "UNKLAR"]:
                    values.append(key + ":" + str(value).strip().casefold())
            components.union(*values)
            dependencies[j["task_id"]] = values
        for j in result["adjudication"]:
            if j["final_reference"]:
                j["final_reference"]["component_ids"] = [components.find("task:" + j["task_id"])]
        result["dependency_audit"] = {
            "links": dependencies,
            "rule": "conservative exact shared-source/scope/actor/project links; manual paraphrase/institution clearance still required",
            "historical_splits_changed": False,
        }
        result["content_sha256"] = digest(result)
        return result
