"""Real local source/canonical QA, not human scientific validation."""

import json
import sqlite3

from fastapi.testclient import TestClient
from test_pilot_release import technical_source

from crawler.core.answer_generation import GenerationInput, GenerationParameters
from crawler.web.app import create_app
from crawler.web.bavaria import create_bavaria_service
from crawler.web.evidence_synthesis import MODEL, EvidenceSynthesisProvider
from crawler.web.local_index import LocalIndex
from crawler.web.product_research import attach_research_product
from crawler.web.query_planning import plan_query
from research.bavaria.workspace.core import make_manifest


def make_app(tmp_path, generation_config=None, **app_options):
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest, _ = technical_source(tmp_path)
    ledger = json.loads(manifest.read_text())["artifacts"]["ledger"]["path"]
    with sqlite3.connect(ledger) as c:
        c.execute("UPDATE units SET text='Planung einer Waermeversorgung 2026. Kein Gold.'")
    manifest = tmp_path / "answer-workspace.json"
    make_manifest(ledger, None, manifest)
    service = create_bavaria_service(manifest, original_roots=[tmp_path])
    index = LocalIndex(service.workspace, tmp_path / "answer-terms.sqlite")
    service.workspace.discovery_index = index
    app = create_app(service, **app_options)
    attach_research_product(
        app, service, tmp_path / "research", generation_config=generation_config
    )
    return app


