"""Prevent territorial totals from answering a municipal-building question."""

from types import SimpleNamespace

import pytest

from crawler.core.retrieval import RetrievalMode, RetrievalRequest, RetrievalResult
from crawler.web.product_retrieval import ProductRetriever, passage
from crawler.web.scope_conditions import subject_conditions

QUESTION = (
    "Welche Veränderung beim Wärmeverbrauch der städtischen Gebäude beschreibt Amberg "
    "für 2021 und 2022? Bitte keine Werte für die gesamte Stadt."
)


def test_explicit_exclusion_does_not_become_a_positive_search_anchor():
    conditions = subject_conditions(QUESTION)
    assert conditions["requested"] == ["municipal_assets"]
    assert conditions["excluded"] == ["territory"]
    assert "gesamte Stadt" not in conditions["semantic_query"]
    assert "kommunale Liegenschaften" in conditions["semantic_query"]
    assert subject_conditions("Vergleiche kommunale Liegenschaften und Gesamtstadt.")[
        "requested"
    ] == ["municipal_assets", "territory"]


def test_real_retriever_rejects_conflicting_section_but_keeps_asset_comparison():
    ags = "09361000"
    texts = [
        "Gesamtstadt – Wärme und Strom\nDer Bedarf ist auf 545 GWh angestiegen.",
        "Kommunale Liegenschaften\nDer Wärmebedarf lag 2021 bei 15.300.000 kWh.",
        "Kommunale Liegenschaften\nDer Wärmebedarf lag 2022 bei 13.200.000 kWh.",
    ]
    rows = [
        passage(
            dict(
                unit_id=f"unit{i}",
                sha256="a" * 64,
                text=text,
                ordinal=i,
                source_url="https://amberg.de/report.pdf",
                frame_ags=[ags],
            ),
            "test-snapshot",
            i + 1,
            "synthetic-fixture",
        )
        for i, text in enumerate(texts)
    ]

    class Baseline:
        workspace = SimpleNamespace(
            municipalities={ags: {"name": "Amberg"}},
            snapshot_id="test-snapshot",
            relations_db=None,
            discovery_index=SimpleNamespace(expand_query=lambda q: ()),
            resolve_ags=lambda supplied, query: list(supplied) or [ags],
        )

        def check_identity(self):
            pass

        def retrieve(self, request):
            return RetrievalResult(
                request.query, request.mode.value, "test-snapshot", None, tuple(rows)
            )

    result = ProductRetriever(Baseline()).retrieve(
        RetrievalRequest(
            QUESTION,
            "explicit-manifest",
            RetrievalMode.LEXICAL_EXPANDED,
            8,
            municipality_ids=(ags,),
        )
    )
    assert len(result.items) == 2
    assert all("Kommunale Liegenschaften" in item.evidence_text for item in result.items)
    assert {item.source_id for item in result.items} == {"unit1", "unit2"}
    assert any(row["reason"] == "excluded_subject_frame" for row in result.diagnostics["rejected"])


def test_query_plan_preserves_comparison_and_does_not_misread_exclusion_as_coverage():
    from crawler.web.query_planning import plan_query

    workspace = SimpleNamespace(
        municipalities={"09361000": {"name": "Amberg", "region": "Oberpfalz"}},
        resolve_ags=lambda *args: ["09361000"],
    )
    plan = plan_query(workspace, QUESTION)
    assert "comparison" in plan.operations
    assert "coverage" not in plan.operations


def test_joint_project_scope_is_not_an_implicit_municipality_comparison():
    from crawler.web.query_planning import plan_query

    w = SimpleNamespace(
        municipalities={"09375122": {"name": "Brunn"}, "09375162": {"name": "Laaber"}}
    )
    scope = ("09375122", "09375162")
    shared = plan_query(
        w,
        "Wer plant den Windpark Brunn-Laaber und welchen Jahresertrag erwartet die Quelle?",
        scope,
    )
    assert "comparison" not in shared.operations
    assert "multi_municipality" in shared.operations
    compared = plan_query(w, "Wie unterscheiden sich die Klimaziele von Brunn und Laaber?", scope)
    assert "comparison" in compared.operations


