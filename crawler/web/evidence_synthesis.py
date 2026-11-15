"""Deterministic extractive answer provider, never a simulated language model.

This low-resource path composes source sentences and explicit source associations.
It abstains from unsupported reasoning; optional generative providers retain the
canonical provider/grounding boundary and must be configured separately.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading

from crawler.core.answer_generation import CANONICAL_INSUFFICIENT_EVIDENCE_TEXT, ProviderResponse
from crawler.web.local_index import LOCAL_EXPANSION
from crawler.web.query_planning import (
    content_terms,
    product_query_expansion,
    requested_graph_role_predicates,
)
from research.bavaria.workspace.core import norm, roots

MODEL = "source-sentence-synthesis-v8"


def sentences(text):
    """Preserve exact spans and common abbreviations, including quantitative qualifiers."""
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n{2,}", text):
        prefix = text[start : boundary.start()]
        if re.search(r"(?:ca|bzw|Abs|Nr|Dr|Prof|z|d|u|a)\.$", prefix, re.I):
            continue
        value = prefix.strip()
        if 25 <= len(value) <= 1800 and len(value.split()) >= 4:
            yield value
        start = boundary.end()
    value = text[start:].strip()
    if 25 <= len(value) <= 1800 and len(value.split()) >= 4:
        yield value


def evidence_kind(text):
    folded = norm(" ".join(text.split()))
    if re.search(
        r"prognos|voraussichtlich|szenario|potenzial|betrachtungszeitraum|hochrechnung|"
        r"\b(?:wird|werden)\b.*\berwartet\b",
        folded,
    ):
        return "PROJECTION_OR_POTENTIAL"
    if re.search(
        r"ziel|geplant|\bplant\b|\bin (?:der )?planung\b|\bsoll\b|angestrebt|vorgesehen", folded
    ):
        return "TARGET_OR_PLAN"
    if re.search(r"umgesetzt|abgeschlossen|fertiggestellt|in betrieb|wurde.*installiert", folded):
        return "REPORTED_IMPLEMENTATION"
    if re.search(r"gemessen|messung", folded):
        return "REPORTED_MEASUREMENT_NOT_CAUSAL_PROOF"
    if re.search(r"verbrauch|bilanz|einsparung|emission", folded):
        return "REPORTED_QUANTITY_MEASUREMENT_STATUS_UNCONFIRMED"
    return "DOCUMENTED_SOURCE_STATEMENT"


def navigation_only_sentence(sentence):
    """Conservative pure-link recognition; factual clauses and dates stay intact."""
    text = " ".join(sentence.split())
    return bool(
        re.search(
            r"finden sie hier|hier finden sie|hier herunterladen|mehr erfahren|"
            r"hier können sie.*erfahren|^weitere informationen|^informationen zu|"
            r"^mehr zum|^lesen sie|^erfahren sie",
            text,
            re.I,
        )
        and not re.search(
            r"\d|\b(?:beauftragt|erstellt|erarbeitet|beschlossen|umgesetzt|"
            r"gebaut|betreibt|geplant|finanziert|gefördert|unterstützt|bearbeiter|"
            r"auftraggeber|fördergeber|betreiber|autor)\w*",
            text,
            re.I,
        )
    )


def formal_heat_plan_topic(text):
    return bool(re.search(r"\bw(?:a|ae)rmeplan(?:ung\w*|e[ns]?|s)?\b", norm(text)))


def block_has_heat_plan_topic(block):
    metadata = block["metadata"]
    return formal_heat_plan_topic(
        " ".join(
            [
                block["evidence_text"],
                metadata.get("heading_context") or "",
                metadata.get("source_url") or "",
            ]
        )
    )


def structural_label_has_fact(sentence):
    if sentence.rstrip().endswith("?"):
        return False
    return bool(
        re.search(
            r"\b(?:wurde|wurden|wird|werden|ist|sind|hat|haben|beschlossen|beauftragt|"
            r"umgesetzt|abgeschlossen|beträgt|betreibt|fördert|gefördert)\b|"
            r"\d[\d., ]*\s*(?:(?:kWh|MWh|GWh|Prozent|Euro|Tonnen)\b|[%€])|"
            r"\b(?:Datum|Stand|Zieljahr|Basisjahr)\s*:\s*\d|"
            r"\bklimaneutral\w*\b.*\b20\d{2}\b|\b20\d{2}\b.*\bklimaneutral\w*\b",
            sentence,
            re.I,
        )
    )


def source_bound_aspects(question, sentence, block, terms):
    """Heuristic aspect coverage from original spans, never new graph conclusions."""
    text = norm(" ".join(sentence.split()))
    roles = []
    who = bool(
        re.search(
            r"\b(?:wer|wem)\b|welche\w*\s+(?:firma|institut|akteur|organisation|unternehmen|büro|bearbeiter|ministeri\w*|geldgeber|fördergeber)",
            question,
            re.I,
        )
    )
    if who and re.search(
        r"\bplant\b|planer|erstell|erarbeit|beauftragt|autor|büro|bearbeiter", question, re.I
    ):
        roles.append(
            (
                "planner",
                {"PLANS", "AUTHORS", "COMMISSIONS", "COMMISSIONED_FOR"},
                r"geplant|\bplant\b|erstellt|erarbeitet|beauftragt|autor|verfasst|bearbeiter",
            )
        )
    if who and re.search(r"finanz|förder|bezahlt|geldgeber", question, re.I):
        roles.append(
            (
                "funder",
                {"FUNDS", "FUNDED_BY", "SUPPORTED_BY"},
                r"gefördert|fördert|finanziert|fördergeber|geldgeber",
            )
        )
    if who and re.search(r"betreib|versorger", question, re.I):
        roles.append(("operator", {"OPERATES"}, r"betreibt|betrieben|betreiber"))
    projection = bool(
        re.search(r"jahresertrag|stromertrag|ertrag|stromerzeug|jahresproduktion", question, re.I)
    )
    report_date = bool(
        re.search(
            r"von wann|wann.*(?:bericht|quelle|stammt)|bericht.*(?:datum|datiert)|berichtsdatum",
            question,
            re.I,
        )
    )
    funding_period = bool(
        re.search(r"\b(?:wann|laufzeit|zeitraum|förderphase)\b", question, re.I)
        and re.search(r"förder|finanz|geldgeber", question, re.I)
    )
    found = set()
    for assertion in block.get("graph_assertions", []):
        excerpt = norm(" ".join(str(assertion.get("evidence_excerpt", "")).split()))
        if not text or text not in excerpt:
            continue
        labels = " ".join(str(assertion.get(key, "")) for key in ["subject", "object"])
        heading = block.get("metadata", {}).get("heading_context") or ""
        if not terms & set(roots(labels + " " + heading)):
            continue

        def contains(argument):
            value = norm(str(argument or ""))
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}\s*[–-]\s*\d{2}\.\d{2}\.\d{4}", value):
                dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", value)
                pattern = r"\s*[–-]\s*".join(re.escape(date) for date in dates)
            else:
                pattern = re.escape(value)
            return len(value) >= 3 and bool(re.search(r"(?<!\w)" + pattern + r"(?!\w)", text))

        relation = assertion.get("relation")
        if report_date and relation == "REPORT_DATED" and contains(assertion.get("object")):
            found.add("report_date")
        if funding_period and relation == "FUNDING_DURING" and contains(assertion.get("object")):
            found.add("funding_period")
        for name, predicates, verb in roles:
            if (
                relation in predicates
                and re.search(verb, sentence, re.I)
                and (contains(assertion.get("subject")) or contains(assertion.get("object")))
            ):
                found.add(name)
        if (
            projection
            and relation == "PROJECTED_ANNUAL_GENERATION"
            and contains(assertion.get("object"))
            and evidence_kind(sentence) == "PROJECTION_OR_POTENTIAL"
        ):
            found.add("annual_projection")
    return sorted(found)


# Grammatical/context words may help retrieval, but cannot carry an answer aspect.
ANSWER_CONTEXT = set(
    roots(
        "wer wem womit wann wo warum wie welche welcher welchen wird werden ist sind "
        "wurde wurden hat haben ich möchte nur konkret steht irgendwo dafür eigentliche "
        "beschrieben übernehmen Rolle hergeben dies diese dieses schon bloß angekündigt "
        "belegt Passage beschreibt nennt Maßnahmen Projekte Ansatz Förderung Förderansatz "
        "kommunal städtisch Planung Ergebnis bitte genau dokumentiert Quellen laut Archiv"
    )
)


def answer_relevance_reason(question, sentence, terms, facets, metadata):
    """Conservative eligibility, separate from semantic ranking and span grounding.

    An original sentence must carry a requested aspect itself. A heading or a
    high cosine score cannot supply a missing subject or role. This is an
    engineering guard, not an independent entailment or truth classifier.
    """
    if sentence.rstrip().endswith("?") or navigation_only_sentence(sentence):
        return "QUESTION_OR_NAVIGATION_NOT_AN_ANSWER"
    if facets:
        return None  # Already bound to a requested typed assertion and exact span.
    focus = {t.replace("waerme", "warme") for t in terms - ANSWER_CONTEXT}
    words = {t.replace("waerme", "warme") for t in roots(sentence)}
    matched = {
        term
        for term in focus
        if term in words or (len(term) >= 6 and any(word.startswith(term) for word in words))
    }
    # A labelled funding field can use its verified section as the project subject;
    # generic prose cannot borrow its answer aspect from that heading.
    funding_field = re.match(r"^Fördergeber\s*:", sentence, re.I)
    heading = set(roots(metadata.get("heading_context") or ""))
    if not matched and not (funding_field and focus & heading):
        return "NO_SENTENCE_LEVEL_ANSWER_ANCHOR"
    funding = re.search(r"förder|finanz|geldgeber|bezahlt", question, re.I)
    who = re.search(
        r"\b(?:wer|wem)\b|welche\w*\s+(?:organisation|firma|büro|ministerium)", question, re.I
    )
    if (
        who
        and funding
        and not re.search(
            r"fördergeber|ministerium|gefördert|fördert|finanziert|geldgeber|projektträger",
            sentence,
            re.I,
        )
    ):
        return "MISSING_REQUESTED_FUNDING_ROLE"
    # A subsidy topic alone does not answer what is being subsidized.
    if funding and not who:
        if not re.search(r"förder|finanz|zuschuss|bezuschuss", sentence, re.I):
            return "MISSING_REQUESTED_FUNDING_STATEMENT"
        subjects = focus - set(roots("fördergeld Förderkennzeichen Förderung Förderphase Laufzeit"))
        if subjects and not (matched & subjects):
            return "MISSING_REQUESTED_FUNDING_SUBJECT"
    return None


class EvidenceSynthesisProvider:
    name = "local_extractive"

    def __init__(self, municipalities=None, encoder=None, source_structure=None):
        self.municipalities = municipalities or {}
        self.encoder = encoder
        self.source_structure = source_structure
        self._selections = {}
        self._selection_lock = threading.Lock()

    def selection_for(self, answer, question=None):
        with self._selection_lock:
            return json.loads(
                json.dumps(self._selections.get(self._selection_key(answer, question), {}))
            )

    @staticmethod
    def _selection_key(answer, question):
        return hashlib.sha256(
            json.dumps([answer, question], ensure_ascii=False).encode()
        ).hexdigest()

    def _remember(self, answer, question, diagnostic, aliases=()):
        with self._selection_lock:
            for text in (answer, *aliases):
                for query in (None, question):
                    self._selections[self._selection_key(text, query)] = diagnostic
            while len(self._selections) > 64:
                self._selections.pop(next(iter(self._selections)))

    def generate(self, generation_input):
        question = generation_input.user_question
        primary_question = question.split(" — Rückfrage-Kontext:")[0]
        names = [r.get("name", "") for r in self.municipalities.values()]
        terms = content_terms(question, names)
        expanded = set(terms)
        controlled_expansion = product_query_expansion(primary_question)
        expanded.update(roots(" ".join(controlled_expansion)))
        for key, values in LOCAL_EXPANSION.items():
            if norm(key) in norm(question):
                expanded.update(roots(" ".join(values)))
        years = set(re.findall(r"\b(?:18|19|20)\d{2}\b", question))
        blocks = [
            json.loads(block)
            for block in re.findall(
                r"<untrusted_evidence>\s*(.*?)\s*</untrusted_evidence>",
                generation_input.evidence_context,
                re.S,
            )
        ]
        ranked = []
        aspects = {}
        minimum = None
        role_query = bool(requested_graph_role_predicates(primary_question))
        navigation_rejections = []
        structure_rejections = []
        topic_rejections = []
        relevance_rejections = []
        require_heat_plan = formal_heat_plan_topic(primary_question)
        heat_plan_documents = {
            b["metadata"].get("document_id")
            for b in blocks
            if b["metadata"].get("document_id")
            and block_has_heat_plan_topic(b)
            and (
                formal_heat_plan_topic(b["metadata"].get("source_url") or "")
                or structural_label_has_fact(b["evidence_text"])
            )
        }
        structures = (
            self.source_structure.inspect_many(blocks)
            if hasattr(self.source_structure, "inspect_many")
            else None
        )
        for i, block in enumerate(blocks):
            text = block["evidence_text"]
            if (
                require_heat_plan
                and not block_has_heat_plan_topic(block)
                and block["metadata"].get("document_id") not in heat_plan_documents
            ):
                topic_rejections.append(
                    dict(
                        citation_label=block["citation_label"],
                        reason="FORMAL_HEAT_PLAN_TOPIC_NOT_ESTABLISHED",
                        evidence_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    )
                )
                continue
            structure = (
                structures[i]
                if structures is not None
                else (
                    self.source_structure(block["metadata"], text) if self.source_structure else {}
                )
            )
            candidates = list(sentences(text))
            has_bound_metadata = False
            # Title-page key/value lines are not prose sentences. Preserve only
            # exact lines that carry a requested, quoted typed assertion; never
            # split arbitrary PDF line wraps or retain contact/navigation fields.
            for raw_line in text.splitlines()[:128]:
                line = raw_line.strip()
                if re.match(
                    r"^(?:Bearbeiter|Verfasser|Autor(?:en)?|Datum|Stand)\b[ \t:]+", line, re.I
                ):
                    if line not in candidates and source_bound_aspects(
                        primary_question, line, block, expanded
                    ):
                        candidates.append(line)
                        has_bound_metadata = True
            if has_bound_metadata:
                # A whole title-page field block also carries phone/email boilerplate.
                # Its requested facts now have exact independent line candidates.
                candidates = [
                    sentence
                    for sentence in candidates
                    if not (
                        "\n" in sentence and re.search(r"@|\bKontakt\b|\bTelefon\b", sentence, re.I)
                    )
                ]
            for j, sentence in enumerate(candidates):
                # Similarity and unrelated digits cannot answer a quantity question.
                # Keep the requested unit literal: Ladepunkte are not Ladesäulen.
                count = re.search(r"\bwie\s+viele\s+([\wäöüÄÖÜß-]+)", primary_question, re.I)
                if count and not re.search(
                    r"(?<![\w.,])\d+(?:[.,]\d+)?\s+" + re.escape(count.group(1)) + r"\b",
                    sentence,
                    re.I,
                ):
                    continue
                facets = source_bound_aspects(primary_question, sentence, block, expanded)
                reason = answer_relevance_reason(
                    primary_question, sentence, expanded, facets, block["metadata"]
                )
                if reason:
                    relevance_rejections.append(
                        dict(
                            citation_label=block["citation_label"],
                            sentence_sha256=hashlib.sha256(sentence.encode()).hexdigest(),
                            reason=reason,
                        )
                    )
                    continue
                if (
                    structure.get("structural_only")
                    and not facets
                    and not structural_label_has_fact(sentence)
                ):
                    structure_rejections.append(
                        dict(
                            citation_label=block["citation_label"],
                            sentence_sha256=hashlib.sha256(sentence.encode()).hexdigest(),
                            reason="STRUCTURAL_LABEL_WITHOUT_FACTUAL_CLAUSE",
                            source_structure=structure,
                        )
                    )
                    continue
                if role_query and navigation_only_sentence(sentence):
                    navigation_rejections.append(
                        dict(
                            citation_label=block["citation_label"],
                            sentence_sha256=hashlib.sha256(sentence.encode()).hexdigest(),
                            reason="PURE_DOCUMENT_LINK_NOT_A_ROLE_ANSWER",
                        )
                    )
                    continue
                if re.search(
                    r"cookie|datenschutz|einwilligung|e-mail|mailto:|javascript|"
                    r"alle rechte|website",
                    sentence,
                    re.I,
                ):
                    continue
                if re.search(r"\[C\d+\]", sentence):
                    continue
                if re.search(
                    r"zielmarke|basisjahr|zieljahr|wie hoch|wie viel", primary_question, re.I
                ) and not re.search(r"\d", sentence):
                    continue
                if re.search(
                    r"abgeschlossen|fertig|umgesetzt|in betrieb", primary_question, re.I
                ) and all(
                    value in norm(text)
                    for value in ["nicht begonnen", "im prozess", "abgeschlossen"]
                ):
                    continue
                if (
                    re.search(r"von wann|bis wann|zeitraum|förderphase", primary_question, re.I)
                    and not re.search(r"\d{2}\.\d{2}\.\d{4}", sentence)
                    and not facets
                ):
                    continue
                stems = set(roots(sentence))
                overlap = len(expanded & stems)
                heading_terms = set(roots(block["metadata"].get("heading_context") or ""))
                role_requested = bool(
                    re.search(r"wer|finanz|förder|geldgeber", primary_question, re.I)
                )
                role_statement = bool(
                    re.search(
                        r"fördergeber|ministerium|gefördert|fördert|projektträger", sentence, re.I
                    )
                )
                if not overlap and role_requested and role_statement and expanded & heading_terms:
                    overlap = 1
                if facets:
                    aspects[(i, j)] = facets
                if not overlap and self.encoder is None and not facets:
                    continue
                if re.search(
                    r"ignore.*instruction|system prompt|vergiss.*anweisung|<script", sentence, re.I
                ):
                    continue
                if years and not (years & set(re.findall(r"\b(?:18|19|20)\d{2}\b", sentence))):
                    # Source heading establishes document context but not statement validity.
                    heading = (
                        (block["metadata"].get("heading_context", "") or "")
                        + " "
                        + block["metadata"].get("source_url", "")
                    )
                    if not (years & set(re.findall(r"\b(?:18|19|20)\d{2}\b", heading))):
                        continue
                score = overlap + min(len(sentence.split()), 50) / 100
                if re.search(
                    r"finanz|fördergeld|fördergeber|wer.*fördert|bezahlt", primary_question, re.I
                ):
                    score += 4 * bool(
                        re.search(r"fördergeber|ministerium|gefördert|fördert", sentence, re.I)
                    )
                if re.search(
                    r"wer.*(?:macht|erstell|planung|beauftragt|erarbeitet)", primary_question, re.I
                ):
                    score += 5 * bool(
                        re.search(r"beauftragt|erstellt|erarbeitet|planungsbüro", sentence, re.I)
                    )
                if re.search(
                    r"von wann|bis wann|zeitraum|laufzeit|förderphase", primary_question, re.I
                ):
                    score += 6 * bool(re.search(r"\d{2}\.\d{2}\.\d{4}", sentence))
                if re.search(r"wie (hoch|viel)|verbrauch|ergebnis", question, re.I):
                    score += 3 * bool(re.search(r"\d\s*(?:GWh|kWh|%|Euro|Tonnen)", sentence, re.I))
                ranked.append((score, i, j, sentence, block))
        if self.encoder is not None and ranked:
            import numpy as np

            # A bounded semantic sentence rerank; scores remain heuristic similarity.
            candidates = sorted(
                ranked, key=lambda r: ((r[1], r[2]) not in aspects, -r[0], r[1], r[2])
            )[:64]
            semantic_question = question
            for name in names:
                if len(name) >= 3:
                    semantic_question = re.sub(
                        r"(?<!\w)" + re.escape(name) + r"(?:s|er)?(?!\w)",
                        "",
                        semantic_question,
                        flags=re.I,
                    )
            vectors = self.encoder().encode([semantic_question, *[r[3] for r in candidates]])
            similarities = vectors[1:] @ vectors[0]
            minimum = max(0.40, float(max(similarities)) - 0.12)
            ranked = [
                (float(score) * 10 + min(row[0], 6) / 10, *row[1:])
                for row, score in zip(candidates, similarities)
                if np.isfinite(score) and (float(score) >= minimum or (row[1], row[2]) in aspects)
            ]
        ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
        used, selected, counts = set(), [], {}
        # Cover each retrieved municipality before spending the remaining sentence budget.
        requested = [
            a
            for a, row in self.municipalities.items()
            if re.search(
                r"(?<!\w)" + re.escape(norm(row.get("name", ""))) + r"(?!\w)", norm(question)
            )
        ]
        for scope in requested:
            candidate = next(
                (
                    r
                    for r in ranked
                    if scope in r[4]["metadata"]["municipality_ids"] and norm(r[3]) not in used
                ),
                None,
            )
            if candidate:
                selected.append(candidate)
                used.add(norm(candidate[3]))
                counts[candidate[1]] = counts.get(candidate[1], 0) + 1
        # Preserve one original source sentence for each explicitly requested,
        # typed source-bound aspect before filling with similarity-ranked context.
        covered = {facet for row in selected for facet in aspects.get((row[1], row[2]), [])}
        for row in ranked:
            facets = set(aspects.get((row[1], row[2]), []))
            if len(selected) >= 4:
                break
            if not facets - covered or norm(row[3]) in used or counts.get(row[1], 0) >= 2:
                continue
            selected.append(row)
            used.add(norm(row[3]))
            counts[row[1]] = counts.get(row[1], 0) + 1
            covered.update(facets)
        for row in ranked:
            if len(selected) >= 4:
                break
            if norm(row[3]) in used or counts.get(row[1], 0) >= 2:
                continue
            selected.append(row)
            used.add(norm(row[3]))
            counts[row[1]] = counts.get(row[1], 0) + 1
        selected = selected[:4]
        if role_query:
            selected.sort(
                key=lambda row: (
                    not bool(
                        set(aspects.get((row[1], row[2]), [])) & {"planner", "funder", "operator"}
                    )
                )
            )
        if not selected:
            response = ProviderResponse(
                "INSUFFICIENT_EVIDENCE: Die gefundenen Passagen tragen keinen hinreichenden "
                "belegbaren Antwortaspekt. Ein Nichtfund beweist keine fehlende Maßnahme.",
                generation_input.model,
                finish_reason="abstained",
            )
            self._remember(
                response.text,
                question,
                dict(
                    method=MODEL,
                    structure_rejections=structure_rejections,
                    navigation_rejections=navigation_rejections,
                    topic_rejections=topic_rejections,
                    relevance_rejections=relevance_rejections,
                ),
                aliases=(CANONICAL_INSUFFICIENT_EVIDENCE_TEXT,),
            )
            return response
        rendered = []
        for _, _, _, sentence, block in selected:
            label = block["citation_label"]
            if label not in generation_input.allowed_citation_labels:
                raise ValueError("Unbound citation in source synthesis")
            # Put the citation inside each sentence boundary for canonical validation.
            text = re.sub(r"\[C\d+\]", "", sentence).strip().rstrip(".!?")
            text = re.sub(r"([.!?])(?=\s+)", lambda m: " [" + label + "]" + m[1], text)
            rendered.append(text + " [" + label + "].")
        answer = "\n\n".join(rendered)
        diagnostic = dict(
            method=MODEL,
            controlled_query_expansion=list(controlled_expansion),
            navigation_rejections=navigation_rejections,
            structure_rejections=structure_rejections,
            topic_rejections=topic_rejections,
            relevance_rejections=relevance_rejections,
            semantic_minimum=minimum,
            source_bound_aspects_preserved=[
                dict(
                    citation_label=row[4]["citation_label"],
                    sentence_sha256=hashlib.sha256(row[3].encode()).hexdigest(),
                    aspects=aspects[(row[1], row[2])],
                    basis="ORIGINAL_SPAN_AND_PROVISIONAL_TYPED_ASSERTION_NOT_HUMAN_GOLD",
                )
                for row in selected
                if (row[1], row[2]) in aspects
            ],
            sentence_budget=4,
            per_evidence_unit_budget=2,
            semantic_candidate_budget=64,
            metadata_line_scan_budget=128,
        )
        self._remember(answer, question, diagnostic)
        return ProviderResponse(answer, generation_input.model, finish_reason="extractive_complete")


def claim_id_for(text, evidence_ids):
    value = json.dumps(
        {"text": text, "evidence_ids": sorted(evidence_ids)}, ensure_ascii=False, sort_keys=True
    )
    return "claim-sha256:" + hashlib.sha256(value.encode()).hexdigest()


def assess_answer(payload, plan):
    """Diagnostic hypotheses are separate from independently validated claim labels."""
    a = payload.get("answer_result") or {}
    evidence = {e["citation"]["label"]: e for e in payload.get("evidence", []) if e.get("citation")}
    claims = []
    for index, block in enumerate(a.get("answer_text", "").split("\n\n")):
        labels = list(dict.fromkeys(re.findall(r"\[(C\d+)\]", block)))
        if not labels:
            continue
        claim = re.sub(r"\s*\[C\d+\]", "", block).strip().rstrip(".")
        claims.append(
            dict(
                id=claim_id_for(claim, [evidence[label]["evidence_id"] for label in labels]),
                text=claim,
                citation_labels=labels,
                status="AUTOMATIC_EXTRACTIVE_NOT_HUMAN_VALIDATED",
                type=evidence_kind(claim),
                exact_span_supported=all(
                    claim in evidence[label]["evidence_text"] for label in labels
                ),
                source_ids=[evidence[label]["source_id"] for label in labels],
                municipality_scope="SOURCE_FRAME_NOT_CONFIRMED_CLAIM_SCOPE",
                temporal_validity="NOT_INDEPENDENTLY_VALIDATED",
            )
        )
    warnings = list(plan.warnings)
    if ((payload.get("retrieval_result") or {}).get("diagnostics") or {}).get("role_scope_only"):
        warnings.append(
            "Die Rollenrecherche ersetzt keine gesonderte Prüfung des angefragten "
            "Bau- oder Umsetzungsstands. Aus Planungs- oder Auftragsbelegen darf "
            "keine Fertigstellung einer Anlage abgeleitet werden."
        )
    return dict(
        method=MODEL,
        claims=claims,
        status="PARTIAL_SOURCE_SYNTHESIS" if claims else "INSUFFICIENT_EVIDENCE",
        limitations=[
            "Extraktive Quellensynthese, keine bestätigte Fachantwort.",
            "Ziele, Berichte und gemessene Werte sind kein kausaler Wirkungsbeweis.",
            "Widersprüche erfordern Vergleich desselben Gegenstands und Zeitrahmens.",
        ],
        operations=list(plan.operations),
        warnings=warnings,
        contradiction_detection="CANDIDATE_SCREENING_NOT_EXHAUSTIVE",
    )