def test_real_free_question_answer_claim_spans_history_scope_and_restart(tmp_path):
    app = make_app(tmp_path)
    a, b = TestClient(app), TestClient(app)
    r = a.post(
        "/api/research/ask",
        json={
            "question": "Was ist über die Planung einer Wärmeversorgung dokumentiert?",
            "mode": "lexical_expanded",
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["result_kind"] == "answer"
    assert result["answer_result"]["status"] == "answered", result["answer_result"]
    assert "[C1]" in result["answer_result"]["answer_text"]
    assert result["answer_assessment"]["claims"]
    assert all(c["exact_span_supported"] for c in result["answer_assessment"]["claims"])
    key = result["result_id"]
    assert a.get("/api/research/history").json()["items"][0]["id"] == key
    assert b.get("/api/research/history/" + key).status_code == 404
    # New service composition, same cookie and snapshot, no simulated persisted judgments.
    again = TestClient(make_app(tmp_path / "other"))
    again.cookies.update(a.cookies)
    assert again.get("/api/research/history/" + key).status_code == 404
    source = result["evidence"][0]["source_metadata"]["archive_url"]
    assert a.get(source).status_code == 200
    followup = a.post(
        "/api/research/ask", json={"question": "Und was ist dazu dokumentiert?", "previous_id": key}
    )
    assert followup.status_code == 200
    assert "Rückfrage" in followup.json()["query_plan"]["rewritten"]
    assert (
        b.post("/api/research/ask", json={"question": "Und dazu?", "previous_id": key}).status_code
        == 422
    )


def test_no_evidence_abstains_without_inventing_absence(tmp_path):
    c = TestClient(make_app(tmp_path))
    r = c.post(
        "/api/research/ask",
        json={"question": "Welche Gletscherbilanz gibt es hier?", "mode": "lexical"},
    )
    assert r.status_code == 200
    assert r.json()["answer_result"]["status"] == "insufficient_evidence"
    assert r.json()["answer_assessment"]["claims"] == []


def test_source_structure_fragments_are_not_answers_but_factual_headings_remain():
    blocks = [
        dict(
            citation_label="C1",
            evidence_text="Vorsorgen für Krisen und Katastrophen",
            metadata=dict(source_id="navigation", municipality_ids=[]),
        ),
        dict(
            citation_label="C2",
            evidence_text="Pläne der Sanierungsgebiete im Stadtplan",
            metadata=dict(source_id="heading", municipality_ids=[]),
        ),
    ]
    provider = EvidenceSynthesisProvider()
    provider.source_structure = lambda metadata, text: dict(
        structural_only=True,
        kind="heading" if metadata["source_id"] == "heading" else "list_item",
        state="VERIFIED_SOURCE_STRUCTURE",
    )

    def ask(question, rows):
        return provider.generate(
            GenerationInput(
                "",
                question,
                "\n".join(
                    "<untrusted_evidence>" + json.dumps(b) + "</untrusted_evidence>" for b in rows
                ),
                tuple(b["citation_label"] for b in rows),
                "synthetic-structure",
                MODEL,
                GenerationParameters(),
            )
        )

    answer = ask(
        "Welche Maßnahmen dienen Krisen und Katastrophen sowie Sanierungsgebieten?", blocks
    )
    assert answer.text.startswith("INSUFFICIENT_EVIDENCE:")
    assert len(provider.selection_for(answer.text)["structure_rejections"]) == 2
    factual = dict(
        citation_label="C3",
        evidence_text="Der Wärmeplan wurde 2025 beschlossen.",
        metadata=dict(source_id="heading", municipality_ids=[]),
    )
    assert "2025 beschlossen" in ask("Wann wurde der Wärmeplan beschlossen?", [factual]).text
    from crawler.web.evidence_synthesis import structural_label_has_fact

    assert not structural_label_has_fact("Was ist kommunale Wärmeplanung?")
    assert not structural_label_has_fact("Termine der Stadtwerke 2026")
    assert structural_label_has_fact("Stromverbrauch: 4.000.000 kWh")
    assert structural_label_has_fact("Förderquote: 50 %")
    # The canonical abstention text is shared, but its trace must stay question-bound.
    question = "Welche Maßnahmen dienen Krisen und Katastrophen sowie Sanierungsgebieten?"
    ask("Welche anderen Belege gibt es?", [])
    from crawler.core.answer_generation import CANONICAL_INSUFFICIENT_EVIDENCE_TEXT

    assert (
        len(
            provider.selection_for(CANONICAL_INSUFFICIENT_EVIDENCE_TEXT, question)[
                "structure_rejections"
            ]
        )
        == 2
    )


def test_formal_heat_plan_question_does_not_substitute_historical_heat_network():
    historical = dict(
        citation_label="C1",
        evidence_text="Die Stadtwerke haben 2012 das Fernwärmenetz in der Neustadt ausgebaut.",
        metadata=dict(municipality_ids=[], heading_context="Stadterneuerung"),
    )
    formal = dict(
        citation_label="C2",
        evidence_text="Die Stadtwerke erstellen die kommunale Wärmeplanung für die Gemeinde.",
        metadata=dict(municipality_ids=[], heading_context="Kommunale Wärmeplanung"),
    )

    def ask(question, blocks):
        return EvidenceSynthesisProvider().generate(
            GenerationInput(
                "",
                question,
                "\n".join(
                    "<untrusted_evidence>" + json.dumps(b) + "</untrusted_evidence>" for b in blocks
                ),
                tuple(b["citation_label"] for b in blocks),
                "synthetic-topic",
                MODEL,
                GenerationParameters(),
            )
        )

    question = "Welche Rolle spielen Stadtwerke bei der Wärmeplanung?"
    assert ask(question, [historical]).text.startswith("INSUFFICIENT_EVIDENCE:")
    answer = ask(question, [historical, formal]).text
    assert "erstellen" in answer and "2012" not in answer
    assert "2012" in ask("Was machen die Stadtwerke beim Fernwärmenetz?", [historical]).text


def test_source_sentence_synthesis_preserves_numeric_magnitudes():
    block = dict(
        citation_label="C1",
        evidence_text="Der Stromverbrauch beträgt 4.000.000 kWh im Jahr 2022.",
        metadata={"municipality_ids": ["09162000"], "heading_context": "Bericht2022"},
    )
    value = GenerationInput(
        "",
        "Wie hoch ist der Stromverbrauch 2022?",
        "<untrusted_evidence>\n" + json.dumps(block) + "\n</untrusted_evidence>",
        ("C1",),
        "grounded-answer-v1",
        MODEL,
        GenerationParameters(),
    )
    response = EvidenceSynthesisProvider().generate(value)
    assert "4.000.000 kWh" in response.text
    assert "[C1]" in response.text


def test_ambiguous_place_never_silently_guessed():
    class W:
        municipalities = {"1": {"name": "Neustadt"}, "2": {"name": "Neustadt"}}

        def resolve_ags(self, *args):
            raise ValueError("Ambiguous municipality name; supply explicit --ags")

    plan = plan_query(W(), "Was macht Neustadt beim Klimaschutz?")
    assert len(plan.clarification) == 2
    assert not plan.municipality_ids


def test_persistent_answer_and_original_reopen_after_fresh_process_composition(tmp_path):
    app = make_app(tmp_path)
    a = TestClient(app)
    r = a.post(
        "/api/research/ask",
        json={"question": "Welche Planung einer Wärmeversorgung ist dokumentiert?"},
    ).json()
    key = r["result_id"]
    service = create_bavaria_service(tmp_path / "answer-workspace.json", original_roots=[tmp_path])
    service.workspace.discovery_index = LocalIndex(
        service.workspace, tmp_path / "answer-terms.sqlite"
    )
    reopened = create_app(service)
    attach_research_product(reopened, service, tmp_path / "research")
    b = TestClient(reopened)
    b.cookies.update(a.cookies)
    assert b.get("/api/research/history/" + key).json()["answer_result"] == r["answer_result"]
    assert b.get(r["evidence"][0]["source_metadata"]["archive_url"]).status_code == 200
    exported = b.get("/api/research/history/" + key + "/export/json")
    assert exported.status_code == 200
    assert exported.json()["answer_result"]["answer_text"] == r["answer_result"]["answer_text"]
    from crawler.core.answer_generation import AnswerResult

    assert isinstance(service.store.get(key).canonical, AnswerResult)


def test_product_queries_share_existing_request_budget(tmp_path):
    app = make_app(tmp_path, query_budget_per_minute=2)
    c = TestClient(app)
    body = {"question": "Welche Planung einer Wärmeversorgung ist dokumentiert?"}
    assert c.post("/api/query", json={**body, "mode": "lexical"}).status_code == 200
    assert c.post("/api/research/ask", json=body).status_code == 200
    assert c.post("/api/research/ask", json=body).status_code == 429
    assert c.post("/api/query", json={**body, "mode": "lexical"}).status_code == 429


def test_product_graph_rejects_general_bavaria_word_overlap():
    import hashlib

    from crawler.core.provenance import evidence_id_for
    from crawler.core.retrieval import (
        DocumentSource,
        GraphAssertion,
        RetrievalMode,
        RetrievalRequest,
        RetrievalResult,
        RetrievalTrace,
        RetrievedEvidence,
    )
    from crawler.web.product_retrieval import ProductRetriever

    quote = "In Bayern werden Soforthilfen für Schäden durch Hochwasser gewährt."

    class W:
        municipalities = {}
        relations_db = "synthetic-relations"
        snapshot_id = "synthetic-snapshot"
        entities = {"s": {"label": "Bayern"}, "o": {"label": "Soforthilfen"}}
        relation_rows = [
            {
                "relation_id": "r",
                "subject_id": "s",
                "object_id": "o",
                "required_source_ids": ["unit"],
                "record": {"quote": quote},
            }
        ]

        class Index:
            @staticmethod
            def expand_query(q):
                return ()

        discovery_index = Index()

        def resolve_ags(self, *args):
            return []

    assertion = GraphAssertion(
        "r",
        "artifact",
        "run",
        "s",
        "Bayern",
        "FUNDS",
        "o",
        "Soforthilfen",
        quote,
        required_source_ids=("unit",),
    )
    item = RetrievedEvidence(
        "synthetic-snapshot",
        evidence_id_for("segment", "unit", hashlib.sha256(quote.encode()).hexdigest()),
        "segment",
        "unit",
        "unit",
        None,
        DocumentSource("source-version:fixture", "https://example.test", "fixture", ()),
        quote,
        (),
        ("artifact",),
        (assertion,),
        (RetrievalTrace("graph", 1, 1),),
        1,
        1,
    )

    class Baseline:
        workspace = W()

        def check_identity(self):
            pass

        def retrieve(self, request):
            return RetrievalResult(
                request.query, "graph", "synthetic-snapshot", "projection", (item,)
            )

    request = RetrievalRequest(
        "Welche Quellen in Bayern nennen einen Hitzeaktionsplan?",
        "fixture",
        RetrievalMode.GRAPH,
        projection_id="projection",
    )
    result = ProductRetriever(Baseline()).retrieve(request)
    assert result.items == ()
    assert result.diagnostics["arms"][0]["selected_before"] == 1
    assert result.diagnostics["arms"][0]["selected_after"] == 0
    assert Baseline().retrieve(request).items == (item,)
    from dataclasses import replace

    supported = ProductRetriever(Baseline()).retrieve(
        replace(request, query="Welche Soforthilfen nennt Bayern?")
    )
    assert supported.items and supported.items[0].assertions
    assert supported.items[0].assertions[0].required_source_ids == ("unit",)


def test_funding_role_can_use_bound_topic_heading_without_role_fabrication():
    block = dict(
        citation_label="C1",
        evidence_text="Fördergeber: Bundesministerium für Wirtschaft und Klimaschutz (BMWK)",
        metadata={
            "municipality_ids": ["09577151"],
            "heading_context": "Kommunale Wärmeplanung Nennslingen",
        },
    )
    value = GenerationInput(
        "",
        "Wer finanziert die Wärmeplanung in Nennslingen?",
        "<untrusted_evidence>\n" + json.dumps(block) + "\n</untrusted_evidence>",
        ("C1",),
        "grounded-answer-v1",
        MODEL,
        GenerationParameters(),
    )
    response = EvidenceSynthesisProvider().generate(value)
    assert "Bundesministerium für Wirtschaft und Klimaschutz" in response.text
    assert "[C1]" in response.text


def test_abbreviated_projection_keeps_qualifier_and_canonical_citations():
    from crawler.web.evidence_synthesis import evidence_kind, sentences

    text = (
        "Über einen Betrachtungszeitraum von 20 Jahren gesehen "
        "werden so ca. 64 Tonnen CO2 eingespart."
    )
    assert list(sentences(text)) == [text]
    assert evidence_kind(text) == "PROJECTION_OR_POTENTIAL"
    block = dict(citation_label="C1", evidence_text=text, metadata={"municipality_ids": []})
    value = GenerationInput(
        "",
        "Worauf beziehen sich 64 Tonnen CO2?",
        "<untrusted_evidence>" + json.dumps(block) + "</untrusted_evidence>",
        ("C1",),
        "grounded-answer-v1",
        MODEL,
        GenerationParameters(),
    )
    answer = EvidenceSynthesisProvider().generate(value).text
    assert "Betrachtungszeitraum von 20 Jahren" in answer
    assert "ca [C1]. 64" in answer


def test_pending_generation_is_session_owned_and_keeps_shared_lock(tmp_path):
    import threading
    import time

    app = make_app(tmp_path, response_timeout=0.02)
    product = app.state.research_product
    product.generative = True  # Exercise job transport with an isolated slow fixture, no model.
    product.provider.validation_for = lambda _: None
    original = product.ask
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    product.ask = slow
    with TestClient(app) as client:
        r = client.post(
            "/api/research/ask", json={"question": "Was ist zur Wärmeversorgung geplant?"}
        )
        assert started.is_set() and r.status_code == 202, r.text
        job = r.json()["poll_url"]
        assert client.get(job).status_code == 202
        # A different browser session cannot inspect the pending job.
        cookie = client.cookies.get("klima_research_session")
        client.cookies.clear()
        assert client.get(job).status_code == 404
        client.cookies.set("klima_research_session", cookie)
        assert (
            client.post(
                "/api/query", json={"question": "Wärmeversorgung", "mode": "lexical"}
            ).status_code
            == 429
        )
        release.set()
        for _ in range(100):
            result = client.get(job)
            if result.status_code != 202:
                break
            time.sleep(0.01)
        assert result.status_code == 200, result.text
        key = result.json()["result_id"]
        assert client.get("/api/research/history/" + key).status_code == 200
        assert client.get(job).json()["result_id"] == key


def test_explicit_model_composition_records_parameters_and_keeps_paraphrases_distinct(
    tmp_path, monkeypatch
):
    import crawler.web.grounded_provider as grounded
    from crawler.core.answer_generation import ProviderResponse

    class LocalFixture:
        def generate(self, value):
            block = next(iter(grounded.source_blocks(value.evidence_context).values()))
            return ProviderResponse(
                json.dumps(
                    {
                        "claims": [
                            {
                                "text": (
                                    "Die Quelle beschreibt die Planung einer Wärmeversorgung 2026."
                                ),
                                "kind": "source_fact",
                                "evidence": [
                                    {
                                        "label": block["citation_label"],
                                        "quote": "Planung einer Waermeversorgung 2026.",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                value.model,
                100,
                50,
                150,
            )

    monkeypatch.setattr(grounded, "OllamaGenerationProvider", lambda _: LocalFixture())
    monkeypatch.setattr(grounded.EvidenceBoundGenerationProvider, "_check_model", lambda _: None)
    config = grounded.GroundedModelConfig("http://127.0.0.1:11435", "synthetic-local", "a" * 64)
    client = TestClient(make_app(tmp_path, generation_config=config))
    result = client.post(
        "/api/research/ask",
        json={"question": "Was ist über die Planung einer Wärmeversorgung dokumentiert?"},
    ).json()
    assert result["answer_result"]["status"] == "answered", result
    generation = result["answer_result"]["generation"]
    assert generation["provider"] == "ollama_grounded"
    assert generation["parameters"]["context_window_tokens"] == 8192
    assessment = result["answer_assessment"]
    assert assessment["method"] == grounded.PROTOCOL
    assert assessment["claims"][0]["exact_span_supported"] is False
    assert (
        assessment["claims"][0]["source_spans"][0]["quote"]
        == "Planung einer Waermeversorgung 2026."
    )
    assert "NOT_HUMAN_VALIDATED" in assessment["claims"][0]["status"]


def test_policy_topic_noun_does_not_override_documented_implementation():
    from crawler.web.evidence_synthesis import evidence_kind

    assert (
        evidence_kind("Die kommunale Wärmeplanung wurde abgeschlossen.")
        == "REPORTED_IMPLEMENTATION"
    )
    assert (
        evidence_kind("Das Ministerium finanziert die kommunale Wärmeplanung.")
        == "DOCUMENTED_SOURCE_STATEMENT"
    )
    assert evidence_kind("Die kommunale Wärmeplanung ist für 2026 geplant.") == "TARGET_OR_PLAN"
    assert evidence_kind("Das Vorhaben ist noch in Planung.") == "TARGET_OR_PLAN"


def test_human_review_layer_updates_saved_answer_without_mutation_or_blindness_leak(tmp_path):
    from crawler.web.review_feedback import ReviewFeedback
    from crawler.web.review_protocol import ReviewProtocol

    app = make_app(tmp_path)
    client = TestClient(app)
    result = client.post(
        "/api/research/ask",
        json={"question": "Was ist über die Planung einer Wärmeversorgung dokumentiert?"},
    ).json()
    evidence = result["evidence"][0]
    p = ReviewProtocol(
        tmp_path / "isolated-reviews",
        result["snapshot"],
        technical=True,
        source_resolver=lambda source: {
            **source,
            "unit_id": evidence["source_id"],
            "text": evidence["evidence_text"],
        },
    )
    app.state.review_feedback = ReviewFeedback(p)
    for name, role in [("first", "reviewer"), ("second", "reviewer"), ("third", "adjudicator")]:
        p.create_account(
            "owner", name, "separate-test-password-" + name, identity="Synthetic " + name, role=role
        )
    claim = result["answer_assessment"]["claims"][0]
    import hashlib

    oid = p.register(
        "owner",
        kind="claim",
        key="synthetic-answer-feedback",
        payload={
            "proposed_text": result["answer_result"]["answer_text"],
            "claim_id": claim["id"],
            "question": result["retrieval_result"]["query"],
            "output_sha256": hashlib.sha256(
                result["answer_result"]["answer_text"].encode()
            ).hexdigest(),
        },
        sources=[
            {
                "sha256": evidence["document"]["document_id"].split(":")[-1],
                "unit_id": evidence["source_id"],
                "quote": evidence["evidence_text"],
            }
        ],
    )
    p.assign("owner", oid, ["first", "second"], "third")
    f = dict(
        decision="SUPPORTED",
        comment="Synthetic technical fixture",
        corrections={},
        independence_confirmed=True,
        evidence_inspected=True,
    )
    p.save("first", oid, f)
    p.save("second", oid, f)
    public = client.get("/api/research/history/" + result["result_id"]).json()
    review = public["answer_assessment"]["claims"][0]["review"]
    assert review["decision"] is None and review["human_gold"] is False
    assert public["answer_result"] == result["answer_result"]
    password = (
        (p.directory / "owner-credentials.txt").read_text().split("password: ")[1].splitlines()[0]
    )
    client.cookies.set("review_protocol_session", p.login("owner", password))
    private = client.get("/api/research/history/" + result["result_id"]).json()
    assert private["answer_assessment"]["claims"][0]["review"]["decision"] == "SUPPORTED"
    assert (
        "review"
        not in app.state.research_product.history.get(
            client.cookies.get("klima_research_session"), result["result_id"]
        )["answer_assessment"]["claims"][0]
    )


def test_source_bound_actor_and_projection_survive_low_sentence_cosine():
    import numpy as np

    project = (
        "Im Norden ist ein gemeinsamer Windpark geplant. "
        "Es wird ein Ertrag von etwa 34 GWh jährlich erwartet."
    )
    actor = "Das Projekt wird von der Firma Beispielwind GmbH geplant."
    blocks = [
        dict(
            citation_label="C1",
            evidence_text=project,
            metadata=dict(municipality_ids=[], heading_context="Windpark Muster"),
            graph_assertions=[
                dict(
                    assertion_id="synthetic-generation",
                    subject="Windpark Muster",
                    relation="PROJECTED_ANNUAL_GENERATION",
                    object="34 GWh",
                    evidence_excerpt=project,
                )
            ],
        ),
        dict(
            citation_label="C2",
            evidence_text=actor,
            metadata=dict(municipality_ids=[], heading_context="Windpark Muster"),
            graph_assertions=[
                dict(
                    assertion_id="synthetic-planner",
                    subject="Beispielwind GmbH",
                    relation="PLANS",
                    object="Windpark Muster",
                    evidence_excerpt=actor,
                )
            ],
        ),
    ]

    class Encoder:
        def encode(self, texts):
            return np.array(
                [
                    [1.0, 0.0] if i == 0 or text.startswith("Im Norden") else [0.0, 1.0]
                    for i, text in enumerate(texts)
                ]
            )

    provider = EvidenceSynthesisProvider(encoder=lambda: Encoder())
    value = GenerationInput(
        "",
        "Wer plant den Windpark Muster und welcher Jahresertrag wird erwartet?",
        "\n".join("<untrusted_evidence>" + json.dumps(b) + "</untrusted_evidence>" for b in blocks),
        ("C1", "C2"),
        "synthetic-test",
        MODEL,
        GenerationParameters(),
    )
    response = provider.generate(value)
    assert "Beispielwind GmbH" in response.text
    assert "etwa 34 GWh jährlich erwartet" in response.text
    assert response.text.index("Beispielwind GmbH") < response.text.index("34 GWh")
    assert "SYNTHETIC" not in response.text
    assert provider.selection_for(response.text)["source_bound_aspects_preserved"]


def test_expected_generation_is_projection_not_measured_effect():
    from crawler.web.evidence_synthesis import evidence_kind

    assert (
        evidence_kind("Es wird ein Ertrag von etwa 34 GWh jährlich erwartet.")
        == "PROJECTION_OR_POTENTIAL"
    )


def test_aspect_preservation_requires_actual_quote_argument_and_projection():
    from crawler.web.evidence_synthesis import source_bound_aspects
    from research.bavaria.workspace.core import roots

    sentence = "Das Projekt wird von der Firma Beispielwind GmbH geplant."
    assertion = dict(
        subject="Beispielwind GmbH",
        object="Windpark Muster",
        relation="PLANS",
        evidence_excerpt=sentence,
    )
    block = dict(metadata=dict(heading_context="Windpark Muster"), graph_assertions=[assertion])
    question = "Wer plant den Windpark Muster?"
    terms = set(roots(question))
    assert source_bound_aspects(question, sentence, block, terms) == ["planner"]
    assertion["evidence_excerpt"] = "Eine andere Aussage ist dokumentiert."
    assert source_bound_aspects(question, sentence, block, terms) == []
    assertion.update(evidence_excerpt=sentence, subject="Unbelegte Firma")
    assert source_bound_aspects(question, sentence, block, terms) == []
    measured = "Der gemessene Jahresertrag beträgt 34 GWh."
    assertion.update(
        subject="Windpark Muster",
        object="34 GWh",
        relation="PROJECTED_ANNUAL_GENERATION",
        evidence_excerpt=measured,
    )
    assert source_bound_aspects("Welcher Jahresertrag wird erwartet?", measured, block, terms) == []


def test_markdown_research_export_keeps_claim_status_method_and_source_identity(tmp_path):
    client = TestClient(make_app(tmp_path))
    response = client.post(
        "/api/research/ask",
        json=dict(
            question="Welche Planung einer Wärmeversorgung ist dokumentiert?", mode="lexical"
        ),
    )
    assert response.status_code == 200
    result = response.json()
    exported = client.get("/api/research/history/" + result["result_id"] + "/export/md")
    assert exported.status_code == 200
    assert "## Aussagen und Prüfstatus" in exported.text
    assert "## Methodentrace" in exported.text and MODEL in exported.text
    assert "Quellenfassung: source-version:" in exported.text
    assert "AUTOMATIC_EXTRACTIVE_NOT_HUMAN_VALIDATED" in exported.text


def test_source_bound_titlepage_author_and_date_without_contact_boilerplate():
    import numpy as np

    text = (
        "Projekt Kommunale Wärmeplanung Beispielort\n"
        "Bearbeiter Beispiel Ingenieure GmbH\n"
        "Kontakt kontakt@example.invalid\n"
        "Datum 15.07.2026"
    )
    block = dict(
        citation_label="C1",
        evidence_text=text,
        metadata=dict(municipality_ids=[], heading_context="Eignungsprüfung Wärmeplanung"),
        graph_assertions=[
            dict(
                subject="Beispiel Ingenieure GmbH",
                object="Eignungsprüfung Wärmeplanung",
                relation="AUTHORS",
                evidence_excerpt=text,
            ),
            dict(
                subject="Eignungsprüfung Wärmeplanung",
                object="15.07.2026",
                relation="REPORT_DATED",
                evidence_excerpt=text,
            ),
        ],
    )

    class Encoder:
        def encode(self, texts):
            return np.array([[1.0, 0.0] if i == 0 else [0.0, 1.0] for i, _ in enumerate(texts)])

    provider = EvidenceSynthesisProvider(encoder=lambda: Encoder())
    response = provider.generate(
        GenerationInput(
            "",
            "Welches Büro wurde für die Eignungsprüfung zur Wärmeplanung genannt "
            "und von wann stammt der Bericht?",
            "<untrusted_evidence>" + json.dumps(block) + "</untrusted_evidence>",
            ("C1",),
            "synthetic-titlepage",
            MODEL,
            GenerationParameters(),
        )
    )
    assert "Bearbeiter Beispiel Ingenieure GmbH" in response.text
    assert "Datum 15.07.2026" in response.text
    assert "mailto:" not in response.text and "example.invalid" not in response.text


def test_source_bound_funding_interval_survives_compound_question_sentence_reranking():
    import numpy as np

    funder = "Fördergeber: Bundesministerium für synthetische Förderfälle"
    period = "Laufzeit Die Förderphase läuft von 01.09.2024 – 31.12.2025"
    blocks = []
    for label, text, predicate, obj in [
        ("C1", funder, "FUNDS", "Wärmeplanung Musterort"),
        ("C2", period, "FUNDING_DURING", "01.09.2024–31.12.2025"),
    ]:
        blocks.append(
            dict(
                citation_label=label,
                evidence_text=text,
                metadata=dict(
                    municipality_ids=["09577151"], heading_context="Wärmeplanung Musterort"
                ),
                graph_assertions=[
                    dict(
                        subject="Bundesministerium für synthetische Förderfälle"
                        if label == "C1"
                        else "Wärmeplanung Musterort",
                        relation=predicate,
                        object=obj,
                        evidence_excerpt=text,
                    )
                ],
            )
        )

    class Encoder:
        def encode(self, texts):
            return np.array(
                [
                    [1.0, 0.0] if i == 0 or text.startswith("Fördergeber") else [0.0, 1.0]
                    for i, text in enumerate(texts)
                ]
            )

    provider = EvidenceSynthesisProvider(
        {"09577151": {"name": "Musterort"}}, encoder=lambda: Encoder()
    )
    response = provider.generate(
        GenerationInput(
            "",
            "Wer ist für den Wärmeplan in Musterort als Geldgeber genannt; "
            "wann lief die Förderung?",
            "\n".join(
                "<untrusted_evidence>" + json.dumps(b) + "</untrusted_evidence>" for b in blocks
            ),
            ("C1", "C2"),
            "synthetic-funding-period",
            MODEL,
            GenerationParameters(),
        )
    )
    assert funder in response.text
    assert period in response.text
    assert any(
        "funding_period" in row["aspects"]
        for row in provider.selection_for(response.text)["source_bound_aspects_preserved"]
    )


def test_role_answer_omits_pure_document_link_but_keeps_factual_link_sentence():
    question = "Welche Organisation erstellt das Standortkonzept für Photovoltaik?"
    texts = [
        "Das Standortkonzept für Photovoltaik-Freiflächenanlagen finden Sie hier.",
        "Das Institut Beispiel wurde mit dem Standortkonzept für Photovoltaik beauftragt.",
        "Das Standortkonzept wurde 2025 beschlossen; weitere Informationen finden Sie hier.",
    ]
    blocks = [
        dict(
            citation_label="C" + str(i),
            evidence_text=text,
            metadata=dict(municipality_ids=[], heading_context="Standortkonzept Photovoltaik"),
        )
        for i, text in enumerate(texts, 1)
    ]
    value = GenerationInput(
        "",
        question,
        "\n".join("<untrusted_evidence>" + json.dumps(b) + "</untrusted_evidence>" for b in blocks),
        ("C1", "C2", "C3"),
        "synthetic-navigation",
        MODEL,
        GenerationParameters(),
    )
    response = EvidenceSynthesisProvider().generate(value)
    assert texts[0].rstrip(".") not in response.text
    assert "Institut Beispiel" in response.text
    assert "2025 beschlossen" in response.text
