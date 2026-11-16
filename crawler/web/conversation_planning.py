"""Versioned routing policy; capability choice is not a measured quality ranking."""

import re

from crawler.core.retrieval import RetrievalMode


def plan(question, requested, retriever):
    patterns = [
        ("NUMERIC", r"wie viel|betrag|emission|leistung|anzahl"),
        ("COMPARISON", r"vergleich|unterschied|gegenüber"),
        ("HISTORICAL_CHANGE", r"entwickl|verändert|seit|verlauf"),
        ("CORPUS_OVERVIEW", r"abdeckung|korpus|quellenbestand"),
        ("EVIDENCE_CONFLICT", r"widerspruch|widersprüch|konflikt"),
        ("RELATION", r"\bwer\b.*(?:plant|erstellt|betreibt)|beziehung|mehrere schritte"),
    ]
    intent = next(
        (name for name, pattern in patterns if re.search(pattern, question, re.I)), "FACT_LOOKUP"
    )
    supported = getattr(retriever, "supported_modes", ["semantic", "hybrid", "graph"])
    supported = [getattr(mode, "value", mode) for mode in supported]
    desired = "graph" if intent == "RELATION" else "semantic"
    limits = ["Intent classification is a technical heuristic, not evaluated planning accuracy."]
    if desired not in supported:
        desired = next(
            (m for m in ["lexical_expanded", "lexical", "hybrid"] if m in supported), desired
        )
        limits.append("Preferred method unavailable; selected available adapter.")
    chosen = requested or RetrievalMode(desired)
    return chosen, {
        "version": "intent-router-v2.1",
        "intent": intent,
        "method": chosen.value,
        "selection": "EXPLICIT" if requested else "CAPABILITY_BOUNDED_HEURISTIC",
        "available_methods": supported,
        "limits": limits,
        "graph_format_advantage": "NOT_ESTABLISHED",
        "structured_information": "SEE_CANONICAL_RETRIEVAL_TRACE",
        "representation": "EVIDENCE_PASSAGES_AND_OPTIONAL_ASSERTIONS",
        "formalization": "NATURAL_LANGUAGE_HEURISTIC",
        "generator": "SEPARATE_FACTOR",
    }