def test_product_heat_plan_normalization_preserves_funder_without_mutating_baseline():
    import hashlib
    from dataclasses import replace

    from crawler.core.provenance import evidence_id_for
    from crawler.core.retrieval import GraphAssertion
    from crawler.web.local_index import LOCAL_EXPANSION

    quote = "Die kommunale Wärmeplanung wird durch das Bundesministerium finanziert."
    source = "synthetic-funding-unit"
    assertion = GraphAssertion(
        "synthetic-funds",
        "artifact",
        "run",
        "ministry",
        "Bundesministerium",
        "FUNDS",
        "plan",
        "Wärmeplanung Nennslingen",
        quote,
        required_source_ids=(source,),
    )
    item = replace(
        passage(
            dict(
                unit_id=source,
                sha256="a" * 64,
                text=quote,
                ordinal=1,
                source_url="https://example.invalid",
                frame_ags=["09577151"],
            ),
            "synthetic-snapshot",
            1,
            "graph",
        ),
        assertions=(assertion,),
        graph_artifact_ids=("artifact",),
    )

    class Baseline:
        workspace = SimpleNamespace(
            municipalities={"09577151": {"name": "Nennslingen"}},
            snapshot_id="synthetic-snapshot",
            relations_db="synthetic-graph",
            discovery_index=SimpleNamespace(expand_query=lambda q: ()),
            resolve_ags=lambda supplied, question: ["09577151"],
            entities={
                "ministry": {"label": "Bundesministerium"},
                "plan": {"label": "Wärmeplanung Nennslingen"},
            },
            relation_rows=[
                dict(
                    relation_id="synthetic-funds",
                    subject_id="ministry",
                    object_id="plan",
                    record={"quote": quote},
                )
            ],
        )

        def check_identity(self):
            pass

        def retrieve(self, request):
            return RetrievalResult(
                request.query, "graph", "synthetic-snapshot", "projection", (item,)
            )

    before = dict(LOCAL_EXPANSION)
    query = "Wer ist für den Wärmeplan in Nennslingen als Geldgeber genannt?"
    result = ProductRetriever(Baseline()).retrieve(
        RetrievalRequest(
            query,
            "explicit-manifest",
            RetrievalMode.GRAPH,
            projection_id="projection",
            municipality_ids=("09577151",),
        )
    )
    assert len(result.items) == 1
    assert result.diagnostics["product_query_expansion"] == ["Wärmeplanung"]
    assert LOCAL_EXPANSION == before
    assert result.items[0].evidence_id == evidence_id_for(
        "segment", source, hashlib.sha256(quote.encode()).hexdigest()
    )


