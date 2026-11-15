"""Bounded navigation over the manifested provisional relation sidecar."""

import hashlib
import json
import re
from collections import Counter, deque


class GraphExplorer:
    def __init__(self, workspace):
        self.workspace = workspace
        self.relations = {r["relation_id"]: r for r in workspace.relation_rows}

    def overview(self):
        w = self.workspace
        years = sorted(
            {
                year
                for r in self.relations.values()
                for year in re.findall(
                    r"\b(?:19|20)\d{2}\b", str(r["record"].get("reference_time") or "")
                )
            }
        )
        degree = Counter(
            x for r in self.relations.values() for x in (r["subject_id"], r["object_id"])
        )
        return {
            "snapshot_id": w.snapshot_id,
            "projection_sha256": w.manifest["artifacts"]["relations"]["sha256"],
            "entities": len(w.entities),
            "relations": len(self.relations),
            "source_versions": len({r["record"]["source_sha256"] for r in self.relations.values()}),
            "entity_types": dict(Counter(e["entity_type"] for e in w.entities.values())),
            "relation_types": dict(Counter(r["predicate"] for r in self.relations.values())),
            "reference_years": years,
            "temporal_semantics": ("Reference-year mentions; unknown excluded when filtered"),
            "validation": "PROVISIONAL_AGENT_SOURCE_REVIEWED",
            "human_gold": False,
            "default_entity": sorted(w.entities, key=lambda x: (-degree[x], x))[0]
            if w.entities
            else None,
            "entity_index": [
                {"id": key, "label": e["label"], "type": e["entity_type"]}
                for key, e in sorted(w.entities.items())
            ],
        }

    def subgraph(self, root, depth=1, predicate="", entity_type="", year=None, target=None):
        w = self.workspace
        if root not in w.entities or not 1 <= depth <= 3:
            raise ValueError("Known entity and depth 1..3 required")
        if target and target not in w.entities:
            raise ValueError("Unknown target")
        adjacency = {key: [] for key in w.entities}
        for rid, r in sorted(self.relations.items()):
            if predicate and r["predicate"] != predicate:
                continue
            if year and str(year) not in re.findall(
                r"\b(?:19|20)\d{2}\b", str(r["record"].get("reference_time") or "")
            ):
                continue
            if entity_type and not all(
                x == root or w.entities[x]["entity_type"] == entity_type
                for x in (r["subject_id"], r["object_id"])
            ):
                continue
            adjacency[r["subject_id"]].append((r["object_id"], rid))
            adjacency[r["object_id"]].append((r["subject_id"], rid))
        visited = {root: 0}
        chosen = set()
        queue = deque([root])
        parent = {}
        truncated = False
        while queue:
            node = queue.popleft()
            if visited[node] >= depth:
                continue
            for other, rid in adjacency[node]:
                if len(chosen) >= 100 or (other not in visited and len(visited) >= 60):
                    truncated = True
                    continue
                chosen.add(rid)
                if other not in visited:
                    visited[other] = visited[node] + 1
                    parent[other] = (node, rid)
                    queue.append(other)
        nodes = [
            {
                "id": key,
                "label": w.entities[key]["label"],
                "type": w.entities[key]["entity_type"],
                "distance": distance,
                "identity_basis": w.entities[key]["identity_basis"],
            }
            for key, distance in sorted(visited.items())
        ]
        edges = []
        for rid in sorted(chosen):
            r = self.relations[rid]
            rec = r["record"]
            e = w.evidence_rows[r["evidence_id"]]
            edges.append(
                {
                    "id": rid,
                    "source": r["subject_id"],
                    "target": r["object_id"],
                    "predicate": r["predicate"],
                    "source_sha256": rec["source_sha256"],
                    "unit_id": e["unit_id"],
                    "locator": {
                        "ordinal": e.get("ordinal"),
                        "page": e["page_ref"],
                        "heading": e["heading"],
                    },
                    "quote": rec["quote"],
                    "visual_original_recommended": bool(
                        e.get("page_ref") and r["predicate"] == "HAS_REPORTED_STATUS"
                    ),
                    "extraction_method": rec.get("extraction_model"),
                    "prompt_version": rec.get("prompt_version"),
                    "extraction_run": "relational-source-review:"
                    + w.manifest["artifacts"]["relations"]["sha256"],
                    "reference_time": rec.get("reference_time"),
                    "claim_scope_ags": rec.get("claim_scope_ags"),
                    "validation": rec["status"],
                    "context_source_ids": w.relation_payload(r)["required_source_ids"],
                }
            )
        chain = []
        cursor = target
        while cursor in parent:
            cursor, rid = parent[cursor]
            chain.append(rid)
        chain.reverse()
        result = {
            "snapshot_id": w.snapshot_id,
            "projection_sha256": w.manifest["artifacts"]["relations"]["sha256"],
            "root": root,
            "depth": depth,
            "filters": {"predicate": predicate, "entity_type": entity_type, "reference_year": year},
            "nodes": nodes,
            "edges": edges,
            "chain": chain,
            "target": target,
            "truncated": truncated,
            "limits": {"nodes": 60, "edges": 100},
            "traversal": "Undirected navigation; edge direction preserved",
            "validation": "PROVISIONAL_NOT_HUMAN_GOLD",
        }
        result["subgraph_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return result
