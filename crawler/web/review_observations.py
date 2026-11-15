"""Private evaluation of exact reviewed research answers, outside catalog optimization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from research.gold_studio.store import digest
from research.product_evaluation.metrics import evaluate_case, summarize_results
from research.product_evaluation.review_bridge import reference_from_review_export


class ReviewObservations:
    def __init__(self, directory, snapshot, write_json):
        self.directory = Path(directory)
        self.snapshot = snapshot
        self.write_json = write_json

    def capture(self, payload):
        from crawler.web.evaluation_dashboard import normalize_product_result

        if payload.get("snapshot") != self.snapshot:
            raise ValueError("Research observation belongs to another snapshot")
        answer = payload.get("answer_result") or {}
        if not answer.get("answer_text") or not payload.get("result_id"):
            raise ValueError("An actual persisted research answer is required")
        normalized = normalize_product_result(payload)
        sources = set(normalized["ranked_ids"])
        if any(
            not (set(claim.get("source_ids", [])) | set(claim.get("context_source_ids", [])))
            <= sources
            for claim in normalized["claims"]
        ):
            raise ValueError("Observation claim source is absent from actual retrieval")
        body = dict(
            schema="reviewed-research-observation-v1",
            snapshot_id=self.snapshot,
            research_result_id=payload["result_id"],
            question=payload["retrieval_result"]["query"],
            output=normalized,
            implementation_sha256=(payload.get("implementation") or {}).get("sha256"),
        )
        if len(json.dumps(body).encode()) > 2 * 1024**2:
            raise ValueError("Research observation exceeds existing persistence budget")
        observation_id = "RO1-" + digest(body)
        path = self.directory / "inputs" / (observation_id + ".json")
        if path.exists():
            if self.load(observation_id) != body:
                raise ValueError("Observation identity differs from stored immutable answer")
        else:
            self.write_json(path, body)
        return observation_id

    def load(self, observation_id):
        if not isinstance(observation_id, str) or not re.fullmatch(
            r"RO1-[0-9a-f]{64}", observation_id
        ):
            raise ValueError("Invalid research observation identity")
        body = json.loads((self.directory / "inputs" / (observation_id + ".json")).read_text())
        if "RO1-" + digest(body) != observation_id or body.get("snapshot_id") != self.snapshot:
            raise ValueError("Research observation integrity/snapshot mismatch")
        return body

    def refresh(self, export):
        started_at = datetime.now(timezone.utc).isoformat()
        # Only explicit free-research bindings participate. No catalog row is read,
        # generated, reassigned, optimized or marked as an independent holdout.
        observations = {}
        splits = {}
        for label in export.get("labels", []):
            oid = (label.get("payload") or {}).get("review_observation_id")
            if label.get("task_id") is not None or not oid or label.get("kind") != "claim":
                continue
            if label.get("split") == "independent_holdout":
                raise ValueError("Sealed holdout cannot enter research development observations")
            observations[oid] = self.load(oid)
            if oid in splits and splits[oid] != label["split"]:
                raise ValueError("Observation split binding differs between claims")
            splits[oid] = label["split"]
        questions = [
            dict(
                question_id=oid,
                question=body["question"],
                snapshot_id=self.snapshot,
                split={
                    "human_annotation_candidate": "human_candidates",
                    "adversarial_negative": "adversarial",
                }.get(splits[oid], splits[oid]),
            )
            for oid, body in observations.items()
        ]
        references = (
            reference_from_review_export(
                export,
                questions,
                allow_technical=export.get("technical") is True,
                skip_other_tasks=True,
                bind_observations=True,
            )
            if questions
            else {}
        )
        rows = []
        for oid, ref in sorted(references.items()):
            body = observations[oid]
            rows.append(
                dict(
                    observation_id=oid,
                    question=body["question"],
                    research_result_id=body["research_result_id"],
                    method="reviewed-answer-observation",
                    output=body["output"],
                    metrics=evaluate_case(body["output"], ref),
                    review_provenance=ref["review_provenance"],
                )
            )
        report = dict(
            schema="reviewed-research-evaluation-v1",
            snapshot_id=self.snapshot,
            source_gold_sha256=export["content_sha256"],
            source_gold_version=export["gold_version"],
            technical=export["technical"],
            human_gold=bool(rows) and not export["technical"],
            cases=len(rows),
            results=rows,
            summary=summarize_results(rows),
            inference_invoked=False,
            catalog_modified=False,
            holdout_accessed=False,
            semantics="Cached exact answers; no new inference or new latency measurement",
            run=dict(
                provider="none",
                model="none",
                prompt_version="exact-claim-review-binding-v1",
                parameters={"k": 8},
                seed=None,
                snapshot_id=self.snapshot,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                input_identifiers=[export["content_sha256"], *sorted(observations)],
                output_identifiers=[digest(rows)],
                status="succeeded",
                provider_cost=0,
            ),
        )
        report["content_sha256"] = digest(report)
        self.write_json(self.directory / "reports" / (export["content_sha256"] + ".json"), report)
        return dict(cases=len(rows), source_gold_sha256=export["content_sha256"])

    def report(self, gold_sha256):
        if not isinstance(gold_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", gold_sha256):
            raise ValueError("Invalid Gold snapshot identity")
        report = json.loads((self.directory / "reports" / (gold_sha256 + ".json")).read_text())
        if (
            report.get("source_gold_sha256") != gold_sha256
            or report.get("snapshot_id") != self.snapshot
            or report.get("content_sha256")
            != digest({key: value for key, value in report.items() if key != "content_sha256"})
        ):
            raise ValueError("Research observation report integrity differs")
        return report
