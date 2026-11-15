"""Deterministic metric denominators and fail-closed eligibility, no inference."""
from __future__ import annotations

import csv
import io
import math
import re

from .contracts import (
    Inputs, Inventory, digest, finite_usage, integer, nonempty,
    require, sha, span, timestamp, unique, validate_tasks,
)

CONTRACT_SHA256 = "dd49934524d61f2bfcae67eea044128be54a9387b22573e479e33ff289df50b8"
METRICS = tuple(f"M{i:02d}" for i in range(1, 17))
QUALITY = set(METRICS) - {"M01", "M02", "M15", "M16"}
VARIANTS = {"LEX", "SEM", "LEX_SEM", "SEM_GRAPH"}
FINDINGS = {"DOCUMENTED", "CONFLICTING", "NO_EVIDENCE_IN_SCOPE", "NOT_ASSESSED"}
ANSWERABILITY = {"FULL", "PARTIAL", "UNSUPPORTED_IN_SCOPE", "CONFLICTED", "UNASSESSED"}
QUALIFIERS = ("municipality", "time", "institution")


class Evaluation:
    def __init__(self, inputs: Inputs):
        self.inputs = inputs
        self.manifest = inputs.manifest
        names = self.manifest["files"]
        require(digest(inputs.data(names["metric_contract"])) == CONTRACT_SHA256,
                "Metric contract is not the v0.2 frozen contract")
        self.contract = list(csv.DictReader(io.StringIO(inputs.text(names["metric_contract"]))))
        require(tuple(r["metric_id"] for r in self.contract) == METRICS, "Metric contract IDs changed")
        self.inventory = Inventory(inputs, inputs.json(names["inventory"]))
        self.tasks, self.overlaps = validate_tasks(inputs, inputs.json(names["tasks"]), self.inventory)
        self.references = inputs.json(names["references"])
        self.outputs = unique(inputs.json(names["outputs"]), "output_id")
        self.reviews = unique(inputs.json(names["answer_reviews"]), "output_id")
        require(self.reviews.keys() <= self.outputs.keys(), "Review of unknown output")
        self.ledger = unique(inputs.json(names["resources"]), "invocation_id")
        self.facets = self.index_reference("facets", "aspect_id")
        self.relevance = self.index_reference("relevance", "unit_id")
        self.task_reviews = self.index_reference("tasks", None)
        self.bundles = unique(self.references.get("bundles", []), "bundle_id")
        self.gaps = self.index_reference("gaps", "gap_id")
        self.conflicts = self.index_reference("conflicts", "conflict_id")
        self.intents = self.index_reference("intents", None)
        self.second_reviews = self.index_reference("second_reviews", None)
        self.validate_references()
        self.resolved = {}
        self.resource_rows = []
        self.validate_outputs()
        self.validate_answer_reviews()
        self.validate_resources()
        self.release = self.references.get("release", {})
        self.admitted = self.release.get("admitted_task_ids", [])
        require(len(self.admitted) == len(set(self.admitted))
                and set(self.admitted) <= self.tasks.keys(), "Invalid admitted task set")
        require(set(self.release.get("allowed_metrics", [])) <= set(METRICS), "Unknown released metric")
        require(self.release.get("state", "OPEN") in {"OPEN", "CLOSED_RESTRICTED", "CLOSED_COMPLETE"},
                "Invalid reference release state")
        self.release_ok = (
            self.release.get("state") in {"CLOSED_RESTRICTED", "CLOSED_COMPLETE"}
            and inputs.independently_reviewed(self.release)
            and bool(self.admitted)
            and all(self.scope_reviewed(t) for t in self.admitted)
            and all(any(self.inputs.independently_reviewed(self.facets.get((t, a), {}))
                        and self.facets[t, a]["finding"] != "NOT_ASSESSED"
                        for a in self.tasks[t]["aspects"]) for t in self.admitted)
            and self.review_protocol_ready()
        )
        self.status = "DIAGNOSTIC"
        if self.release_ok and self.manifest["study_origin"] != "POSTHOC_E2" \
                and self.manifest["purpose"] != "RESEARCH_DIAGNOSTIC":
            complete = (set(self.admitted) == self.tasks.keys()
                        and not self.missing_output_cells
                        and all(self.usage[o]["complete"] for o in self.outputs)
                        and all(self.reference_complete(t) for t in self.tasks)
                        and not self.overlaps)
            self.status = ("SCORABLE_COMPLETE" if complete and
                           self.release["state"] == "CLOSED_COMPLETE" else "SCORABLE_RESTRICTED")
        if not self.outputs:
            self.status = "DIAGNOSTIC"
        self.rows = []

    def review_protocol_ready(self):
        asset = self.release.get("review_protocol_asset")
        if not asset:
            return False
        protocol = self.inputs.json(asset)
        for field in ("source_order", "second_review_selection_rule", "prior_exposure_disclosure"):
            nonempty(protocol.get(field), "review protocol " + field)
        require(protocol.get("rubric_asset") == self.manifest["conditions"]["rubric_asset"],
                "Review protocol/rubric mismatch")
        planned = protocol.get("second_review_task_ids", [])
        require(len(planned) == len(set(planned)) and set(planned) <= self.tasks.keys(),
                "Invalid predeclared second-review sample")
        if not planned:
            return False
        for tid in planned:
            second = self.second_reviews.get(tid, {})
            first = self.task_reviews.get(tid, {})
            if not (self.inputs.independently_reviewed(second) and self.inputs.reviewed(first)):
                return False
            require(second.get("agreement") in {"AGREE", "ADJUDICATED_DISAGREEMENT", "UNRESOLVED"},
                    "Second review outcome missing")
            a = self.inputs.audit_cache[second["review"]["audit_asset"]]
            b = self.inputs.audit_cache[first["review"]["audit_asset"]]
            if a["reviewer_id"] == b["reviewer_id"] or second["agreement"] == "UNRESOLVED":
                return False
            nonempty(second.get("adjudication_note"), "second review record")
        return True

    def index_reference(self, name, field):
        result = {}
        for row in self.references.get(name, []):
            require(row["task_id"] in self.tasks, f"Unknown {name} task")
            key = (row["task_id"], row[field]) if field else row["task_id"]
            require(key not in result, f"Duplicate {name} judgment")
            self.inputs.reviewed(row)
            result[key] = row
        return result

    def validate_references(self):
        inv = self.inventory
        for (tid, aspect), facet in self.facets.items():
            require(aspect in self.tasks[tid]["aspects"], "Unknown reference aspect")
            require(facet["finding"] in FINDINGS, "Invalid aspect finding")
            for bundle_id in facet.get("bundle_ids", []):
                require(bundle_id in self.bundles, "Unknown evidence bundle")
                bundle = self.bundles[bundle_id]
                require(bundle["task_id"] == tid and bundle["aspect_id"] == aspect,
                        "Evidence bundle assigned to wrong task/aspect")
        for bundle in self.bundles.values():
            require((bundle["task_id"], bundle["aspect_id"]) in self.facets, "Orphan evidence bundle")
            ids = bundle["required_locator_ids"]
            require(ids and len(ids) == len(set(ids)), "Empty/duplicate bundle locators")
            task = self.tasks[bundle["task_id"]]
            for lid in ids:
                require(lid in inv.locators, "Unknown bundle locator")
                require(inv.locators[lid]["source_scope_id"] == task["source_scope_id"],
                        "Bundle locator outside task scope")
            self.inputs.reviewed(bundle)
        for (tid, uid), judgment in self.relevance.items():
            require(uid in self.scope_units(tid), "Relevance unit outside task scope")
            require(judgment["grade"] is None or
                    (type(judgment["grade"]) is int and 0 <= judgment["grade"] <= 3),
                    "Grade must be explicit 0..3 or null")
        for tid, review in self.task_reviews.items():
            require(type(review.get("scope_complete")) is bool, "Scope completeness must be a boolean")
            require(review["answerability"] in ANSWERABILITY, "Invalid task answerability")
            expected = set(inv.scopes[self.tasks[tid]["source_scope_id"]]["source_ids"])
            ids = review.get("reviewed_source_ids", [])
            require(len(ids) == len(set(ids)) and set(ids) <= expected, "Invalid reviewed source set")
            if review.get("scope_complete"):
                require(set(ids) == expected, "Scope completeness contradicts source inventory")
        for (tid, _), conflict in self.conflicts.items():
            require(conflict.get("case_kind") in {"CONFLICT", "EVOLUTION"}, "Conflict/evolution kind required")
            nonempty(conflict.get("time_relation"), "reviewed temporal relation")
            nonempty(conflict.get("reviewed_conclusion"), "reviewed conflict conclusion")
            ids = conflict.get("locator_ids", [])
            require(len(set(ids)) >= 2 and len(ids) == len(set(ids)), "Both conflict/evolution sides required")
            require(all(lid in inv.locators and inv.locators[lid]["source_scope_id"] ==
                        self.tasks[tid]["source_scope_id"] for lid in ids), "Conflict locator outside scope")
        for tid, review in self.intents.items():
            timestamp(review.get("recorded_at"), "intent recorded_at")
            nonempty(review.get("information_need"), "frozen intent")
            require(review["information_need"] == self.tasks[tid]["information_need"], "Intent mismatch")

    def scope_units(self, tid):
        ids = self.inventory.scopes[self.tasks[tid]["source_scope_id"]]["source_ids"]
        return {uid for uid, unit in self.inventory.units.items() if unit["source_id"] in ids}

    def scope_reviewed(self, tid):
        r = self.task_reviews.get(tid, {})
        return bool(r.get("scope_complete") and self.inputs.independently_reviewed(r)
                    and r.get("answerability") != "UNASSESSED")

    def reference_complete(self, tid):
        return (self.scope_reviewed(tid)
                and all(self.inputs.independently_reviewed(self.facets.get((tid, a), {}))
                        and self.facets[tid, a]["finding"] != "NOT_ASSESSED"
                        for a in self.tasks[tid]["aspects"])
                and all(self.bundle_ready(f) for f in self.documented(tid))
                and all(self.judged(tid, uid) and self.inputs.independently_reviewed(self.relevance[tid, uid])
                        for uid in self.scope_units(tid)))

    def judged(self, tid, uid):
        r = self.relevance.get((tid, uid), {})
        return r.get("grade") is not None and self.inputs.reviewed(r)

    def validate_outputs(self):
        conditions = self.manifest["conditions"]
        integer(conditions["k"], "k", 1)
        for key in ("context_tokens", "total_input_tokens", "output_tokens"):
            integer(conditions["limits"][key], key, 1)
        for key in ("provider", "model", "revision", "prompt_sha256"):
            nonempty(conditions["answer_component"].get(key), "answer component " + key)
        sha(conditions["answer_component"]["prompt_sha256"])
        require(conditions["rubric_asset"] in self.inputs.assets, "Frozen graded rubric missing")
        expected = self.manifest.get("expected_outputs", [])
        require(expected, "Expected task/variant/experiment cells must be frozen")
        self.expected_cells = set()
        for cell in expected:
            identity = (cell["task_id"], cell["variant_id"], cell["experiment"])
            require(cell["task_id"] in self.tasks and cell["variant_id"] in VARIANTS
                    and cell["experiment"] in {"V1", "V2", "V3_BEFORE", "V3_AFTER"},
                    "Invalid expected output cell")
            require(identity not in self.expected_cells, "Duplicate expected output cell")
            self.expected_cells.add(identity)
        require({t for t, _, _ in self.expected_cells} == self.tasks.keys(),
                "Expected output inventory omits frozen tasks")
        observed_pairs = set()
        for oid, output in self.outputs.items():
            tid = output["task_id"]
            timestamp(output.get("produced_at"), "output produced_at")
            require(tid in self.tasks, "Unknown output task")
            require(output["variant_id"] in VARIANTS, "No implicit hybrid/LEX_SEM equivalence")
            require(output["experiment"] in {"V1", "V2", "V3_BEFORE", "V3_AFTER"}, "Unknown experiment")
            identity = (tid, output["variant_id"], output["experiment"])
            require(identity in self.expected_cells, "Output outside frozen expected cells")
            require(identity not in observed_pairs, "Duplicate output cell; use an explicit new run")
            observed_pairs.add(identity)
            require(output["source_scope_id"] == self.tasks[tid]["source_scope_id"], "Output scope mismatch")
            require(output["answer_component"] == conditions["answer_component"],
                    "Answer components differ across controlled comparison")
            for key in ("retrieval_component_id", "representation_id", "comparison_id"):
                nonempty(output.get(key), key)
            require(output["source_snapshot_sha256"] == self.manifest["assets"][
                self.manifest["files"]["inventory"]]["sha256"], "Output source snapshot mismatch")
            context = self.inputs.text(output["context_asset"])
            self.inputs.text(output["answer_asset"])
            ranked = []
            for view in output["ranked"]:
                uid, start, end = self.inventory.resolve(view)
                require(uid in self.scope_units(tid), "Ranked unit outside scope")
                require(uid not in ranked, "Duplicate canonical ranked unit; no silent deduplication")
                ranked.append(uid)
            ranges = []
            selected = unique(output["selected"], "selection_id")
            serial_intervals = []
            for selection in selected.values():
                uid, start, end = self.inventory.resolve(selection["view"])
                require(uid in ranked, "Context selection absent from ranked inventory")
                text = self.inventory.units[uid]["text"][start:end]
                encoding = selection.get("encoding", "PLAIN")
                require(encoding in {"PLAIN", "JSON_STRING_CONTENT"}, "Unsupported text serialization")
                if encoding == "JSON_STRING_CONTENT":
                    import json
                    text = json.dumps(text, ensure_ascii=False)[1:-1]
                a, b = selection["serialized_start"], selection["serialized_end"]
                require(span(context, a, b, "serialized selection") == text,
                        "Selected evidence differs from actual serialized context")
                require(all(b <= c or a >= d for c, d in serial_intervals), "Overlapping context offsets")
                serial_intervals.append((a, b))
                ranges.append((uid, start, end))
            self.resolved[oid] = {"ranked": ranked, "ranges": ranges, "context": context}
        self.missing_output_cells = sorted(self.expected_cells - observed_pairs)

    def validate_answer_reviews(self):
        for oid, review in self.reviews.items():
            self.inputs.reviewed(review)
            tid = self.outputs[oid]["task_id"]
            answer = self.inputs.text(self.outputs[oid]["answer_asset"])
            claims = unique(review.get("claims", []), "claim_id")
            for claim in claims.values():
                require(claim["claim_type"] in {"FACTUAL", "NONFACTUAL"}, "Unknown claim type")
                require(span(answer, claim["start"], claim["end"], "claim") == claim["text"],
                        "Claim text is not in exact answer")
                require(claim["support"] in {"FULL", "PARTIAL", "NONE", "CONTRADICTED", "NOT_ASSESSED"},
                        "Invalid support judgment")
                ids = claim["cited_locator_ids"]
                require(len(ids) == len(set(ids)), "Duplicate claim citation")
                for lid in ids:
                    require(lid in self.inventory.locators, "Unknown cited locator")
                    require(self.inventory.contains(self.resolved[oid]["ranges"], lid),
                            "Claim citation not present in serialized context")
                if claim["support"] in {"FULL", "PARTIAL"}:
                    require(ids, "Supported claim has no original evidence citation")
                self.validate_qualifiers(claim.get("qualifiers", {}))
                self.inputs.reviewed(claim)
            facets = unique(review.get("facets", []), "aspect_id")
            for aspect, row in facets.items():
                require(aspect in self.tasks[tid]["aspects"], "Unknown answer aspect")
                require(row["verdict"] in {"CORRECT_SUFFICIENT", "INCORRECT", "INSUFFICIENT", "NOT_ASSESSED"},
                        "Invalid answer facet verdict")
                require(set(row["claim_ids"]) <= claims.keys(), "Unknown facet claim")
                self.validate_qualifiers(row.get("qualifiers", {}))
                self.inputs.reviewed(row)
            for field, key in (("gaps", "gap_id"), ("conflicts", "conflict_id")):
                unique(review.get(field, []), key)
                expected = self.gaps if field == "gaps" else self.conflicts
                for row in review.get(field, []):
                    require((tid, row[key]) in expected, "Unknown expected gap/conflict")
                    require(type(row["correct"]) in {bool, type(None)}, "Invalid reviewed outcome")
                    self.inputs.reviewed(row)
            behavior = review.get("behavior", {})
            if behavior:
                for key in ("properly_qualified_nonanswer", "invented_answer", "only_refusal"):
                    require(type(behavior.get(key)) in {bool, type(None)}, "Invalid behavior judgment")
                self.inputs.reviewed(behavior)
            if review.get("intent"):
                require(type(review["intent"].get("preserved")) in {bool, type(None)}, "Invalid intent verdict")
                self.inputs.reviewed(review["intent"])

    def validate_qualifiers(self, qualifiers):
        require(set(qualifiers) <= set(QUALIFIERS), "Unknown qualification")
        for row in qualifiers.values():
            require(type(row.get("applicable")) in {bool, type(None)}, "Applicability must be explicit")
            require(row.get("validity") in {"VALID", "INVALID", "NOT_ASSESSED"}, "Invalid qualification verdict")
            self.inputs.reviewed(row)

    def count_tokens(self, text):
        """Actual serialization, with a local pinned tokenizer; never characters-as-tokens."""
        tokenizer = self.manifest["conditions"]["tokenizer"]
        config = self.inputs.json(tokenizer["asset"])
        require(config.get("version") == tokenizer["version"], "Tokenizer version mismatch")
        if tokenizer["algorithm"] == "synthetic-greedy-vocab-v1":
            require(self.manifest["purpose"] == "TECHNICAL_TEST", "Synthetic tokenizer is test-only")
            vocab = config["vocabulary"]
            require(vocab and len(vocab) == len(set(vocab)) and all(vocab), "Invalid vocabulary")
            count = 0
            position = 0
            ordered = sorted(vocab, key=lambda s: (-len(s), s))
            while position < len(text):
                match = next((s for s in ordered if text.startswith(s, position)), None)
                require(match is not None, "Text outside fixed tokenizer vocabulary")
                position += len(match)
                count += 1
            return count
        if tokenizer["algorithm"] == "huggingface-tokenizers-json":
            # Optional installed local package; Tokenizer.from_str does not download anything.
            from importlib.metadata import PackageNotFoundError, version
            try:
                installed = version("tokenizers")
            except PackageNotFoundError:
                return None
            require(installed == tokenizer["library_version"], "Tokenizer library version mismatch")
            from tokenizers import Tokenizer
            value = Tokenizer.from_str(self.inputs.text(config["tokenizer_json_asset"]))
            require(type(tokenizer.get("add_special_tokens")) is bool, "Special token rule required")
            return len(value.encode(text, add_special_tokens=tokenizer["add_special_tokens"]).ids)
        return None

    def validate_resources(self):
        self.usage = {}
        used = set()
        for oid, output in self.outputs.items():
            ids = output["invocation_ids"]
            require(len(ids) == len(set(ids)) and set(ids) <= self.ledger.keys(), "Invalid invocation inventory")
            totals = {"input_tokens": 0, "output_tokens": 0}
            context_count = self.count_tokens(self.resolved[oid]["context"])
            known = context_count is not None
            answer_call = False
            for iid in ids:
                row = self.ledger[iid]
                require(row["output_id"] == oid, "Invocation associated with wrong output")
                require(iid not in used, "Invocation charged to multiple outputs")
                used.add(iid)
                for key in ("stage", "operation", "provider", "model", "revision", "hardware",
                            "boundaries", "cache_state", "parallelism"):
                    nonempty(row.get(key), "resource " + key)
                require(row["stage"] in {"BUILD", "UPDATE", "REQUEST", "ACQUISITION"}, "Invalid resource stage")
                require(row["snapshot_sha256"] == output["source_snapshot_sha256"], "Resource snapshot mismatch")
                sha(row["prompt_sha256"])
                for key in ("elapsed_ms", "cost", "storage_bytes"):
                    finite_usage(row.get(key), key)
                if row.get("cost") is not None:
                    nonempty(row.get("currency"), "cost currency")
                count_in = count_out = None
                if row.get("input_asset") is not None and row.get("output_asset") is not None:
                    actual_input = self.inputs.text(row["input_asset"])
                    actual_output = self.inputs.text(row["output_asset"])
                    count_in = self.count_tokens(actual_input)
                    count_out = self.count_tokens(actual_output)
                    if row["operation"] == "ANSWER":
                        require(not answer_call, "Multiple answer invocations need an explicit final-answer export")
                        answer_call = True
                        context = self.resolved[oid]["context"]
                        a, b = row["context_start"], row["context_end"]
                        require(type(a) is int and type(b) is int and 0 <= a <= b <= len(actual_input),
                                "Invalid context embedding offsets")
                        require(actual_input[a:b] == context, "Measured prompt omits/changes serialized metadata")
                        require(actual_output == self.inputs.text(output["answer_asset"]), "Answer invocation mismatch")
                        require(all(row[k] == output["answer_component"][k]
                                    for k in ("provider", "model", "revision", "prompt_sha256")),
                                "Answer ledger component mismatch")
                for key, count in (("input_tokens", count_in), ("output_tokens", count_out)):
                    if row.get(key) is not None:
                        integer(row[key], key)
                        if count is not None:
                            require(row[key] == count, "Recorded tokens differ from pinned tokenizer")
                    if row["stage"] in {"REQUEST", "ACQUISITION"}:
                        if count is None:
                            known = False
                        else:
                            totals[key] += count
                self.resource_rows.append({**row, "measured_input_tokens": count_in,
                                           "measured_output_tokens": count_out,
                                           "token_NA_reason": None if count_in is not None and count_out is not None
                                           else "UNAVAILABLE_TOKENIZER_OR_SERIALIZED_IO"})
            known = known and answer_call and output.get("resource_inventory_complete") is True
            limits = self.manifest["conditions"]["limits"]
            if context_count is not None:
                require(context_count <= limits["context_tokens"], "Serialized context exceeds token budget")
            if known:
                require(totals["input_tokens"] <= limits["total_input_tokens"], "All-call input budget exceeded")
                require(totals["output_tokens"] <= limits["output_tokens"], "All-call output budget exceeded")
            self.usage[oid] = {**totals, "context_tokens": context_count, "complete": known}
        require(used == self.ledger.keys(), "Unassigned resource invocation; no hidden extra calls")

    def quality_gate(self, tid, metric, oid):
        if self.status == "DIAGNOSTIC":
            return "DIAGNOSTIC_REFERENCE_NOT_RELEASED_OR_POSTHOC"
        if tid not in self.admitted:
            return "TASK_OUTSIDE_RELEASED_REFERENCE"
        if metric not in self.release.get("allowed_metrics", []):
            return "METRIC_NOT_RELEASED_IN_SCOPE"
        if not self.usage[oid]["complete"]:
            return "RESOURCE_OR_TOKEN_ACCOUNTING_INCOMPLETE"
        return None

    def emit(self, oid, metric, numerator=None, denominator=None, reason=None, judged=None,
             expected=None, **extra):
        output = self.outputs[oid]
        tid = output["task_id"]
        if metric in QUALITY:
            reason = self.quality_gate(tid, metric, oid) or reason
        if not reason and (denominator is None or denominator == 0):
            reason = "ZERO_OR_UNKNOWN_DENOMINATOR"
        # Do not leak quality counts from unverified or unreleased judgments.
        if metric in QUALITY and self.quality_gate(tid, metric, oid):
            numerator = denominator = None
        row = {
            "run_id": self.manifest["run_id"], "output_id": oid, "task_id": tid,
            "case_id": self.tasks[tid]["case_id"], "variant_id": output["variant_id"],
            "experiment": output["experiment"], "metric_id": metric,
            "value": None if reason else numerator / denominator,
            "numerator": numerator, "denominator": denominator,
            "judged_count": judged, "expected_count": expected,
            "judgment_coverage": judged / expected if expected else None,
            "NA_reason": reason, "source_scope_id": output["source_scope_id"],
            "status": self.status, "purpose": self.manifest["purpose"],
            "study_origin": self.manifest["study_origin"],
            "components": {k: output[k] for k in ("retrieval_component_id", "representation_id",
                                                  "answer_component", "source_snapshot_sha256")},
            **extra,
        }
        self.rows.append(row)

    def documented(self, tid):
        rows = [r for (t, _), r in self.facets.items() if t == tid and r["finding"] == "DOCUMENTED"]
        return [r for r in rows if self.inputs.independently_reviewed(r)]

    def bundle_ready(self, facet):
        ids = facet.get("bundle_ids", [])
        return bool(ids) and all(self.inputs.independently_reviewed(self.bundles[b]) for b in ids)

    def qualifiers_complete(self, row):
        q = row.get("qualifiers", {})
        return set(q) == set(QUALIFIERS) and all(
            self.inputs.reviewed(v) and v.get("applicable") is not None
            and (not v["applicable"] or v["validity"] != "NOT_ASSESSED") for v in q.values())

    def qualifiers_valid(self, row):
        return self.qualifiers_complete(row) and all(
            not v["applicable"] or v["validity"] == "VALID" for v in row["qualifiers"].values())

    def correct_facets(self, oid):
        output = self.outputs[oid]
        docs = self.documented(output["task_id"])
        review = self.reviews.get(oid, {})
        rows = {r["aspect_id"]: r for r in review.get("facets", [])}
        claims = {r["claim_id"]: r for r in review.get("claims", [])}
        complete = self.inputs.reviewed(review) and review.get("facet_inventory_complete") is True
        count = judged = 0
        for facet in docs:
            row = rows.get(facet["aspect_id"], {})
            ready = (self.inputs.reviewed(row) and row.get("verdict") != "NOT_ASSESSED"
                     and self.qualifiers_complete(row))
            judged += bool(ready)
            complete = complete and ready
            linked = [claims[c] for c in row.get("claim_ids", [])]
            if row.get("verdict") == "CORRECT_SUFFICIENT":
                complete = complete and bool(linked) and all(
                    self.inputs.reviewed(c) and c["support"] != "NOT_ASSESSED"
                    and self.qualifiers_complete(c) for c in linked)
            fully_supported = bool(linked) and all(
                self.inputs.reviewed(c) and c["support"] == "FULL" and self.qualifiers_valid(c)
                for c in linked)
            count += bool(ready and row["verdict"] == "CORRECT_SUFFICIENT"
                          and self.qualifiers_valid(row) and fully_supported)
        return count, len(docs), judged, bool(complete)

    def compute(self):
        for oid, output in self.outputs.items():
            tid = output["task_id"]
            state = self.resolved[oid]
            k = self.manifest["conditions"]["k"]
            ranked = state["ranked"][:k]
            review = self.reviews.get(oid, {})
            admitted_locs = {lid for lid, loc in self.inventory.locators.items()
                             if loc["source_scope_id"] == output["source_scope_id"]}
            admitted_locs.update(lid for c in review.get("claims", []) for lid in c["cited_locator_ids"])
            self.emit(oid, "M01", len(admitted_locs), len(admitted_locs), judged=len(admitted_locs),
                      expected=len(admitted_locs))
            judged = sum(self.judged(tid, uid) for uid in ranked)
            self.emit(oid, "M02", judged, len(ranked), judged=judged, expected=len(ranked),
                      empty_context=not state["ranges"], empty_ranking=not ranked)
            scope = self.scope_units(tid)
            complete = self.scope_reviewed(tid) and all(
                self.judged(tid, u) and self.inputs.independently_reviewed(self.relevance[tid, u]) for u in scope)
            dcg = ideal = None
            if complete:
                gains = [self.relevance[tid, u]["grade"] for u in ranked]
                best = sorted((self.relevance[tid, u]["grade"] for u in scope), reverse=True)[:k]
                dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains))
                ideal = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(best))
            self.emit(oid, "M03", dcg, ideal, reason=None if complete else "INCOMPLETE_SCOPE_OR_GRADES",
                      judged=sum(self.judged(tid, u) for u in scope), expected=len(scope))
            docs = self.documented(tid)
            ready = all(self.bundle_ready(f) for f in docs)
            covered = sum(any(all(self.inventory.contains(state["ranges"], lid)
                                  for lid in self.bundles[bid]["required_locator_ids"])
                              for bid in f["bundle_ids"]) for f in docs) if ready else None
            self.emit(oid, "M04", covered, len(docs), reason=None if ready else "MISSING_BUNDLE_JUDGMENTS",
                      judged=sum(self.inputs.independently_reviewed(self.facets.get((tid, a), {}))
                                 for a in self.tasks[tid]["aspects"]), expected=len(self.tasks[tid]["aspects"]),
                      full_task_aspect_count=len(self.tasks[tid]["aspects"]))
            count, total, assessed, complete = self.correct_facets(oid)
            self.emit(oid, "M05", count if complete else None, total,
                      reason=None if complete else "UNREVIEWED_REQUIRED_ANSWER_ASPECTS",
                      judged=assessed, expected=total)
            claims = [c for c in review.get("claims", []) if c["claim_type"] == "FACTUAL"]
            inventory_ready = self.inputs.reviewed(review) and review.get("claim_inventory_complete") is True
            claim_ready = (inventory_ready and all(self.inputs.reviewed(c) and c["support"] != "NOT_ASSESSED" for c in claims))
            reason = None if claim_ready else "INCOMPLETE_FACTUAL_CLAIM_INVENTORY_OR_SUPPORT"
            n_reviewed = sum(self.inputs.reviewed(c) and c["support"] != "NOT_ASSESSED" for c in claims)
            for metric, labels in (("M06", {"FULL"}), ("M07", {"NONE", "CONTRADICTED"})):
                self.emit(oid, metric, sum(c["support"] in labels for c in claims) if claim_ready else None,
                          len(claims) if inventory_ready else None, reason=reason, judged=n_reviewed,
                          expected=len(claims) if inventory_ready else None,
                          observed_exported_claim_count=len(claims),
                          partial_claim_count=sum(c["support"] == "PARTIAL" for c in claims)
                          if claim_ready else None)
            qualifier_ready = claim_ready and self.scope_reviewed(tid) and all(
                self.qualifiers_complete(c) for c in claims)
            quals = [q for c in claims for q in c.get("qualifiers", {}).values() if q.get("applicable")]
            self.emit(oid, "M08", sum(q["validity"] == "VALID" for q in quals) if qualifier_ready else None,
                      len(quals) if qualifier_ready else None,
                      reason=None if qualifier_ready else "UNREVIEWED_REFERENCE_OR_QUALIFICATIONS",
                      judged=sum(self.inputs.reviewed(q) for c in claims for q in c.get("qualifiers", {}).values()),
                      expected=len(claims) * len(QUALIFIERS) if inventory_ready else None)
            for metric, field, index, key in (("M09", "gaps", self.gaps, "gap_id"),
                                               ("M12", "conflicts", self.conflicts, "conflict_id")):
                expected = [r for (t, _), r in index.items() if t == tid]
                actual = {r[key]: r for r in review.get(field, [])}
                ready = self.scope_reviewed(tid) and self.inputs.reviewed(review) and all(
                    self.inputs.independently_reviewed(r) and self.inputs.reviewed(actual.get(r[key], {}))
                    and actual[r[key]].get("correct") is not None for r in expected)
                self.emit(oid, metric, sum(actual[r[key]]["correct"] for r in expected) if ready else None,
                          len(expected), reason=None if ready else "INCOMPLETE_EXPECTED_OR_RESPONSE_JUDGMENTS",
                          judged=sum(self.inputs.reviewed(actual.get(r[key], {})) for r in expected),
                          expected=len(expected))
            task_review = self.task_reviews.get(tid, {})
            behavior = review.get("behavior", {})
            ready = (self.inputs.reviewed(review) and self.inputs.reviewed(behavior)
                     and self.scope_reviewed(tid))
            for metric, answerability in (("M10", "UNSUPPORTED_IN_SCOPE"), ("M11", "FULL")):
                eligible = int(task_review.get("answerability") == answerability and self.scope_reviewed(tid))
                fields = ("properly_qualified_nonanswer", "invented_answer") if metric == "M10" else ("only_refusal",)
                known = ready and all(behavior.get(f) is not None for f in fields)
                value = ((behavior.get("properly_qualified_nonanswer") and not behavior.get("invented_answer"))
                         if metric == "M10" else behavior.get("only_refusal"))
                self.emit(oid, metric, int(bool(value)) if known and eligible else None, eligible,
                          reason=None if known or not eligible else "UNREVIEWED_RESPONSE_BEHAVIOR",
                          judged=int(bool(known and eligible)), expected=eligible,
                          answerability_stratum=task_review.get("answerability", "UNASSESSED"))
            intent = review.get("intent", {})
            ready = (output["experiment"] == "V2" and self.inputs.independently_reviewed(self.intents.get(tid, {}))
                     and self.inputs.reviewed(review) and self.inputs.reviewed(intent)
                     and intent.get("preserved") is not None
                     and timestamp(self.intents[tid]["recorded_at"], "intent") <=
                     timestamp(output["produced_at"], "output"))
            self.emit(oid, "M13", int(intent["preserved"]) if ready else None, 1,
                      reason=None if ready else "MISSING_INTENT_CONTEXT_OR_REVIEW_OR_NOT_V2")
            self.incremental(oid)
            usage = self.usage[oid]
            for entry in self.resource_rows:
                if entry["output_id"] != oid:
                    continue
                for measure, field in (("input_tokens", "measured_input_tokens"),
                                       ("output_tokens", "measured_output_tokens"),
                                       ("elapsed_ms", "elapsed_ms"), ("cost", "cost"),
                                       ("storage_bytes", "storage_bytes")):
                    value = entry.get(field)
                    self.emit(oid, "M15", value, 1,
                              reason=None if value is not None else "UNMEASURED_RESOURCE",
                              invocation_id=entry["invocation_id"], resource_stage=entry["stage"],
                              measure=measure, currency=entry.get("currency"),
                              resource_inventory_complete=usage["complete"])
            self.emit(oid, "M15", usage["context_tokens"], 1,
                      reason=None if usage["context_tokens"] is not None else "UNAVAILABLE_TOKENIZER",
                      measure="serialized_context_tokens", resource_stage="REQUEST",
                      resource_inventory_complete=usage["complete"])
            for measure in ("input_tokens", "output_tokens"):
                self.emit(oid, "M15", usage[measure] if usage["complete"] else None, 1,
                          reason=None if usage["complete"] else "INCOMPLETE_INVOCATION_INVENTORY",
                          measure="all_request_and_acquisition_" + measure, resource_stage="REQUEST_AND_ACQUISITION")
            self.national(oid)
        for tid, variant, experiment in self.missing_output_cells:
            for metric in METRICS:
                self.rows.append({"run_id": self.manifest["run_id"], "output_id": None,
                                  "task_id": tid, "case_id": self.tasks[tid]["case_id"],
                                  "variant_id": variant, "experiment": experiment, "metric_id": metric,
                                  "value": None, "numerator": None, "denominator": None,
                                  "judged_count": 0, "expected_count": None, "judgment_coverage": None,
                                  "NA_reason": "MISSING_OUTPUT", "status": self.status,
                                  "source_scope_id": self.tasks[tid]["source_scope_id"],
                                  "purpose": self.manifest["purpose"], "study_origin": self.manifest["study_origin"],
                                  "components": {"answer_component": self.manifest["conditions"]["answer_component"]}})
        return self.report()

    def incremental(self, oid):
        output = self.outputs[oid]
        before_id = output.get("before_output_id")
        ready = False
        numerator = None
        if output["experiment"] == "V3_AFTER" and before_id:
            require(before_id in self.outputs, "Unknown V3 baseline")
            before = self.outputs[before_id]
            require(before["experiment"] == "V3_BEFORE" and before["task_id"] == output["task_id"]
                    and before["variant_id"] == output["variant_id"]
                    and before["comparison_id"] == output["comparison_id"], "Unpaired V3 task")
            require(timestamp(before["produced_at"], "before") <= timestamp(output["produced_at"], "after"),
                    "V3 before/after chronology reversed")
            before_stats = self.correct_facets(before_id)
            after_stats = self.correct_facets(oid)
            acquisition = [self.ledger[i] for i in output["invocation_ids"]
                           if self.ledger[i]["stage"] == "ACQUISITION"]
            budget = output.get("acquisition_budget")
            require(type(budget) is int and budget >= 0, "V3 acquisition budget missing")
            for item in acquisition:
                integer(item.get("responses"), "acquisition responses")
            require(sum(i["responses"] for i in acquisition) <= budget, "V3 acquisition budget exceeded")
            ready = (before_stats[3] and after_stats[3] and before_stats[1] > 0
                     and before_stats[1] == after_stats[1] and bool(acquisition)
                     and self.usage[before_id]["complete"])
            if ready:
                numerator = after_stats[0] - before_stats[0]
        self.emit(oid, "M14", numerator, 1, reason=None if ready else "MISSING_PAIRED_REVIEW_OR_ACQUISITION_LEDGER")

    def national(self, oid):
        # Separate named stages; no automatic inference from registry presence.
        national = self.references.get("national")
        if not national:
            self.emit(oid, "M16", None, None, reason="NO_DATED_NATIONAL_FRAME")
            return
        frame = self.inputs.json(national["frame_asset"])
        require(frame.get("frame_date") and frame.get("identity_rules"), "National frame metadata missing")
        ids = frame["municipality_ids"]
        require(len(ids) == len(set(ids)) and all(re.fullmatch(r"\d{8}", a) for a in ids),
                "Invalid national frame IDs")
        ready = self.inputs.independently_reviewed(national)
        for stage in ("IDENTIFIED", "ATTEMPTED", "SOURCE_AVAILABLE", "TASK_REVIEWED"):
            reached = national.get("stages", {}).get(stage)
            if reached is not None:
                require(len(reached) == len(set(reached)) and set(reached) <= set(ids), "Invalid national stage set")
            self.emit(oid, "M16", len(reached) if ready and reached is not None else None, len(ids),
                      reason=None if ready and reached is not None else "UNKNOWN_OR_UNREVIEWED_STAGE_COUNT",
                      national_stage=stage, frame_date=frame["frame_date"])

    def report(self):
        missing = {tid: [a for a in task["aspects"]
                         if not self.inputs.independently_reviewed(self.facets.get((tid, a), {}))
                         or self.facets[tid, a]["finding"] == "NOT_ASSESSED"]
                   for tid, task in self.tasks.items()}
        return {
            "schema_version": "offline-evaluation-result/1", "run_id": self.manifest["run_id"],
            "status": self.status, "purpose": self.manifest["purpose"],
            "study_origin": self.manifest["study_origin"], "prospectivity_certified": False,
            "scientific_truth_or_blinding_verified": False,
            "excluded_tasks": sorted(set(self.tasks) - set(self.admitted)) if self.release_ok else sorted(self.tasks),
            "missing_output_cells": [{"task_id": t, "variant_id": v, "experiment": e}
                                     for t, v, e in self.missing_output_cells],
            "missing_aspect_judgments": missing, "split_disclosures": self.overlaps,
            "source_frames": list(self.inventory.scopes.values()),
            "release_document_validated": self.release_ok,
            "fully_negative_reference": bool(self.admitted) and all(
                self.task_reviews.get(t, {}).get("answerability") == "UNSUPPORTED_IN_SCOPE" for t in self.admitted),
            "no_architecture_winner_inferred": True,
            "rows": self.rows, "resources": self.resource_rows,
            "resolved_selections": self.resolved,
            "citation_links": {oid: [{"claim_id": c["claim_id"], "locator_ids": c["cited_locator_ids"]}
                                     for c in r.get("claims", [])] for oid, r in self.reviews.items()},
        }
