"""User-oriented retrieval version, isolated from frozen scientific comparators."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from crawler.core.provenance import evidence_id_for
from crawler.core.retrieval import (
    CanonicalRetriever,
    DocumentSource,
    RetrievalError,
    RetrievalMode,
    RetrievalResult,
    RetrievalTrace,
    RetrievedEvidence,
)
from crawler.web.bavaria import BavariaPreviewRetriever, make_scope, response_budget
from crawler.web.local_index import navigation_boilerplate
from crawler.web.query_planning import (
    answer_constraint,
    content_terms,
    product_query_expansion,
    requested_graph_role_predicates,
)
from crawler.web.scope_conditions import passage_subject, subject_conditions
from research.bavaria.workspace.core import roots

VERSION = "evidence-retrieval-product-v4"


def passage(row, snapshot, rank, method):
    locator = (row.get("heading") or "Originalabschnitt") + f" · Einheit {row['ordinal']}"
    if row.get("page_ref"):
        locator += f" · physische Seite {row['page_ref']}"
    frames = row.get("source_frame_ags", row.get("frame_ags", []))
    if isinstance(frames, str):
        frames = frames.split(",")
    return RetrievedEvidence(
        database_snapshot_id=snapshot,
        evidence_id=evidence_id_for(
            "segment", row["unit_id"], hashlib.sha256(row["text"].encode()).hexdigest()
        ),
        source_type="segment",
        source_id=row["unit_id"],
        segment_id=row["unit_id"],
        chunk_id=None,
        document=DocumentSource(
            "source-version:" + row["sha256"], row["source_url"], locator, tuple(frames)
        ),
        evidence_text=row["text"],
        extraction_run_ids=(),
        graph_artifact_ids=(),
        assertions=(),
        retrieval_traces=(RetrievalTrace(method, rank, float(row.get("score", 0))),),
        retrieval_score=float(row.get("score", 0)),
        rank=rank,
    )


class ProductRetriever(CanonicalRetriever):
    def __init__(self, baseline, semantic=None):
        super().__init__()
        self.baseline, self.workspace, self.semantic = baseline, baseline.workspace, semantic
        self.supported_modes = ["lexical", "lexical_expanded", "graph"]
        if semantic is not None and semantic.status().get("complete"):
            self.supported_modes += ["semantic", "hybrid"]

    def _role_pass(self, request, result, scope, terms):
        predicates = requested_graph_role_predicates(request.query)
        trace = dict(applied=False, baseline_items=len(result.items))
        if result.items or not predicates or not isinstance(self.baseline, BavariaPreviewRetriever):
            return result, trace
        if len(self.workspace.relation_rows) > 500:
            return result, {**trace, "reason": "ROLE_CANDIDATE_BUDGET_UNAVAILABLE"}
        generic = set(
            roots(
                "wer wem welche firma institut büro organisation akteur unternehmen ministerium "
                "erstellt erarbeitet beauftragt geplant plant genannt geldgeber finanziert "
                "belegt passage bereits fertig gebaut anlage"
            )
        )
        anchors = terms - generic
        if not anchors:
            return result, {**trace, "reason": "NO_SUBSTANTIVE_ROLE_QUERY_ANCHOR"}
        candidates = []
        for relation in self.workspace.relation_rows:
            if relation["predicate"] not in predicates:
                continue
            claim_scope = set(relation["record"].get("claim_scope_ags") or [])
            if not claim_scope or (scope and not set(scope) & claim_scope):
                continue
            labels = " ".join(
                self.workspace.entities[relation[key]]["label"]
                for key in ("subject_id", "object_id")
            )
            score = len(anchors & set(roots(labels + " " + relation["record"]["quote"])))
            if score:
                candidates.append((score, relation["relation_id"]))
        ids = frozenset(
            rid
            for _, rid in sorted(candidates, key=lambda row: (-row[0], row[1]))[
                : min(6, request.top_k)
            ]
        )
        if not ids:
            return result, {**trace, "reason": "NO_SOURCE_BOUND_TYPED_ROLE_CANDIDATE"}
        # Explicit IDs choose a different product plan, not different evidence.
        # Canonical original-byte verification, scope, context and k gates still run.
        selected = self.baseline.retrieve(
            replace(
                request,
                query="Beleggebundene Rollenrecherche",
                mode=RetrievalMode.GRAPH,
                topic="",
                municipality_ids=tuple(scope),
                reference_year=None,
            ),
            relation_ids=ids,
        )
        return selected, dict(
            applied=True,
            baseline_items=len(result.items),
            strategy="SOURCE_BOUND_TYPED_ROLE_SECOND_PASS_V1",
            relation_ids=sorted(ids),
            predicates=list(predicates),
            candidate_budget=500,
            relation_budget=min(6, request.top_k),
            canonical_graph_hops=request.graph_hops,
            scope_semantics="EXPLICIT_CLAIM_SCOPE_NOT_WEBSITE_FRAME",
            proves_implementation=False,
        )

    def retrieve(self, request):
        request.validate()
        self.baseline.check_identity()
        w = self.workspace
        targets = w.resolve_ags(list(request.municipality_ids) or None, request.query)
        constraint = answer_constraint(request.query)
        if constraint:
            return RetrievalResult(
                request.query,
                request.mode.value,
                w.snapshot_id,
                request.projection_id,
                (),
                warnings=(constraint,),
                search_scope=make_scope(request, targets, "unsupported_inference_or_scope"),
                diagnostics=dict(method=VERSION, reason=constraint, arms=[], fallback=False),
            )
        names = [w.municipalities[a]["name"] for a in targets]
        conditions = subject_conditions(request.query)
        terms = content_terms(conditions["positive_query"] + " " + request.topic, names)
        for year in re.findall(r"\b(?:18|19|20)\d{2}\b", request.query):
            terms.discard(year)
        topic_text = " ".join(sorted(terms)) or request.query
        semantic_question = conditions["semantic_query"]
        for name in names:
            semantic_question = re.sub(
                r"(?<!\w)" + re.escape(name) + r"(?:s|er)?(?!\w)", "", semantic_question, flags=re.I
            )

        expansion = w.discovery_index.expand_query(request.query)
        product_expansion = (
            product_query_expansion(request.query)
            if request.mode
            in {RetrievalMode.LEXICAL_EXPANDED, RetrievalMode.HYBRID, RetrievalMode.GRAPH}
            else ()
        )
        effective_terms = terms | set(roots(" ".join((*expansion, *product_expansion))))
        # Separate per-place requests prevent one large municipality consuming a comparison.
        scopes = [[a] for a in targets] if 1 < len(targets) <= 8 else [targets]
        arms, diagnostics = [], []
        for scope in scopes:
            arm_scope = tuple(scope)
            if request.mode in {
                RetrievalMode.LEXICAL,
                RetrievalMode.LEXICAL_EXPANDED,
                RetrievalMode.HYBRID,
            }:
                mode = (
                    RetrievalMode.LEXICAL
                    if request.mode is RetrievalMode.LEXICAL
                    else RetrievalMode.LEXICAL_EXPANDED
                )
                result = self.baseline.retrieve(
                    replace(
                        request,
                        query=(
                            topic_text
                            + " "
                            + " ".join([*conditions["controlled_expansion"], *product_expansion])
                            if mode is RetrievalMode.LEXICAL_EXPANDED
                            else topic_text
                        ),
                        mode=mode,
                        municipality_ids=arm_scope,
                        reference_year=None,
                    )
                )
                arms.append(result.items)
                diagnostics.append(
                    dict(method=mode.value, scope=scope, diagnostics=result.diagnostics)
                )
            if request.mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID}:
                if self.semantic is None or not self.semantic.status().get("complete"):
                    raise RetrievalError("method_unavailable_for_snapshot")
                raw = self.semantic.discover(semantic_question, scope, request.top_k)
                arms.append(
                    tuple(
                        passage(row, w.snapshot_id, i, "dense-product-v1")
                        for i, row in enumerate(raw["results"], 1)
                    )
                )
                diagnostics.append(
                    dict(method="semantic", scope=scope, diagnostics=raw.get("diagnostics", {}))
                )
            if request.mode in {RetrievalMode.GRAPH, RetrievalMode.HYBRID} and w.relations_db:
                result = self.baseline.retrieve(
                    replace(
                        request,
                        mode=RetrievalMode.GRAPH,
                        municipality_ids=arm_scope,
                        reference_year=None,
                    )
                )
                result, role_selection = self._role_pass(request, result, scope, effective_terms)
                # Generic query/location overlap alone must not justify a graph relation.
                allowed = set()
                relations = {r["relation_id"]: r for r in w.relation_rows}
                for item in result.items:
                    for assertion in item.assertions:
                        relation = relations.get(assertion.assertion_id)
                        if not relation:
                            continue
                        labels = " ".join(
                            w.entities[relation[k]]["label"] for k in ("subject_id", "object_id")
                        )
                        quote = relation["record"]["quote"]
                        if effective_terms & set(roots(labels + " " + quote)):
                            allowed.add(assertion.assertion_id)
                required = {
                    source
                    for item in result.items
                    for assertion in item.assertions
                    if assertion.assertion_id in allowed
                    for source in assertion.required_source_ids
                }
                items = tuple(
                    replace(
                        item,
                        assertions=tuple(a for a in item.assertions if a.assertion_id in allowed),
                    )
                    for item in result.items
                    if item.source_id in required
                )
                arms.append(items)
                diagnostics.append(
                    dict(
                        method="graph",
                        role_selection=role_selection,
                        scope=scope,
                        selected_before=sum(len(i.assertions) for i in result.items),
                        selected_after=len(allowed),
                        rejection_rule="no_topical_relation_anchor-v1",
                    )
                )
        # Bounded reciprocal rank fusion; no mixing incomparable cosine/BM25 scores.
        scores, items = {}, {}
        for arm in arms:
            for rank, item in enumerate(arm, 1):
                key = item.evidence_id
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                if key in items:
                    previous = items[key]
                    items[key] = replace(
                        previous,
                        retrieval_traces=previous.retrieval_traces + item.retrieval_traces,
                        assertions=tuple(
                            {
                                a.assertion_id: a for a in (*previous.assertions, *item.assertions)
                            }.values()
                        ),
                    )
                else:
                    items[key] = item
        selected, rejected = [], []
        for key in sorted(items, key=lambda k: (-scores[k], k)):
            item = items[key]
            subject = passage_subject(item.evidence_text, item.document.heading_context)
            if subject in conditions["excluded"] or (
                conditions["requested"] == ["municipal_assets"] and subject == "territory"
            ):
                rejected.append(dict(evidence_id=key, reason="excluded_subject_frame"))
                continue
            # Lexical anchors are sufficient, not necessary for dense retrieval.
            # Navigation/contact-only candidates never become substantive answer passages.
            if navigation_boilerplate(item.evidence_text) or len(item.evidence_text.split()) < 5:
                rejected.append(dict(evidence_id=key, reason="navigation_or_title_only"))
                continue
            if request.reference_year and str(request.reference_year) not in item.evidence_text:
                rejected.append(dict(evidence_id=key, reason="requested_year_not_mentioned"))
                continue
            selected.append(replace(item, retrieval_score=scores[key], rank=len(selected) + 1))
        if conditions["requested"] == ["municipal_assets"]:
            selected.sort(
                key=lambda item: (
                    passage_subject(item.evidence_text, item.document.heading_context)
                    != "municipal_assets"
                )
            )
        # Preserve at least one eligible item per requested municipality for comparisons.
        balanced = []
        if len(targets) > 1:
            for ags in targets:
                hit = next((i for i in selected if ags in i.document.municipality_ids), None)
                if hit and hit.evidence_id not in {i.evidence_id for i in balanced}:
                    balanced.append(hit)
        balanced += [i for i in selected if i.evidence_id not in {b.evidence_id for b in balanced}]
        selected = [replace(i, rank=r) for r, i in enumerate(balanced[: request.top_k], 1)]
        present_sources = {item.source_id for item in selected}
        selected = [
            replace(
                item,
                assertions=tuple(
                    a for a in item.assertions if set(a.required_source_ids) <= present_sources
                ),
            )
            for item in selected
        ]
        if request.mode is RetrievalMode.GRAPH:
            required_context = {
                source
                for item in selected
                for assertion in item.assertions
                for source in assertion.required_source_ids
            }
            selected = [
                item for item in selected if item.assertions or item.source_id in required_context
            ]
        selected = [
            replace(
                item,
                rank=i,
                graph_artifact_ids=tuple(
                    dict.fromkeys(a.graph_artifact_id for a in item.assertions)
                ),
            )
            for i, item in enumerate(selected, 1)
        ]
        return response_budget(
            RetrievalResult(
                request.query,
                request.mode.value,
                w.snapshot_id,
                request.projection_id,
                tuple(selected),
                warnings=("Quellenrahmen und Aussagegeltung unterscheiden; fachlich unvalidiert.",),
                search_scope=make_scope(
                    request, targets, "source_association_with_graph_claim_scope"
                ),
                diagnostics=dict(
                    method=VERSION,
                    terms=sorted(terms),
                    expansion=list(expansion),
                    product_query_expansion=list(product_expansion),
                    fusion="RRF-k60",
                    role_scope_only=bool(
                        re.search(r"umsetz|fertig|gebaut|betrieb|realisi", request.query, re.I)
                        and any(arm.get("role_selection", {}).get("applied") for arm in diagnostics)
                    ),
                    subject_conditions=conditions,
                    arms=diagnostics,
                    rejected=rejected,
                    temporal_semantics="year_mention_not_validity",
                    fallback=False,
                ),
            )
        )
