"""Hash-bound inputs and explicit original-text identity. Standard library only."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit


class ValidationError(ValueError):
    """Input cannot be evaluated without guessing or changing its identity."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "Invalid SHA-256")
    return value


def nonempty(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"Missing {name}")
    return value


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum, f"Invalid integer {name}")
    return value


def timestamp(value, name):
    nonempty(value, name)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid timestamp: {name}") from exc
    require(result.tzinfo is not None, f"Timezone required: {name}")
    return result


def unique(rows, key):
    require(isinstance(rows, list), f"Expected list: {key}")
    result = {}
    for row in rows:
        require(isinstance(row, dict), f"Expected object: {key}")
        value = nonempty(row.get(key), key)
        require(value not in result, f"Duplicate/ambiguous {key}: {value}")
        result[value] = row
    return result


def json_bytes(data):
    def pairs(items):
        obj = {}
        for key, value in items:
            require(key not in obj, f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def invalid(value):
        raise ValidationError(f"Nonfinite JSON number: {value}")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError(f"Invalid JSON: {exc}") from exc


class Inputs:
    """Read every declared asset once. The externally supplied manifest hash is mandatory."""

    def __init__(self, path: Path, expected_sha256: str):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.manifest_bytes = self.path.read_bytes()
        require(digest(self.manifest_bytes) == sha(expected_sha256), "Manifest changed after freeze")
        self.manifest = json_bytes(self.manifest_bytes)
        m = self.manifest
        require(m.get("schema_version") == "offline-evaluation/1", "Unsupported schema version")
        nonempty(m.get("run_id"), "run_id")
        require(m.get("purpose") in {"TECHNICAL_TEST", "RESEARCH_DIAGNOSTIC", "RESEARCH_EVALUATION"},
                "Explicit run purpose required")
        require(m.get("study_origin") in {"SYNTHETIC", "NEW_PROTOCOL", "POSTHOC_E2"},
                "Explicit study origin required")
        require(not m.get("claim_prospective", False),
                "Adapter cannot certify prospectivity; POSTHOC_E2 cannot be relabelled")
        require((m["purpose"] == "TECHNICAL_TEST") == (m["study_origin"] == "SYNTHETIC"),
                "Synthetic fixtures must be marked TECHNICAL_TEST")
        require(m.get("export_source", {}).get("kind") == "EXPLICIT_FILES",
                "Explicit file export required; no database or implicit fallback")
        require(m["export_source"].get("inventory_asset") == m.get("files", {}).get("inventory"),
                "Export source is not bound to inventory")
        self.assets = {}
        self.paths = {}
        require(isinstance(m.get("assets"), dict) and m["assets"], "No frozen assets")
        for name, spec in m["assets"].items():
            relative = Path(nonempty(spec.get("path"), "asset path"))
            require(not relative.is_absolute() and ".." not in relative.parts,
                    "Asset path must stay inside input package")
            path = (self.root / relative).resolve()
            require(path.is_relative_to(self.root) and path.is_file(), f"Missing/escaping asset {name}")
            require(path not in self.paths.values(), "Same file declared twice")
            data = path.read_bytes()
            require(digest(data) == sha(spec.get("sha256")), f"Frozen asset changed: {name}")
            self.assets[name] = data
            self.paths[name] = path
        self.audit_cache = {}

    def data(self, asset):
        require(asset in self.assets, f"Undeclared asset: {asset}")
        return self.assets[asset]

    def json(self, asset):
        return json_bytes(self.data(asset))

    def text(self, asset):
        try:
            return self.data(asset).decode("utf-8")
        except UnicodeError as exc:
            raise ValidationError(f"Non-UTF-8 text asset: {asset}") from exc

    def reviewed(self, obj):
        """Verify a record-bound audit document, never infer independent scientific truth."""
        review = obj.get("review", {})
        require(review.get("status", "NOT_ASSESSED") in
                {"NOT_ASSESSED", "IN_REVIEW", "ADJUDICATED"}, "Invalid review status")
        if review.get("status") != "ADJUDICATED" or not review.get("audit_asset"):
            return False
        payload = {k: v for k, v in obj.items() if k != "review"}
        audit = self.json(review["audit_asset"])
        require(audit.get("object_sha256") == digest(canonical(payload)), "Audit/record mismatch")
        for field in ("reviewer_id", "reviewed_at", "reason", "procedure", "prior_exposure"):
            nonempty(audit.get(field), f"audit {field}")
        try:
            require(datetime.fromisoformat(audit["reviewed_at"]).tzinfo is not None,
                    "Audit timestamp needs timezone")
        except ValueError as exc:
            raise ValidationError("Invalid review timestamp") from exc
        require(type(audit.get("independent_of_rankings")) is bool, "Audit exposure declaration missing")
        self.audit_cache[review["audit_asset"]] = audit
        return True

    def independently_reviewed(self, obj):
        return self.reviewed(obj) and self.audit_cache[obj["review"]["audit_asset"]][
            "independent_of_rankings"]

    def unchanged(self):
        require(self.path.read_bytes() == self.manifest_bytes, "Manifest mutated during evaluation")
        for name, path in self.paths.items():
            require(path.read_bytes() == self.assets[name], f"Input mutated during evaluation: {name}")


def span(text, start, end, label):
    integer(start, f"{label}.start")
    integer(end, f"{label}.end", 1)
    require(start < end <= len(text), f"Invalid Unicode offsets: {label}")
    return text[start:end]


def snapshot_key(row):
    fields = ("database_snapshot_id", "evidence_id", "source_type", "source_id", "segment_id",
              "document_id")
    return tuple(nonempty(row.get(k), f"snapshot {k}") for k in fields)


class Inventory:
    def __init__(self, inputs: Inputs, data):
        self.inputs = inputs
        self.sources = unique(data["sources"], "source_id")
        self.units = unique(data["units"], "unit_id")
        self.locators = unique(data["locators"], "locator_id")
        self.scopes = unique(data["scopes"], "source_scope_id")
        identities = set()
        for source in self.sources.values():
            sha(source["body_sha256"])
            require(re.fullmatch(r"\d{8}", source["acquisition_ags"]), "Invalid acquisition AGS")
            require(urlsplit(source["url"]).scheme in {"http", "https"}
                    and urlsplit(source["url"]).netloc, "Invalid source URL")
            nonempty(source.get("family_id"), "document version family")
            require(digest(inputs.data(source["raw_asset"])) == source["body_sha256"],
                    "Original source body hash mismatch")
            identity = (source["acquisition_ags"], source["url"], source["body_sha256"])
            require(identity not in identities, "Duplicate source version identity")
            identities.add(identity)
        for unit in self.units.values():
            require(unit["source_id"] in self.sources, "Unit has unknown source")
            nonempty(unit.get("text"), "canonical parser text")
            require("page_ref" in unit, "Explicit page_ref (or null) required")
        # Canonical units must be copied exactly from an explicit frozen parser export,
        # not merely asserted in a hand-written mapping next to a source hash.
        exports = data.get("canonical_exports", [])
        require(exports, "Explicit canonical parser export required")
        exported = {}
        for export in exports:
            require(export["kind"] in {"E2", "CANONICAL_PARSER_V1"}, "Unknown canonical export form")
            catalog = [json_bytes(line) for line in inputs.data(export["catalog_asset"]).splitlines() if line.strip()]
            versions = set()
            for row in catalog:
                key = (row["ags"], row["url"], sha(row["body_sha256"]))
                require(key not in versions, "Duplicate original catalog version")
                versions.add(key)
            for line in inputs.data(export["corpus_asset"]).splitlines():
                if not line.strip():
                    continue
                row = json_bytes(line)
                uid = row["unit_id"]
                require(uid not in exported, "Ambiguous canonical export unit ID")
                require(row.get("raw_hashes") and all(
                    (row["acquisition_ags"], row["source_url"], sha(h)) in versions
                    for h in row["raw_hashes"]), "Canonical export absent from original catalog")
                exported[uid] = row
        for uid, unit in self.units.items():
            require(uid in exported, "Unit absent from frozen canonical export")
            row = exported[uid]
            source = self.sources[unit["source_id"]]
            require(row["text"] == unit["text"] and row.get("page_ref") == unit["page_ref"]
                    and row["source_url"] == source["url"]
                    and row["acquisition_ags"] == source["acquisition_ags"]
                    and row["raw_hashes"] == [source["body_sha256"]],
                    "Canonical export text/source mismatch or composite unit needs explicit splitting")
        for scope in self.scopes.values():
            ids = scope["source_ids"]
            require(isinstance(ids, list) and len(ids) == len(set(ids)), "Duplicate scope source")
            require(set(ids) <= self.sources.keys(), "Unknown scope source")
            nonempty(scope.get("description"), "source scope description")
        seen_spans = set()
        for loc in self.locators.values():
            require(loc["unit_id"] in self.units, "Unknown canonical unit")
            unit = self.units[loc["unit_id"]]
            source = self.sources[unit["source_id"]]
            require(loc["source_scope_id"] in self.scopes, "Unknown locator scope")
            require(source["source_id"] in self.scopes[loc["source_scope_id"]]["source_ids"],
                    "Locator outside declared source scope")
            for field, value in (("url", source["url"]), ("body_sha256", source["body_sha256"]),
                                 ("acquisition_ags", source["acquisition_ags"]),
                                 ("page_ref", unit["page_ref"])):
                require(loc.get(field) == value, f"Locator {field} mismatch")
            require(span(unit["text"], loc["start"], loc["end"], loc["locator_id"]) == loc["quote"],
                    "Locator quote mismatch")
            identity = (loc["source_scope_id"], loc["unit_id"], loc["start"], loc["end"])
            require(identity not in seen_spans, "Duplicate canonical locator")
            seen_spans.add(identity)
        self.mappings = {}
        for mapping in data.get("snapshot_mappings", []):
            key = snapshot_key(mapping)
            require(key not in self.mappings, "Ambiguous snapshot mapping")
            require(mapping["unit_id"] in self.units, "Unknown mapped unit")
            unit = self.units[mapping["unit_id"]]
            source = self.sources[unit["source_id"]]
            require(mapping["body_sha256"] == source["body_sha256"]
                    and mapping["url"] == source["url"], "Snapshot source-version mismatch")
            require(span(unit["text"], mapping["start"], mapping["end"], "mapping") ==
                    mapping["evidence_text"], "Snapshot text/offset mismatch")
            original = inputs.json(mapping["snapshot_export_asset"])
            original_key = {**original, "document_id": original.get("document", {}).get("document_id")}
            require(snapshot_key(original_key) == key, "Original snapshot identity mismatch")
            require(original.get("evidence_text") == mapping["evidence_text"]
                    and original.get("document", {}).get("source_url") == mapping["url"],
                    "Original snapshot export text/source mismatch")
            self.mappings[key] = mapping

    def resolve(self, view):
        """No URL/text matching fallback, even when text is identical."""
        if view.get("kind") == "E2":
            require(view["unit_id"] in self.units, "Unknown E2 canonical unit")
            unit = self.units[view["unit_id"]]
            start, end = view["start"], view["end"]
            span(unit["text"], start, end, "E2 selection")
            return view["unit_id"], start, end
        require(view.get("kind") == "SNAPSHOT", "Unknown export identity kind")
        key = snapshot_key(view)
        require(key in self.mappings, "No explicit snapshot-to-original mapping")
        mapping = self.mappings[key]
        require(view["evidence_text"] == mapping["evidence_text"], "Snapshot evidence changed")
        return mapping["unit_id"], mapping["start"], mapping["end"]

    def contains(self, ranges, locator_id):
        loc = self.locators[locator_id]
        # Adjacent selected slices may jointly preserve one original quote.
        intervals = sorted((a, b) for unit, a, b in ranges if unit == loc["unit_id"])
        reached = loc["start"]
        for start, end in intervals:
            if start <= reached:
                reached = max(reached, end)
        return reached >= loc["end"]


def validate_tasks(inputs, data, inventory):
    require(data.get("status") == "FROZEN", "DRAFT task cards are not reference tasks")
    tasks = unique(data["tasks"], "task_id")
    require(tasks, "No frozen tasks")
    for task in tasks.values():
        for key in ("case_id", "information_need", "domain", "question_variant_id"):
            nonempty(task.get(key), key)
        require(re.fullmatch(r"\d{8}", task["municipality_id"]), "Invalid task municipality")
        try:
            date.fromisoformat(task["cutoff_date"])
        except ValueError as exc:
            raise ValidationError("Invalid task cutoff") from exc
        require(task["split"] in {"DEVELOPMENT", "TEST"}, "Invalid split")
        require(task["source_scope_id"] in inventory.scopes, "Unknown task scope")
        require(task["aspects"] and len(task["aspects"]) == len(set(task["aspects"])),
                "Missing/duplicate predefined aspects")
        require(task.get("known_exposure") in {"NONE_DECLARED", "DEVELOPMENT", "E2_POSTHOC"},
                "Task exposure declaration required")
        if task["known_exposure"] == "E2_POSTHOC":
            require(inputs.manifest["study_origin"] == "POSTHOC_E2", "Known E2 is post-hoc")
    disclosures = inputs.manifest.get("split_disclosures", [])
    overlaps = []
    values = list(tasks.values())
    for i, left in enumerate(values):
        for right in values[i + 1:]:
            if left["case_id"] == right["case_id"]:
                require(left["split"] == right["split"], "Case language variants cross split")
                require(left["municipality_id"] == right["municipality_id"]
                        and left["cutoff_date"] == right["cutoff_date"],
                        "Case ID reused for different municipality/time")
                continue
            ls = [inventory.sources[s] for s in inventory.scopes[left["source_scope_id"]]["source_ids"]]
            rs = [inventory.sources[s] for s in inventory.scopes[right["source_scope_id"]]["source_ids"]]
            shared = {f"{key}:{s[key]}" for s in ls for t in rs
                      for key in ("body_sha256", "url", "family_id") if s[key] == t[key]}
            if left["municipality_id"] == right["municipality_id"]:
                shared.add("municipality:" + left["municipality_id"])
            lt = {digest(u["text"].encode()) for u in inventory.units.values()
                  if u["source_id"] in {s["source_id"] for s in ls}}
            rt = {digest(u["text"].encode()) for u in inventory.units.values()
                  if u["source_id"] in {s["source_id"] for s in rs}}
            shared.update("parser_text:" + h for h in lt & rt)
            if shared:
                pair = {left["case_id"], right["case_id"]}
                matched = [d for d in disclosures if set(d["case_ids"]) == pair
                           and d.get("reason") and d.get("analysis_cluster")]
                require(len(matched) == 1, "Shared source/case requires explicit split disclosure")
                overlaps.append({"case_ids": sorted(pair), "shared": sorted(shared), **matched[0]})
    return tasks, overlaps


def finite_usage(value, name):
    if value is not None:
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                f"Invalid observed usage: {name}")
