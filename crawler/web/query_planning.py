"""Versioned exploratory planning; historical retrieval execution stays unchanged."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from crawler.web.scope_conditions import subject_conditions
from research.bavaria.workspace.core import norm, roots

VERSION = "research-plan-v4"
ALIASES = {
    "munich": "munchen",
    "muenchen": "munchen",
    "nuremberg": "nurnberg",
    "nuernberg": "nurnberg",
    "wuerzburg": "wurzburg",
    "augsburg city": "augsburg",
}
CONTEXT = set(
    roots(
        "welche welcher welchen was macht machen eigentlich beim gegen hinsichtlich "
        "besonders aktiv bayern bayrisch bayerisch quellen nennen nennt beschreibt "
        "dokumentieren dokumentiert kommunen kommunalen gemeinden gemeinde stadt "
        "bitte gibt gibt es ist sind wie etwas sieht aus dazu dort damit deren "
        "unterscheiden vergleich vergleiche zwischen seit bis mehr weniger"
    )
)


def content_terms(text, names=()):
    omit = CONTEXT | {t for name in names for t in roots(name)}
    return set(roots(text)) - omit


def product_query_expansion(question):
    """Observed terminology gap, isolated from frozen lexical expansion rules.

    A plan and the planning process are related retrieval cues, never proof that
    a plan has been completed or implemented.
    """
    if re.search(r"\bwarmeplan(?:s|es|e|en)?\b", norm(question)):
        return ("Wärmeplanung",)
    return ()


def requested_graph_role_predicates(question):
    """Explicit product role intent; never override temporal/status/shared-funder gates."""
    if re.search(
        r"\b(?:heute|aktuell|derzeit|gegenwärtig)\b|umgesetzt|fertiggestellt|"
        r"abgeschlossen|realisiert|in\s+betrieb",
        question,
        re.I,
    ):
        return ()
    funding = bool(re.search(r"förder|finanz|geldgeber|bezahlt", question, re.I))
    if funding and re.search(r"beide|sowohl|gemeinsam|mehrere", question, re.I):
        return ()
    if not re.search(
        r"\b(?:wer|wem)\b|welche\w*\s+(?:firma|institut|büro|organisation|akteur|"
        r"unternehmen|ministerium|fördergeber|geldgeber)",
        question,
        re.I,
    ):
        return ()
    predicates = set()
    if funding:
        predicates.update({"FUNDS", "FUNDED_BY", "SUPPORTED_BY"})
    if re.search(
        r"\bplant\b|planer|erstell|erarbeit|beauftragt|verfass|autor|büro", question, re.I
    ):
        predicates.update({"PLANS", "AUTHORS", "COMMISSIONED_FOR", "COMMISSIONS"})
    if re.search(r"betreib|versorger", question, re.I):
        predicates.add("OPERATES")
    return tuple(sorted(predicates))


def answer_constraint(question):
    if re.search(
        r"erfundene?[nmsr]?\s+(?:zitat|beleg)|erfinde.*(?:zitat|beleg)|ignoriere.*belege",
        question,
        re.I,
    ):
        return "Eine erfundene Quellenbehauptung wird nicht erzeugt."
    if re.search(
        r"(?:objektiv|tatsächlich).*?(?:sieger|rangier)|(?:aktivste|beste).*gemeinde|"
        r"gemeinden.*besonders aktiv",
        question,
        re.I,
    ):
        return (
            "Eine Wirkungs- oder Aktivitätsrangliste benötigt "
            "definierte vergleichbare Metriken und hinreichende Quellenabdeckung."
        )
    if re.search(
        r"\b(?:Hamburg|Berlin|Bremen|Sachsen|Brandenburg|Hessen|Saarland|Nordrhein-Westfalen|Baden-Württemberg|Niedersachsen|Schleswig-Holstein|Thüringen)\b",
        question,
        re.I,
    ):
        return (
            "Der recherchierbare Quellenrahmen ist auf Bayern "
            "beschränkt; für den angefragten externen Ort liegt keine gebundene Evidenz vor."
        )
    return None


@dataclass(frozen=True)
class QueryPlan:
    original: str
    rewritten: str
    municipality_ids: tuple[str, ...]
    resolution: tuple[dict, ...]
    parts: tuple[str, ...]
    years: tuple[int, ...]
    operations: tuple[str, ...]
    warnings: tuple[str, ...]
    clarification: tuple[dict, ...] = ()
    version: str = VERSION

    def to_dict(self):
        return {**asdict(self), "subject_conditions": subject_conditions(self.rewritten)}


def plan_query(workspace, question, municipality_ids=(), history=()):
    """Resolve exact/alias locations, expose fuzzy resolution, never guess ambiguous towns."""
    rewritten = question.strip()
    resolutions, warnings, clarification = [], [], []
    by_name = {}
    for ags, row in workspace.municipalities.items():
        for field in ("name", "official_name"):
            name = norm(row.get(field, ""))
            if name:
                by_name.setdefault(name, set()).add(ags)
    folded = norm(rewritten)
    for alias, name in ALIASES.items():
        if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", folded):
            for ags in sorted(by_name.get(name, ())):
                resolutions.append(dict(ags=ags, method="alias", input=alias, confidence=1.0))
                rewritten = re.sub(
                    re.escape(alias), workspace.municipalities[ags]["name"], rewritten, flags=re.I
                )
    targets = list(municipality_ids)
    if not targets:
        try:
            targets = workspace.resolve_ags(None, rewritten)
        except ValueError as exc:
            if "Ambiguous municipality" not in str(exc):
                raise
            for name, ids in by_name.items():
                if len(ids) > 1 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", folded):
                    clarification.extend(
                        dict(
                            ags=a,
                            name=workspace.municipalities[a]["name"],
                            district=workspace.municipalities[a].get("district"),
                        )
                        for a in sorted(ids)
                    )
        if clarification:
            ambiguous_names = {norm(c["name"]) for c in clarification}
            qualified = [
                c
                for c in clarification
                if any(
                    norm(workspace.municipalities[c["ags"]].get(field, "")) in folded
                    for field in ("region", "district")
                    if workspace.municipalities[c["ags"]].get(field)
                    and norm(workspace.municipalities[c["ags"]][field]) not in ambiguous_names
                )
            ]
            if len(qualified) == 1:
                targets = [qualified[0]["ags"]]
                resolutions.append(
                    dict(ags=targets[0], method="regional_qualification", confidence=1.0)
                )
                clarification = []
        if not targets and not clarification:
            for name, ids in by_name.items():
                if len(name) > 3:
                    continue
                if re.search(
                    r"\b(?:in|aus|von|für|stadt|gemeinde)\s+" + re.escape(name) + r"\b", folded
                ):
                    if len(ids) == 1:
                        ags = next(iter(ids))
                        targets.append(ags)
                        resolutions.append(
                            dict(
                                ags=ags,
                                method="short_toponym_geographic_cue",
                                input=name,
                                confidence=1.0,
                            )
                        )
                    else:
                        clarification.extend(
                            dict(ags=a, name=workspace.municipalities[a]["name"])
                            for a in sorted(ids)
                        )
        if not targets and not clarification:
            variants = {}
            for name, ids in by_name.items():
                if len(name) < 4:
                    continue
                forms = [name + "s", name + "er"]
                if name.endswith("en"):
                    forms.append(name[:-2] + "er")
                for form in forms:
                    if re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)", folded):
                        variants.setdefault(form, set()).update(ids)
            for form, ids in variants.items():
                if len(ids) == 1:
                    ags = next(iter(ids))
                    targets.append(ags)
                    rewritten = re.sub(
                        r"(?<!\w)" + re.escape(form) + r"(?!\w)",
                        workspace.municipalities[ags]["name"],
                        rewritten,
                        flags=re.I,
                    )
                    resolutions.append(
                        dict(ags=ags, method="toponym_inflection", input=form, confidence=1.0)
                    )
                else:
                    clarification.extend(
                        dict(ags=a, name=workspace.municipalities[a]["name"]) for a in sorted(ids)
                    )
        if not targets and not clarification:
            # Only a capitalized place after a geographic preposition can be fuzzy-resolved.
            for token in re.findall(r"\b(?:in|aus|von|für)\s+([A-ZÄÖÜ][\wäöüÄÖÜß-]{4,})", question):
                candidates = sorted(
                    ((SequenceMatcher(None, norm(token), name).ratio(), name) for name in by_name),
                    reverse=True,
                )
                if not candidates or candidates[0][0] < 0.84:
                    continue
                score, name = candidates[0]
                if len(by_name[name]) != 1 or (
                    len(candidates) > 1 and score - candidates[1][0] < 0.07
                ):
                    clarification.extend(
                        dict(ags=a, name=workspace.municipalities[a]["name"])
                        for a in sorted(by_name[name])
                    )
                else:
                    ags = next(iter(by_name[name]))
                    targets.append(ags)
                    resolutions.append(
                        dict(
                            ags=ags,
                            method="spelling_suggestion_applied",
                            input=token,
                            confidence=round(score, 3),
                        )
                    )
                    rewritten = rewritten.replace(token, workspace.municipalities[ags]["name"])
                    warnings.append(
                        "Ortskorrektur automatisch angewandt; Auflösung im Trace prüfen."
                    )
    if (
        not targets
        and not clarification
        and history
        and re.search(r"\b(dort|dazu|dabei|diese|dieser|deren|und|seitdem)\b", folded)
    ):
        last = history[-1]
        targets = [a for a in last.get("municipality_ids", []) if a in workspace.municipalities]
        previous = last.get("question", "")[:4000]
        rewritten = rewritten + " — Rückfrage-Kontext: " + previous
        warnings.append("Vorherige Frage und geografischer Rahmen aus diesem Gespräch übernommen.")
    if set(targets) - workspace.municipalities.keys():
        raise ValueError("Unknown municipality scope")
    years = tuple(sorted({int(y) for y in re.findall(r"\b(?:18|19|20)\d{2}\b", rewritten)}))
    ops = []
    operation_question = subject_conditions(rewritten)["positive_query"]
    for pattern, operation in [
        (r"vergleich|unterscheid|gegenüber|veränderung", "comparison"),
        (
            r"seit|entwick|verlauf|früher|damals|\bwann\b|berichtsdatum|laufzeit|zeitraum",
            "temporal",
        ),
        (
            r"umgesetzt|tatsächlich|realisiert|wirkung|ergebnis|fertiggestellt|"
            r"fertig gebaut|\bgebaut\w*\b|in betrieb",
            "implementation",
        ),
        (r"widerspr|konflikt", "contradictions"),
        (r"keine|nicht dokument|fehlen|ausreichend", "coverage"),
        (r"rolle|beziehung|akteur|versorger", "multi_hop"),
    ]:
        if re.search(pattern, operation_question, re.I):
            ops.append(operation)
    if len(targets) > 1 and "comparison" not in ops:
        # A joint project or shared institution may span municipalities without
        # asking for pairwise differences. Geography alone is no comparison intent.
        ops.append("multi_municipality")
    constraint = answer_constraint(question)
    if constraint:
        warnings.append(constraint)
        ops.append("unsupported_inference_or_scope")
    if years:
        warnings.append("Jahresnennung ist keine zeitliche Gültigkeit; Quellenzeit separat prüfen.")
    if "coverage" in ops:
        warnings.append("Nichtfund belegt keine Abwesenheit kommunaler Maßnahmen.")
    if "besonders aktiv" in folded:
        warnings.append(
            "Keine Aktivitätsrangliste ohne definierte Metrik und vollständige Quellenbasis."
        )
    parts = tuple(p.strip() for p in re.split(r"\?\s*|\s+sowie\s+|;", rewritten) if p.strip())[:6]
    return QueryPlan(
        question,
        rewritten,
        tuple(dict.fromkeys(targets)),
        tuple(resolutions),
        parts,
        years,
        tuple(ops),
        tuple(warnings),
        tuple(clarification),
    )
