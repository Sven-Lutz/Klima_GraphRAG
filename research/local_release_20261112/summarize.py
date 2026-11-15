"""Rebuild the bounded agent top-1 audit; never writes scientific qrels."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
AUDIT = ROOT / "generated/research-release-20261112/RETRIEVAL_AUDIT.json"
PRIOR = ROOT / "generated/local-product-quality-audit-release/RETRIEVAL_AUDIT.json"
# Explicit manual top-1 judgments after passage inspection. 0 unrelated;
# 1 topic/navigation/context only; 2 supplies a requested aspect (not complete truth).
GRADES = {
    "lexical": {8: 1, 17: 1, 18: 2, 19: 2, 20: 1, 21: 2, 25: 1, 26: 2, 27: 2},
    "lexical_expanded": {
        7: 2,
        8: 1,
        9: 1,
        10: 1,
        11: 2,
        12: 1,
        13: 1,
        17: 1,
        18: 2,
        19: 2,
        20: 1,
        21: 2,
        22: 2,
        23: 2,
        25: 1,
        26: 2,
        27: 2,
    },
    "graph": {7: 1, 10: 2, 18: 2, 19: 2, 20: 2, 26: 0, 27: 0},
}
REASONS = {
    7: "Expansion: Fernwaermeanschluesse; Graph: LED-Strassenraum, nur verwandtes Energiethema.",
    8: "Radverkehrsueberschrift ohne konkrete Massnahme; Raenge 5-8 Adresse/Kontakt/Navigation.",
    9: (
        "Energiespar-Link ohne konkrete kommunale Einsparmassnahme; "
        "Folgepassage regionale Beratung."
    ),
    10: "Expansion: Hitzeschutz-Titel; Graph: staedtisches Begruenungsfoerderprogramm.",
    11: "Ziel2030,54Massnahmen und Solarkataster2017; kein aktueller Umsetzungsnachweis.",
    12: "Mobilitaetsziele und OePNV-Anteil; kein einzelnes realisiertes Projekt belegt.",
    13: "Klimaschutz-Ueberschrift ohne das erfragte Klimaziel.",
    17: "Solarenergie-Link ohne konkrete Massnahme; nur Rechercheeinstieg.",
    18: (
        "NKI/Bundesregierung im Foerderkontext; Graph nennt BMWK direkt. VG/Gemeinde unterscheiden."
    ),
    19: "IKSK-Tableau nennt LED-Massnahme; Farbstatus aus Text unbestimmt.",
    20: (
        "BM25 Rang1: aelterer 4GWh-Abschnitt; Graph Rang1:2022-Kontext "
        "mit Vorjahr2021. Jahresverwechslung vermeiden."
    ),
    21: "Entwurfsauslage dokumentiert Planungsstand, keine fertige Waermeversorgung.",
    22: "PV-Erzeugung und geplanter Speicher, berichtet statt unabhaengig verifiziert.",
    23: "LED-Flutlichtprojekt mit Zeitraum2023-2024, kein aktueller Status.",
    25: (
        "Dank/Prozess der VG Bad Neustadt; individuelle Aussagegeltung "
        "unbestaetigt. Raenge2-4 sachfremde Ortsnavigation."
    ),
    26: (
        "Lexikalisch: ausdrueckliche Hitzeaktionsplan-Nennung Aschheim. "
        "Graph: Hochwasserhilfe/THG statt Hitzeaktionsplan."
    ),
    27: (
        "Lexikalisch: ausdrueckliche Entsiegelungsnennung. Graph Rang1 "
        "Hochwasserhilfe, Rang3 THG sachfremd; Rang2 Entsiegelung relevant."
    ),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    data, prior = json.loads(AUDIT.read_text()), json.loads(PRIOR.read_text())
    content = json.loads((ROOT / "docs/development/RESEARCH_QUALITY_QUERIES.json").read_text())
    assert len(data["runs"]) == len(prior["runs"]) == 84
    rows = []
    for i, run in enumerate(data["runs"]):
        qid = i // 3
        assert run["question"] == content["questions"][qid]["question"]
        assert run["ranked_evidence_sha256"] == prior["runs"][i]["ranked_evidence_sha256"]
        assert run["replay_identical_ranked_evidence"]
        ev = run["evidence"]
        grade = GRADES[run["method"]].get(qid)
        assert bool(ev) == (grade is not None)
        wrong = [
            j + 1
            for j, e in enumerate(ev)
            if run["ags"] and run["ags"] not in e["document"]["municipality_ids"]
        ]
        rows.append(
            dict(
                question_id=qid + 1,
                question=run["question"],
                ags=run["ags"],
                method=run["method"],
                count=len(ev),
                top1_grade=grade,
                top1_source_id=ev[0]["source_id"] if ev else None,
                top1_text_sha256=hashlib.sha256(ev[0]["text"].encode()).hexdigest() if ev else None,
                ranking_sha256=run["ranked_evidence_sha256"],
                wrong_source_municipality_ranks=wrong,
                rationale=REASONS.get(qid, content["questions"][qid]["agent_content_observation"]),
                latency_ms=run["latency_ms"],
            )
        )
    summary = {}
    for method in GRADES:
        arm = [r for r in rows if r["method"] == method]
        n = sum(r["count"] > 0 for r in arm)
        useful = sum(r["top1_grade"] == 2 for r in arm)
        summary[method] = dict(
            questions=28,
            nonempty=n,
            passages=sum(r["count"] for r in arm),
            useful_top1=useful,
            useful_top1_per_question=useful / 28,
            useful_top1_given_nonempty=useful / n,
            unrelated_top1=sum(r["top1_grade"] == 0 for r in arm),
            contextual_top1=sum(r["top1_grade"] == 1 for r in arm),
            max_latency_ms=max(r["latency_ms"] for r in arm),
        )
    result = dict(
        schema="local-release-agent-top1-v1",
        human_gold=False,
        method=(
            "Manual agent inspection, development questions; 0 unrelated,1 "
            "contextual,2 requested aspect; empty=null"
        ),
        scope="Top1 only; no pooled recall, nDCG, independent relevance or policy truth",
        snapshot=data["runs"][0]["snapshot"],
        audit_sha256=sha(AUDIT),
        prior_sha256=sha(PRIOR),
        replay_identical=84,
        prior_identical=84,
        source_municipality_mismatches=sum(len(r["wrong_source_municipality_ranks"]) for r in rows),
        claim_scope_correctness="NOT_INDEPENDENTLY_VALIDATED",
        summary=summary,
        rows=rows,
    )
    (OUT / "RELEVANCE.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
