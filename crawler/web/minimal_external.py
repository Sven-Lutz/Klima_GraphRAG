"""Explicit factual DTOs for review or separately authorized minimal serving.

Canonical retrieval remains unchanged. Live use requires a separate, explicit,
time-bound statutory assessment; candidate curation alone grants no permission.
"""

import hashlib
import json
import re
from urllib.parse import urlsplit

from crawler.web.minimal_policy import content_hash, validate_minimal_policy


class MinimalExternalPreview:
    def __init__(self, candidate, authorization=None):
        self.live = authorization is not None
        if authorization is not None:
            validate_minimal_policy(authorization)
            if content_hash(candidate) != authorization["facts_sha256"]:
                raise ValueError("Authorization binds a different factual registry")
        if candidate.get("deployment_approved") is not False:
            raise ValueError("Review projector cannot accept a deployment approval")
        self.registry_sha256 = content_hash(candidate)
        self.facts = {}
        for fact in candidate["facts"]:
            rid = fact["relation_id"]
            url = urlsplit(fact["url"])
            if (
                rid in self.facts
                or url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or not re.fullmatch(r"[a-f0-9]{64}", fact["source_sha256"])
            ):
                raise ValueError("Unique facts and direct HTTPS publisher links required")
            if len(fact["summary"]) > 600 or not fact["summary"]:
                raise ValueError("Bounded own factual restatement required")
            self.facts[rid] = fact

    def _envelope(self):
        return {
            "contract": "minimal_external_preview-v1",
            "release_status": "SCOPED_RESEARCH_PREVIEW"
            if self.live
            else "TECHNICAL_REVIEW_ONLY_NOT_DEPLOYABLE",
            "scientific_validation": "PENDING",
            "originals": "NOT_DISTRIBUTED",
            "excerpts": "NOT_DISTRIBUTED",
        }

    @staticmethod
    def _public_fact(fact):
        # Explicit construction: no source text, prompts, private paths or new backend fields.
        return {
            "relation_id": fact["relation_id"],
            "summary": fact["summary"],
            "presentation": "OWN_FACTUAL_RESTATEMENT_NOT_SOURCE_QUOTE",
            "source": {
                "sha256": fact["source_sha256"],
                "url": fact["url"],
                "publisher": fact["publisher"],
                "physical_page": fact["page"],
            },
            "reference_time": fact["reference_time"],
            "time_semantics": "SOURCE_REFERENCE_NOT_CURRENT_VALIDITY",
            "extraction": "PROVISIONAL_AGENT_SOURCE_REVIEWED",
        }

    def query(self, response, question):
        leads = []
        seen = set()
        for rank, hit in enumerate(response["evidence"], 1):
            sha = hit["document"]["document_id"].removeprefix("source-version:")
            if sha in seen:
                continue
            facts = [self._public_fact(f) for f in self.facts.values() if f["source_sha256"] == sha]
            if not facts:
                continue
            seen.add(sha)
            leads.append(
                {
                    "first_retrieval_rank": rank,
                    "retrieved_source_id": hit["source_id"],
                    "association": "SOURCE_LEVEL_ONLY_NOT_ENTAILMENT_BY_MATCHED_PASSAGE",
                    "facts": facts,
                }
            )
        return {
            **self._envelope(),
            "result_id": response["result_id"],
            "question": question,
            "source_leads": leads,
            "unmapped_hits": "OMITTED_NOT_SEMANTIC_ABSTENTION",
            "snapshot": response["retrieval_result"]["database_snapshot_id"],
        }

    def graph(self, response):
        edges, nodes = [], {}
        for edge in response["edges"]:
            fact = self.facts.get(edge["id"])
            if not fact:
                continue
            if (edge["source"], edge["target"], edge["source_sha256"], edge["predicate"]) != (
                fact["subject_id"],
                fact["object_id"],
                fact["source_sha256"],
                fact["predicate"],
            ):
                raise ValueError("Stored relation does not match source-bound fact")
            for side in ("subject", "object"):
                nodes[fact[side + "_id"]] = {
                    "id": fact[side + "_id"],
                    "label": fact[side + "_label"],
                }
            edges.append(
                {
                    "id": edge["id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "predicate": fact["predicate"],
                    "fact": self._public_fact(fact),
                }
            )
        body = {
            **self._envelope(),
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(edges, key=lambda e: e["id"]),
            "underlying_snapshot": response["snapshot_id"],
        }
        body["view_sha256"] = hashlib.sha256(self.export(body, "json").encode()).hexdigest()
        return body

    def export(self, public_dto, format_name):
        if public_dto.get("contract") != "minimal_external_preview-v1":
            raise ValueError("Canonical/raw exports cannot be used in the minimal review view")
        allowed = set(self._envelope()) | {
            "result_id",
            "question",
            "source_leads",
            "unmapped_hits",
            "snapshot",
            "nodes",
            "edges",
            "underlying_snapshot",
            "view_sha256",
        }
        if set(public_dto) - allowed or any(
            public_dto.get(k) != v for k, v in self._envelope().items()
        ):
            raise ValueError("Unexpected field or review status in external DTO")
        known = [self._public_fact(f) for f in self.facts.values()]
        for key in ("result_id", "snapshot", "underlying_snapshot"):
            if key in public_dto and not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", public_dto[key]):
                raise ValueError("Invalid public identifier")
        for lead in public_dto.get("source_leads", []):
            if set(lead) != {"first_retrieval_rank", "retrieved_source_id", "association", "facts"}:
                raise ValueError("Unexpected source-lead field")
            if any(f not in known for f in lead["facts"]):
                raise ValueError("Unregistered factual representation")
            if (
                not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", lead["retrieved_source_id"])
                or type(lead["first_retrieval_rank"]) is not int
                or not 1 <= lead["first_retrieval_rank"] <= 20
            ):
                raise ValueError("Invalid source-lead identifier or rank")
        labels = {
            (f[side + "_id"], f[side + "_label"])
            for f in self.facts.values()
            for side in ("subject", "object")
        }
        for node in public_dto.get("nodes", []):
            if set(node) != {"id", "label"} or (node["id"], node["label"]) not in labels:
                raise ValueError("Unregistered node label")
        for edge in public_dto.get("edges", []):
            if (
                set(edge) != {"id", "source", "target", "predicate", "fact"}
                or edge["fact"] not in known
            ):
                raise ValueError("Unexpected graph field or fact")
        if format_name == "json":
            return json.dumps(public_dto, ensure_ascii=False, sort_keys=True, indent=2)
        if format_name == "markdown":
            # Serialize only this same DTO, never the canonical export renderer.
            return (
                "# Minimal external preview — technical review only\n\n```json\n"
                + json.dumps(public_dto, ensure_ascii=False, sort_keys=True, indent=2).replace(
                    "```", "\\u0060\\u0060\\u0060"
                )
                + "\n```\n"
            )
        raise ValueError("Only JSON and Markdown supported")
