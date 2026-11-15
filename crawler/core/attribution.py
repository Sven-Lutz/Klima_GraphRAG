"""Conservative, deterministic attribution of an explicitly bounded text unit.

Acquisition metadata never supplies territorial evidence. These deliberately
narrow grammars identify documented relations, not arbitrary municipality mentions.
Unknown templates abstain. A document's primary scope is NOT inherited by claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EvidenceText:
    text: str
    kind: Literal["heading", "body", "pdf_page"]
    locator: dict
    primary: bool = False


def attribute(
    acquisition_municipality: dict[str, str],
    texts: list[EvidenceText],
    *,
    municipality_names: tuple[str, ...],
    unit: Literal["document_primary_scope", "claim"] = "document_primary_scope",
) -> dict:
    """Return only explicit attribution, with inspectable source locators.

    ``municipality_names`` is a vocabulary, never a source of territorial facts.
    Multiple different scope assertions are contradictory, unless a single
    assertion explicitly includes the municipalities jointly. No majority vote.
    ``claim`` disables document-title rules to prevent scope inheritance.
    The caller supplies source-checked text and locators; unsupported input fails.
    """
    if unit not in ("document_primary_scope", "claim"):
        raise ValueError("Unsupported attribution unit")
    if not acquisition_municipality.get("ags") or not acquisition_municipality.get("name"):
        raise ValueError("Explicit acquisition AGS and name required")
    names = tuple(sorted(set(municipality_names), key=lambda x: (-len(x), x)))
    if not names or acquisition_municipality["name"] not in names:
        raise ValueError("Target municipality must be in the explicit vocabulary")
    city = "(?:" + "|".join(re.escape(n) for n in names) + ")"
    committee = re.compile(
        rf"(?P<actor>Gemeinderat|Finanzausschuss|Energie- und Umweltausschuss|"
        rf"Umwelt-, Energie- und Gewässerausschuss) - (?P<city>{city})"
    )
    scope_line = re.compile(rf"Geltungsbereich: (?P<cities>{city}(?: und {city})*)\.")
    plan_title = re.compile(rf"(?P<city>{city})s Klimaplan")
    pdf_title = re.compile(
        rf"INTEGRIERTES\s+KLIMASCHUTZKONZEPT\s+STADT\s+(?P<city>{city})\s+(?:Kurzbericht|Gesamtbericht)",
        re.IGNORECASE,
    )
    scopes: list[frozenset[str]] = []
    excluded = set()
    sources, actors, publishers, citations = set(), set(), set(), []

    def cite(item, quote, relation):
        citations.append(dict(item.locator, quote=quote, relation=relation))

    def add_scope(item, quote, municipalities):
        scopes.append(frozenset(municipalities))
        cite(item, quote, "explicit_scope")

    for item in texts:
        if not item.locator or not item.locator.get("source_id"):
            raise ValueError("Every input text needs a nonempty source_id locator")
        if item.kind not in ("heading", "body", "pdf_page"):
            raise ValueError("Unsupported evidence kind")
        text = item.text.strip()
        if unit == "document_primary_scope":
            match = committee.fullmatch(text) if item.kind == "heading" and item.primary else None
            if match:
                name, actor = match["city"], match["actor"]
                sources.add(name)
                actors.add((actor, "named_municipal_body"))
                add_scope(item, text, [name])
                cite(item, text, "municipal_body_identity_not_sector_competence")
            match = plan_title.fullmatch(text) if item.kind == "heading" and item.primary else None
            if match:
                sources.add(match["city"])
                add_scope(item, text, [match["city"]])
            match = pdf_title.match(text) if item.kind == "pdf_page" and item.primary else None
            if match:
                name = next(n for n in names if n.casefold() == match["city"].casefold())
                sources.add(name)
                add_scope(item, match[0], [name])
        # Only entire, affirmative, explicit relation lines are recognized.
        # Negated, quoted, conditional or incidental mentions do not match.
        for line in text.splitlines():
            line = line.strip()
            match = scope_line.fullmatch(line)
            if match:
                add_scope(item, line, match["cities"].split(" und "))
            match = re.fullmatch(rf"Gilt nicht für (?P<cities>{city}(?: und {city})*)\.", line)
            if match:
                excluded.update(match["cities"].split(" und "))
                cite(item, line, "explicit_scope_exclusion")
            match = re.fullmatch(r"Herausgeber: ([^\n]+)\.", line)
            if match:
                publishers.add(match[1])
                cite(item, line, "publisher_only")
                for name in names:
                    if match[1] in (f"Gemeinde {name}", f"Stadt {name}"):
                        sources.add(name)
            match = re.fullmatch(r"Zuständige Institution: ([^\n]+)\.", line)
            if match:
                actors.add((match[1], "explicitly_responsible_for_evaluated_unit"))
                cite(item, line, "responsible_actor_only")
    unique_scopes = set(scopes)
    if len(unique_scopes) > 1 or any(s & excluded for s in unique_scopes):
        status = "unknown"
        reason = "contradictory_explicit_scopes; no union or majority inference"
        territory = []
    elif unique_scopes:
        territory = sorted(next(iter(unique_scopes)))
        if acquisition_municipality["name"] in territory:
            status = "verified"
            reason = "explicit_target_scope_for_evaluated_unit"
        else:
            status = "foreign_scope"
            reason = "explicit_foreign_scope_for_evaluated_unit"
    else:
        territory = []
        status = "unknown"
        reason = (
            "no_supported_explicit_scope; acquisition/publisher/actor/host cannot fill this gap"
        )
    return dict(
        acquisition_municipality=dict(acquisition_municipality),
        source_municipality=sorted(sources),
        responsible_actor=[dict(name=name, role=role) for name, role in sorted(actors)],
        territorial_scope=territory,
        attribution_status=status,
        evidence_locator=citations,
        attribution_reason=reason,
        publisher=sorted(publishers),
        attribution_unit=unit,
        conflicting_scope_assertions=[
            sorted(s) for s in sorted(unique_scopes, key=lambda s: sorted(s))
        ]
        if status == "unknown" and scopes
        else [],
        explicitly_excluded_municipalities=sorted(excluded),
    )
