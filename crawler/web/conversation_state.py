"""Bounded dialogue state; explicit events are technical decisions, not human labels."""

import re

VERSION = "conversation-state-v2.1"


def resolve(question, parent, municipality_ids, reference_year):
    previous = parent.get("context_scope", {}) if parent else {}
    ids = list(municipality_ids)
    year = reference_year
    events = []
    clarification = None
    # Location names need an authoritative registry; never guess an ID from model knowledge.
    place = re.search(r"\b(?:in|für)\s+([A-ZÄÖÜ][a-zäöüß]+)", question)
    if place and place.group(1) in {
        "Betrieb",
        "Planung",
        "Umsetzung",
        "Entwurf",
        "Vergleich",
        "Prognose",
        "Rathaus",
        "Verwaltungsbetrieb",
    }:
        place = None
    if place and not ids and parent and previous.get("municipality_ids"):
        clarification = "Bitte die Gemeinde-ID für den genannten Ort bestätigen."
        events.append({"kind": "UNRESOLVED_PLACE", "value": place.group(1)})
    if re.search(r"ander\w* (?:gemeinde|ort)|anderswo", question, re.I):
        clarification = "Welche Gemeinde meinen Sie? Bitte Gemeinde-ID angeben."
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", question))
    if reference_year and years and years != {str(reference_year)}:
        clarification = "Frage und eingestelltes Bezugsjahr widersprechen sich. Welches Jahr gilt?"
    if len(years) > 1:
        clarification = "Welcher Zeitraum und welche zeitlich vergleichbare Kennzahl sind gemeint?"
    if not year and len(years) == 1:
        year = int(next(iter(years)))
        events.append({"kind": "EXPLICIT_YEAR", "value": year})
    changed = bool(ids and ids != previous.get("municipality_ids", [])) or bool(place)
    if parent and not changed:
        ids = ids or list(previous.get("municipality_ids", []))
        year = year or previous.get("reference_year")
    if changed:
        events.append({"kind": "PLACE_CHANGED", "time_inheritance": "CLEARED"})
    deictic = bool(
        re.search(
            r"\bdort\b|diese\w* maßnahme|\bdavon\b|welche wurden|wie viel leistung", question, re.I
        )
    )
    effective = question
    topic = (
        parent.get("conversation_state", {}).get("topic_question", parent["question"])
        if parent
        else question
    )
    if year and year != previous.get("reference_year"):
        topic = re.sub(r"\b(?:19|20)\d{2}\b", "", topic)
    if deictic and parent and not changed:
        effective += " " + topic
        events.append({"kind": "TOPIC_INHERITED", "parent_turn_id": parent["turn_id"]})
    elif deictic and not parent:
        clarification = "Auf welche Frage, Gemeinde oder Maßnahme beziehen Sie sich?"
    if not parent and (
        re.match(r"^(und\b|dazu\b|dort\b)", question, re.I)
        or question.casefold().strip(" ?.! ") in {"leistung", "jahr", "wann", "umfang"}
    ):
        clarification = "Auf welche Quelle, Gemeinde und vorherige Frage beziehen Sie sich?"
    if parent and re.search(r"diese\w* maßnahme|diese\w* förder", question, re.I):
        texts = {c["text"] for c in parent.get("claims", [])}
        if len(texts) > 1:
            clarification = "Welche der zuvor genannten Maßnahmen meinen Sie?"
    if re.search(r"emission", question, re.I) and not re.search(
        r"territorial|verwaltungsbetrieb|verwaltungsemission", question, re.I
    ):
        clarification = "Meinen Sie territoriale Emissionen oder den Verwaltungsbetrieb?"
    if re.search(r"erfolgreich|gewirkt", question, re.I):
        clarification = "Welche Maßnahme und welches gemessene Erfolgskriterium meinen Sie?"
    if (
        parent
        and parent.get("answer_status") == "CLARIFICATION_REQUIRED"
        and re.search(r"emission", parent["question"], re.I)
        and re.match(r"^(?:territorial|verwaltungsbetrieb|verwaltungsemission)", question, re.I)
    ):
        effective = parent["question"] + " " + question
        topic = effective
        deictic = True
        clarification = None
        events.append(
            {
                "kind": "CLARIFICATION_RESOLVED",
                "dimension": "EMISSIONS_BOUNDARY",
                "decision": question,
                "parent_turn_id": parent["turn_id"],
            }
        )
    scope = {"municipality_ids": ids, "reference_year": year, "source_ids": []}
    state = {
        "version": VERSION,
        "research_question": question,
        "topic_question": topic if deictic and parent and not changed else question,
        "effective_question": effective,
        "scope": scope,
        "referenced_evidence_ids": previous.get("source_ids", [])
        if deictic and not changed
        else [],
        "relevant_turn_ids": [parent["turn_id"]] if parent and not changed else [],
        "events": events,
        "open_aspects": [clarification] if clarification else [],
        "topic_status": "INHERITED" if deictic and parent and not changed else "CURRENT_QUESTION",
        "intent": "RESEARCH",
        "unresolved_place": bool(place and not ids),
        "entities": [],
        "measures": [],
    }
    return state, clarification