@pytest.mark.parametrize("mode", [RetrievalMode.GRAPH, RetrievalMode.HYBRID])
@pytest.mark.parametrize("context_required", [False, True])
def test_product_role_pass_recovers_author_of_concept_with_canonical_relation_ids(
    mode, context_required
):
    from dataclasses import replace

    from crawler.core.retrieval import GraphAssertion
    from crawler.web.bavaria import BavariaPreviewRetriever

    quote = (
        "Das Institut Beispiel wurde mit der Erstellung eines Standortkonzepts "
        "für Freiflächenphotovoltaik beauftragt."
    )
    item = replace(
        passage(
            dict(
                unit_id="synthetic-author-unit",
                sha256="a" * 64,
                text=quote,
                ordinal=1,
                source_url="https://example.invalid",
                frame_ags=["09771112"],
            ),
            "synthetic-snapshot",
            1,
            "graph",
        ),
        assertions=(
            GraphAssertion(
                "synthetic-author",
                "artifact",
                "run",
                "office",
                "Institut Beispiel",
                "COMMISSIONED_FOR",
                "project",
                "Standortkonzept Freiflächen-PV",
                quote,
                required_source_ids=("synthetic-author-unit",),
            ),
        ),
        graph_artifact_ids=("artifact",),
    )
    if context_required:
        item = replace(
            item,
            assertions=(
                replace(
                    item.assertions[0],
                    required_source_ids=("synthetic-author-unit", "synthetic-context-unit"),
                ),
            ),
        )
    context = passage(
        dict(
            unit_id="synthetic-context-unit",
            sha256="a" * 64,
            text="Das Standortkonzept betrifft die beschriebenen Freiflächen der Gemeinde.",
            ordinal=2,
            source_url="https://example.invalid",
            frame_ags=["09771112"],
        ),
        "synthetic-snapshot",
        2,
        "graph",
    )
    admitted = (item, context) if context_required else (item,)
    calls = []

    class Baseline(BavariaPreviewRetriever):
        def __init__(self):
            self.workspace = SimpleNamespace(
                municipalities={"09771112": {"name": "Affing"}},
                snapshot_id="synthetic-snapshot",
                relations_db="synthetic-graph",
                discovery_index=SimpleNamespace(expand_query=lambda q: ()),
                resolve_ags=lambda supplied, question: ["09771112"],
                entities={
                    "office": {"label": "Institut Beispiel"},
                    "project": {"label": "Standortkonzept Freiflächen-PV"},
                },
                relation_rows=[
                    dict(
                        relation_id="synthetic-author",
                        predicate="COMMISSIONED_FOR",
                        subject_id="office",
                        object_id="project",
                        record=dict(quote=quote, claim_scope_ags=["09771112"]),
                    )
                ],
            )

        def check_identity(self):
            pass

        def retrieve(self, request, *, relation_ids=None):
            calls.append((request, relation_ids))
            return RetrievalResult(
                request.query,
                "graph",
                "synthetic-snapshot",
                "projection",
                admitted if relation_ids == frozenset({"synthetic-author"}) else (),
            )

    question = "Welche Organisation erstellt das Freiflächen-PV-Standortkonzept für Affing?"
    semantic = SimpleNamespace(
        status=lambda: {"complete": True}, discover=lambda *args: {"results": [], "diagnostics": {}}
    )
    result = ProductRetriever(
        Baseline(), semantic if mode is RetrievalMode.HYBRID else None
    ).retrieve(
        RetrievalRequest(
            question,
            "explicit-manifest",
            mode,
            projection_id="projection",
            municipality_ids=("09771112",),
        )
    )
    assert len(result.items) == (2 if context_required else 1)
    assert len(calls) == (3 if mode is RetrievalMode.HYBRID else 2)
    assert calls[-1][1] == frozenset({"synthetic-author"})
    assert calls[-1][0].mode is RetrievalMode.GRAPH
    assert calls[-1][0].graph_hops == 1 and calls[-1][0].top_k == 8
    assert result.query == question
    assert (
        next(arm for arm in result.diagnostics["arms"] if arm["method"] == "graph")[
            "role_selection"
        ]["baseline_items"]
        == 0
    )


def test_typed_role_second_pass_preserves_existing_status_and_shared_funder_gates():
    from crawler.web.query_planning import requested_graph_role_predicates

    assert "COMMISSIONED_FOR" in requested_graph_role_predicates(
        "Welche Organisation erstellt das Standortkonzept?"
    )
    for question in [
        "Welche Organisation erstellt aktuell das Standortkonzept?",
        "Wer hat das Standortkonzept bereits fertiggestellt?",
        "Wer fördert beide Gemeinden gemeinsam?",
    ]:
        assert requested_graph_role_predicates(question) == ()


def test_query_plan_marks_construction_and_report_time_conditions():
    from crawler.web.query_planning import plan_query

    workspace = SimpleNamespace(municipalities={"09771112": {"name": "Affing"}})
    plan = plan_query(
        workspace,
        "Welche Organisation erstellt das Standortkonzept und belegt dies "
        "eine fertig gebaute Anlage?",
        ("09771112",),
    )
    assert "implementation" in plan.operations
    dated = plan_query(
        workspace, "Welches Büro verfasste den Bericht und von wann stammt er?", ("09771112",)
    )
    assert "temporal" in dated.operations
