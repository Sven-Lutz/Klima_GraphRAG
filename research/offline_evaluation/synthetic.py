"""TECHNICAL TEST FIXTURES ONLY. No municipal observations or research judgments.

python3 -m research.offline_evaluation.synthetic --out /fresh/fixture/directory
"""
from __future__ import annotations

import argparse
import copy
import json
import string
from pathlib import Path

from .contracts import canonical, digest
from .evaluate import METRICS


class SyntheticPackage:
    """Mutable fixture builder; freeze() writes a new, explicitly synthetic package."""

    def __init__(self):
        self.audit_number = 0
        self.audit_assets = {}
        self.assets = {
            "raw": b"Fixture source body. A\nFixture source body. B\nAlternative fixture report.",
            "rubric": b"TECHNICAL TEST ONLY: 0 irrelevant, 1 related, 2 useful, 3 direct fixture.",
            "metric_contract": (Path(__file__).parents[1] / "experiments" /
                                "architecture_evaluation_v02_20261025" / "METRIC_CONTRACT.csv").read_bytes(),
        }
        self.documents = {}
        self.documents["tokenizer"] = {"version": "synthetic-fixture-1", "vocabulary":
                                      list(string.printable) + ["ä", "Fixture", "source", "metadata"]}
        self.documents["tasks"] = {"status": "FROZEN", "tasks": [{
            "task_id": "t1", "case_id": "case1", "municipality_id": "00000001",
            "cutoff_date": "2000-01-01", "information_need": "Explain fictional fixture A and B.",
            "domain": "TECHNICAL_TEST", "question_variant_id": "precise", "split": "TEST",
            "source_scope_id": "scope1", "aspects": ["status", "next"], "known_exposure": "NONE_DECLARED",
        }]}
        body = digest(self.assets["raw"])
        texts = ["Fixture source body. A", "Fixture source body. B", "Alternative fixture report."]
        inv = {
            "sources": [{"source_id": "s1", "url": "https://fixture.invalid/source", "body_sha256": body,
                         "raw_asset": "raw", "acquisition_ags": "00000001", "family_id": "family1"}],
            "units": [{"unit_id": f"u{i}", "source_id": "s1", "text": text, "page_ref": None}
                      for i, text in enumerate(texts, 1)],
            "scopes": [{"source_scope_id": "scope1", "source_ids": ["s1"],
                        "description": "TECHNICAL TEST: one fictional source; not a municipal corpus"}],
            "locators": [{"locator_id": f"l{i}", "unit_id": f"u{i}", "source_scope_id": "scope1",
                          "url": "https://fixture.invalid/source", "body_sha256": body,
                          "acquisition_ags": "00000001", "start": 0, "end": len(text),
                          "quote": text, "page_ref": None} for i, text in enumerate(texts, 1)],
            "snapshot_mappings": [],
            "canonical_exports": [{"kind": "E2", "corpus_asset": "original_corpus",
                                   "catalog_asset": "original_catalog"}],
        }
        self.documents["inventory"] = inv
        self.assets["original_catalog"] = canonical({"ags": "00000001", "url": inv["sources"][0]["url"],
                                                    "body_sha256": body}) + b"\n"
        self.assets["original_corpus"] = b"".join(canonical({
            "unit_id": u["unit_id"], "acquisition_ags": "00000001", "source_url": inv["sources"][0]["url"],
            "raw_hashes": [body], "text": u["text"], "page_ref": u["page_ref"],
            "heading_context": None, "origins": []}) + b"\n" for u in inv["units"])
        self.documents["references"] = {
            "release": self.reviewed({"state": "CLOSED_COMPLETE", "admitted_task_ids": ["t1"],
                                      "allowed_metrics": list(METRICS),
                                      "inclusion_rule": "All fictional fixture aspects and source units",
                                      "review_protocol_asset": "review_protocol"}),
            "tasks": [self.reviewed({"task_id": "t1", "answerability": "FULL",
                                     "scope_complete": True, "reviewed_source_ids": ["s1"]})],
            "facets": [self.reviewed({"task_id": "t1", "aspect_id": "status", "finding": "DOCUMENTED",
                                      "bundle_ids": ["b1", "b2"]}),
                       self.reviewed({"task_id": "t1", "aspect_id": "next", "finding": "DOCUMENTED",
                                      "bundle_ids": ["b3"]})],
            "bundles": [self.reviewed({"bundle_id": "b1", "task_id": "t1", "aspect_id": "status",
                                       "required_locator_ids": ["l1", "l2"]}),
                        self.reviewed({"bundle_id": "b2", "task_id": "t1", "aspect_id": "status",
                                       "required_locator_ids": ["l3"]}),
                        self.reviewed({"bundle_id": "b3", "task_id": "t1", "aspect_id": "next",
                                       "required_locator_ids": ["l2"]})],
            "relevance": [self.reviewed({"task_id": "t1", "unit_id": f"u{i}", "grade": g})
                          for i, g in enumerate([3, 2, 1], 1)],
            "gaps": [], "conflicts": [], "intents": [],
        }
        self.documents["review_protocol"] = {
            "rubric_asset": "rubric", "source_order": "source_id then unit_id, without rankings",
            "second_review_selection_rule": "TECHNICAL TEST: all fixture tasks",
            "prior_exposure_disclosure": "Invented software expectations, not empirical judgments",
            "second_review_task_ids": ["t1"],
        }
        second = self.reviewed({"task_id": "t1", "agreement": "AGREE",
                                "adjudication_note": "TECHNICAL TEST ONLY, no actual human review"})
        self.audit_assets[second["review"]["audit_asset"]]["reviewer_id"] = "FICTIONAL_SECOND_TEST_REVIEWER"
        self.documents["references"]["second_reviews"] = [second]
        component = {"provider": "SYNTHETIC_NO_PROVIDER", "model": "NO_MODEL", "revision": "fixture-1",
                     "prompt_sha256": digest(b"TECHNICAL_TEST_PROMPT")}
        self.documents["outputs"] = [{
            "output_id": "o1", "task_id": "t1", "variant_id": "LEX", "experiment": "V1",
            "produced_at": "2000-01-02T00:00:00+00:00",
            "source_scope_id": "scope1", "source_snapshot_sha256": "filled-at-freeze",
            "answer_component": component, "retrieval_component_id": "fictional-ranking-1",
            "representation_id": "original-parser-text", "comparison_id": "fixture-comparison",
            "context_asset": "context", "answer_asset": "answer", "ranked": [self.view(i) for i in (1, 2, 3)],
            "selected": [], "invocation_ids": ["call1"], "resource_inventory_complete": True,
        }]
        self.assets["answer"] = b"Fixture claim A. Fixture claim B."
        self.documents["answer_reviews"] = [self.reviewed({
            "output_id": "o1", "claim_inventory_complete": True, "facet_inventory_complete": True,
            "claims": [self.reviewed({"claim_id": "c1", "claim_type": "FACTUAL", "start": 0, "end": 16,
                                      "text": "Fixture claim A.", "cited_locator_ids": ["l1"], "support": "FULL",
                                      "qualifiers": self.qualifiers()}),
                       self.reviewed({"claim_id": "c2", "claim_type": "FACTUAL", "start": 17, "end": 33,
                                      "text": "Fixture claim B.", "cited_locator_ids": ["l2"], "support": "FULL",
                                      "qualifiers": self.qualifiers()})],
            "facets": [self.reviewed({"aspect_id": a, "verdict": "CORRECT_SUFFICIENT",
                                      "claim_ids": [c], "qualifiers": self.qualifiers()})
                       for a, c in [("status", "c1"), ("next", "c2")]],
            "gaps": [], "conflicts": [],
            "behavior": self.reviewed({"properly_qualified_nonanswer": False, "invented_answer": False,
                                       "only_refusal": False}),
        })]
        self.documents["resources"] = [{
            "invocation_id": "call1", "output_id": "o1", "stage": "REQUEST", "operation": "ANSWER",
            **component, "hardware": "FICTIONAL_TEST_HARDWARE", "boundaries": "fixture start to end",
            "cache_state": "FIXTURE", "parallelism": "1", "snapshot_sha256": "filled-at-freeze",
            "input_asset": "prompt", "output_asset": "answer", "context_start": 4, "context_end": 0,
            "input_tokens": None, "output_tokens": None, "elapsed_ms": 1, "cost": None,
            "storage_bytes": None,
        }]
        self.manifest = {
            "schema_version": "offline-evaluation/1", "run_id": "TECHNICAL_TEST_ONLY",
            "purpose": "TECHNICAL_TEST", "study_origin": "SYNTHETIC", "claim_prospective": False,
            "export_source": {"kind": "EXPLICIT_FILES", "inventory_asset": "inventory"},
            "files": {k: k for k in ("inventory", "tasks", "references", "outputs", "answer_reviews",
                                      "resources", "metric_contract")},
            "conditions": {"k": 3, "answer_component": component, "rubric_asset": "rubric",
                           "limits": {"context_tokens": 10000, "total_input_tokens": 20000, "output_tokens": 10000},
                           "tokenizer": {"algorithm": "synthetic-greedy-vocab-v1", "version": "synthetic-fixture-1",
                                         "asset": "tokenizer"}}, "split_disclosures": [],
        }
        self.select([1, 2])

    def reviewed(self, row):
        self.audit_number += 1
        asset = f"audit{self.audit_number}"
        row["review"] = {"status": "ADJUDICATED", "audit_asset": asset}
        self.audit_assets[asset] = {
            "reviewer_id": "FICTIONAL_SOFTWARE_TEST_REVIEWER", "reviewed_at": "2000-01-01T00:00:00+00:00",
            "reason": "TECHNICAL TEST EXPECTATION, NOT AN EMPIRICAL JUDGMENT",
            "procedure": "Synthetic input constructed for software acceptance test",
            "prior_exposure": "All fixtures are invented; no blinding or research claim",
            "independent_of_rankings": True,
        }
        return row

    def qualifiers(self):
        return {k: self.reviewed({"applicable": True, "validity": "VALID"})
                for k in ("municipality", "time", "institution")}

    def view(self, number):
        unit = self.documents["inventory"]["units"][number - 1]
        return {"kind": "E2", "unit_id": unit["unit_id"], "start": 0, "end": len(unit["text"])}

    def select(self, numbers):
        output = self.documents["outputs"][0]
        context = "metadata: TECHNICAL_TEST_ONLY\n" if numbers else ""
        selected = []
        for number in numbers:
            text = self.documents["inventory"]["units"][number - 1]["text"]
            start = len(context)
            context += text + "\n"
            selected.append({"selection_id": f"sel{number}", "view": self.view(number),
                             "serialized_start": start, "serialized_end": start + len(text)})
        output["selected"] = selected
        self.assets["context"] = context.encode()
        self.assets["prompt"] = ("SYS\n" + context).encode()
        self.documents["resources"][0]["context_end"] = 4 + len(context)

    def strip_answers(self):
        self.documents["answer_reviews"] = []

    def refresh_audits(self, value):
        if isinstance(value, list):
            for item in value:
                self.refresh_audits(item)
        if isinstance(value, dict):
            for k, item in value.items():
                if k != "review":
                    self.refresh_audits(item)
            review = value.get("review", {})
            if review.get("audit_asset") in self.audit_assets:
                asset = review["audit_asset"]
                audit = copy.deepcopy(self.audit_assets[asset])
                audit["object_sha256"] = digest(canonical({k: v for k, v in value.items() if k != "review"}))
                self.assets[asset] = canonical(audit)

    def freeze(self, root: Path):
        root.mkdir(parents=True, exist_ok=False)
        self.assets["inventory"] = canonical(self.documents["inventory"])
        snapshot = digest(self.assets["inventory"])
        for output in self.documents["outputs"]:
            output["source_snapshot_sha256"] = snapshot
        for row in self.documents["resources"]:
            row["snapshot_sha256"] = snapshot
        for value in self.documents.values():
            self.refresh_audits(value)
        for name, value in self.documents.items():
            self.assets[name] = canonical(value)
        specs = {}
        for name, data in self.assets.items():
            path = name + ".fixture"
            (root / path).write_bytes(data)
            specs[name] = {"path": path, "sha256": digest(data)}
        self.manifest.setdefault("expected_outputs", [
            {"task_id": task["task_id"], "variant_id": variant, "experiment": experiment}
            for task in self.documents["tasks"]["tasks"]
            for variant, experiment in sorted({(o["variant_id"], o["experiment"])
                                               for o in self.documents["outputs"]})])
        self.manifest["assets"] = specs
        data = canonical(self.manifest)
        path = root / "manifest.json"
        path.write_bytes(data)
        (root / "manifest.sha256").write_text(digest(data) + "\n")
        return path, digest(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    path, checksum = SyntheticPackage().freeze(args.out)
    print(json.dumps({"purpose": "TECHNICAL_TEST", "manifest": str(path), "sha256": checksum}))


if __name__ == "__main__":
    main()
