"""Public adapter over the existing retriever; never serializes canonical source text."""

import copy
import hashlib
import json
from collections import OrderedDict
from types import SimpleNamespace

from crawler.core.retrieval import RetrievalError
from crawler.web.graph_explorer import GraphExplorer
from crawler.web.minimal_external import MinimalExternalPreview
from crawler.web.minimal_policy import validate_minimal_policy
from crawler.web.service import ResearchApplicationService


class ProjectedStore:
    """Keep the public projection under the same expiry and capacity as its canonical result."""

    def __init__(self, canonical, public):
        self.canonical, self.public = canonical, public
        self.capacity = canonical.capacity

    def purge_expired(self):
        self.canonical.purge_expired()
        for key in list(self.public):
            if self.canonical.get(key) is None:
                self.public.pop(key, None)

    def get(self, key):
        self.purge_expired()
        return self.canonical.get(key)


NOTICE = (
    "Eigene sachliche Darstellung, kein Originalzitat und keine generierte Antwort. "
    "Der Suchtreffer ordnet eine Quelle zu; diese Darstellung muss nicht "
    "die konkrete Frage beantworten."
)


class MinimalResearchService(ResearchApplicationService):
    def __init__(self, inner, policy):
        validate_minimal_policy(policy)
        self._inner = inner
        self.projector = MinimalExternalPreview(policy["minimal_facts"], authorization=policy)
        self.retriever = inner.retriever
        self.database_snapshot_id = inner.database_snapshot_id
        self.manifest_path = inner.manifest_path
        self.projection_id = inner.projection_id
        self.generator = None
        self._public = OrderedDict()
        self.store = ProjectedStore(inner.store, self._public)
        w = inner.workspace
        relations = {r["relation_id"]: r for r in w.relation_rows}
        if set(relations) != set(self.projector.facts):
            raise ValueError("Minimal bundle contains extra or missing relations")
        entities = {}
        for rid, fact in self.projector.facts.items():
            r = relations[rid]
            if (r["subject_id"], r["object_id"], r["predicate"], r["record"]["source_sha256"]) != (
                fact["subject_id"],
                fact["object_id"],
                fact["predicate"],
                fact["source_sha256"],
            ):
                raise ValueError("Minimal fact is not bound to the stored relation")
            for side in ("subject", "object"):
                eid = fact[side + "_id"]
                entities[eid] = {
                    "label": fact[side + "_label"],
                    "entity_type": w.entities[eid]["entity_type"],
                    "identity_basis": (
                        "Vorläufige maschinelle Extraktion; eigene sachliche Bezeichnung."
                    ),
                }
        self.workspace = SimpleNamespace(
            entities=entities,
            relation_rows=list(relations.values()),
            evidence_rows=w.evidence_rows,
            snapshot_id=w.snapshot_id,
            manifest=w.manifest,
            relations_db=w.relations_db,
            relation_payload=w.relation_payload,
        )

    def status(self):
        base = self._inner.status()
        return {
            "application": "Klima Research",
            "demo_mode": False,
            "demo_notice": None,
            "provider_gate": self._inner.provider_gate_payload(),
            "database_snapshot_id": self.database_snapshot_id,
            "projection_id": self.projection_id,
            "supported_modes": ["lexical", "lexical_expanded", "graph"],
            "local_preview": True,
            "pilot_mode": True,
            "public_release_cleared": False,
            "human_gold_available": False,
            "coverage": base["coverage"],
            "municipalities": base["municipalities"],
            "examples": base["examples"],
            "graph_example": "Welche Quellen nennen Beziehungen zur Wärmeplanung?",
            "scope_notice": NOTICE,
            "research_preview": {
                "source_presentation": "minimal_external_preview",
                "scientific_validation": "PENDING",
                "facts": len(self.projector.facts),
                "operator": "Nichtkommerziell forschende Einzelperson; kein Hochschulbetrieb",
            },
        }

    def _present_public(self, raw, question, options=None, relation_id=None):
        view = self.projector.query(raw, question)
        evidence, shown_facts = [], {}
        raw_by_source = {e["source_id"]: e for e in raw["evidence"]}
        for lead in view["source_leads"]:
            hit = raw_by_source[lead["retrieved_source_id"]]
            for f in lead["facts"]:
                fact = self.projector.facts[f["relation_id"]]
                eid = "fact-evidence:" + f["relation_id"]
                shown_facts[f["relation_id"]] = eid
                evidence.append(
                    {
                        "evidence_id": eid,
                        "rank": lead["first_retrieval_rank"],
                        "source_id": lead["retrieved_source_id"],
                        "source_type": "source_association",
                        "database_snapshot_id": self.database_snapshot_id,
                        "evidence_text": fact["summary"],
                        "document": {
                            "document_id": "source-version:" + fact["source_sha256"],
                            "source_url": fact["url"],
                            "heading_context": fact["publisher"] + " · eigene Darstellung",
                            "municipality_ids": hit["document"]["municipality_ids"],
                        },
                        "citation": {"label": "C" + str(len(evidence) + 1)},
                        "assertions": [
                            {"assertion_id": f["relation_id"], "relation_type": fact["predicate"]}
                        ],
                        "retrieval_traces": [
                            {
                                "method": raw["retrieval_result"]["mode"],
                                "first_source_rank": lead["first_retrieval_rank"],
                                "association": lead["association"],
                            }
                        ],
                        "source_metadata": {
                            "archive_url": None,
                            "source_presentation": "minimal_external_preview",
                            "publisher": fact["publisher"],
                            "publication_date": None,
                            "retrieved_at": self._inner.retrieved_at.get(fact["source_sha256"]),
                            "original_sha256": fact["source_sha256"],
                            "identity_status": "EXPORT_VERIFIED_NO_RUNTIME_ORIGINAL",
                            "locator": {
                                "kind": "pdf_page" if fact["page"] else "html",
                                "ordinal": None,
                                "page_ref": fact["page"],
                            },
                            "presentation": "OWN_FACTUAL_RESTATEMENT_NOT_SOURCE_QUOTE",
                            "association_notice": NOTICE,
                        },
                    }
                )
        edges, nodes = [], {}
        for rid, eid in shown_facts.items():
            f = self.projector.facts[rid]
            for side in ("subject", "object"):
                entity_id = f[side + "_id"]
                nodes[entity_id] = {
                    "id": entity_id,
                    "label": f[side + "_label"],
                    "entity_type": self.workspace.entities[entity_id]["entity_type"],
                }
            edges.append(
                {
                    "id": rid,
                    "kind": "assertion",
                    "source": f["subject_id"],
                    "target": f["object_id"],
                    "label": f["predicate"],
                    "evidence_ids": [eid],
                    "context_evidence_ids": [],
                    "reference_time": f["reference_time"],
                    "claim_scope_ags": None,
                }
            )
        scope = raw["retrieval_result"].get("search_scope") or {}
        safe_scope = {
            k: scope.get(k)
            for k in ("requested_ags", "effective_ags", "resolution", "basis", "text_year")
        }
        result = {
            "result_id": raw["result_id"],
            "result_kind": "retrieval",
            "answer_result": None,
            "inline_answer_parts": [],
            "evidence": evidence,
            "graph": {"nodes": list(nodes.values()), "edges": edges},
            "retrieval_result": {
                "database_snapshot_id": self.database_snapshot_id,
                "query": question,
                "mode": raw["retrieval_result"]["mode"],
                "search_scope": safe_scope,
                "warnings": [NOTICE],
            },
            "provider_gate": self._inner.provider_gate_payload(),
            "demo_mode": False,
            "partial": bool(options and options.reference_year),
            "request": {
                "kind": "retrieval",
                "question": options.question,
                "mode": options.mode.value,
                "top_k": options.top_k,
                "max_evidence_items": options.max_evidence_items,
                "max_evidence_characters": options.max_evidence_characters,
                "municipality_ids": list(options.municipality_ids),
                "topic": options.topic,
                "reference_year": options.reference_year,
                "generate_answer": False,
            }
            if options
            else {"kind": "stored_relation", "relation_id": relation_id},
            "projection_id": self.projection_id,
            "factual_registry_sha256": self.projector.registry_sha256,
            "external_contract": {
                "name": "minimal_external_preview-v1",
                "status": "SCOPED_RESEARCH_PREVIEW",
                "source_passages": "NOT_DISTRIBUTED",
                "original_files": "NOT_DISTRIBUTED",
                "scientific_validation": "PENDING",
            },
        }
        self.store.purge_expired()
        while len(self._public) >= self.store.capacity:
            self._public.popitem(last=False)
        self._public[result["result_id"]] = copy.deepcopy(result)
        return result

    def execute(self, options):
        return self._present_public(self._inner.execute(options), options.question, options)

    def graph_evidence(self, relation_id):
        if relation_id not in self.projector.facts:
            raise ValueError("Unknown relation")
        return self._present_public(
            self._inner.graph_evidence(relation_id),
            "Gespeicherte Relation prüfen",
            relation_id=relation_id,
        )

    def graph_view(self, root=None, depth=1, predicate="", entity_type="", year=None, target=None):
        explorer = GraphExplorer(self.workspace)
        if root is None:
            return explorer.overview()
        raw = explorer.subgraph(root, depth, predicate, entity_type, year, target)
        public = self.projector.graph(raw)
        result = {
            k: raw[k]
            for k in (
                "snapshot_id",
                "root",
                "depth",
                "filters",
                "chain",
                "target",
                "truncated",
                "limits",
                "traversal",
                "validation",
            )
        }
        result["nodes"] = [
            {
                "id": n["id"],
                "label": n["label"],
                "type": n["type"],
                "distance": n["distance"],
                "identity_basis": n["identity_basis"],
            }
            for n in raw["nodes"]
        ]
        result["edges"] = []
        for e in public["edges"]:
            fact = self.projector.facts[e["id"]]
            result["edges"].append(
                {
                    "id": e["id"],
                    "source": e["source"],
                    "target": e["target"],
                    "predicate": e["predicate"],
                    "source_sha256": fact["source_sha256"],
                    "reference_time": fact["reference_time"],
                    "extraction_method": explorer.relations[e["id"]]["record"].get(
                        "extraction_model"
                    ),
                    "prompt_version": explorer.relations[e["id"]]["record"].get("prompt_version"),
                    "validation": "PROVISIONAL_NOT_HUMAN_GOLD",
                    "visual_original_recommended": False,
                }
            )
        result["external_contract"] = "minimal_external_preview-v1"
        result["subgraph_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return result

    def present(self, *_args, **_kwargs):
        raise ValueError("Only explicit minimal projection entry points are available")

    def export(self, result_id, format_name):
        if format_name not in {"json", "markdown"}:
            raise ValueError("Unsupported export format")
        if self.store.get(result_id) is None:
            self._public.pop(result_id, None)
            return None
        result = self._public.get(result_id)
        if result is None:
            return None
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        if format_name == "json":
            return rendered
        return "# Klima Research · eigene sachliche Darstellungen\n\n" + rendered.replace(
            "```", "\\u0060\\u0060\\u0060"
        )

    def original(self, *_args, **_kwargs):
        raise RetrievalError("archive_distribution_disabled")

    def close(self):
        self._public.clear()
        self._inner.close()
