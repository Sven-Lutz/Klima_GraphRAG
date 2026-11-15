"""Selected real claims and explicitly synthetic counterexamples. No HTTP."""
import csv
import json
from pathlib import Path
from collections import Counter

from .adapter import ROOT, Passage, evaluate_claim, load_passage, sha


def synthetic(text, identifier):
    return Passage(text, dict(source_id=f"synthetic:{identifier}",
        text_sha256=sha(text.encode()), span_start=0, span_end=len(text)),
        "09190118", "synthetic_counterexample")


def cases():
    with (ROOT / "research/experiments/ds008/attribution.csv").open() as stream:
        docs = [r for r in csv.DictReader(stream) if r["attribution_unit"] == "document_primary_scope"]
    rows = []

    def add(identifier, doc, indices, relation, value, cutoff=None):
        passages = [load_passage(doc["arm"], doc["document_id"], i) for i in indices]
        target = json.loads(doc["acquisition_municipality"])["ags"]
        rows.append(evaluate_claim(identifier, target, relation, value, passages,
                                  document_status=doc["attribution_status"], required_as_of=cutoff))

    foreign = sorted((d for d in docs if d["attribution_status"] == "foreign_scope"), key=lambda d: d["row_id"])
    for index, doc in enumerate(foreign, 1):
        p = load_passage(doc["arm"], doc["document_id"], 6)
        actor = p.text.rsplit(" - ", 1)[0]
        add(f"real-foreign-{index}", doc, [6], "municipal_body", {"actor": actor})
    burggen = next(d for d in docs if d["url"].endswith("/3_G_21/Umwelt-Energie-und_Gewaesserausschuss.html"))
    actor = "Umwelt-, Energie- und Gewässerausschuss"
    add("real-burggen-body", burggen, [6], "municipal_body", {"actor": actor})
    add("real-burggen-mandate", burggen, [6], "heat_operator", {"actor": actor, "as_of": "2026"})
    shared = next(d for d in docs if d["url"].endswith("/Gremien.html"))
    add("real-shared", shared, [7], "heat_operator", {"actor": "Verwaltungsgemeinschaft Bernbeuren", "as_of": "2026"})
    private = next(d for d in docs if d["url"].endswith("/gemeinde/gemeinderat"))
    add("real-private", private, [19, 21], "heat_operator", {"actor": "Thomas Kirchhofer", "as_of": "2026"})
    climate = next(d for d in docs if d["url"].endswith("/stadtportal/klimaplan.html"))
    add("real-climate", climate, [1], "climate_neutrality_target", {"year": 2040, "decision_year": 2025})
    add("real-climate-wrong-year", climate, [1], "climate_neutrality_target", {"year": 2045, "decision_year": 2025})
    add("real-climate-cutoff", climate, [1], "climate_neutrality_target",
        {"year": 2040, "decision_year": 2025}, "2026-10-19")
    shared_text = "Wärmebetreiber: Beispielverband.\nGeltungsbereich: Burggen und Bernbeuren.\nStand: 2025."
    samples = {
        "synthetic-shared": [shared_text],
        "synthetic-territory-conflict": [
            shared_text.replace("Burggen und Bernbeuren", "Burggen"),
            shared_text.replace("Burggen und Bernbeuren", "Bernbeuren")],
        "synthetic-actor-conflict": [shared_text, shared_text.replace("Beispielverband", "Anderer Verband")],
        "synthetic-publisher-only": ["Herausgeber: Gemeinde Burggen.\nBetreibergebiet ungeklärt."],
    }
    for identifier, texts in samples.items():
        rows.append(evaluate_claim(identifier, "09190118", "heat_operator",
            {"actor": "Beispielverband", "as_of": "2025"},
            [synthetic(t, f"{identifier}:{i}") for i, t in enumerate(texts)],
            document_status="verified"))  # deliberately cannot confer claim status
    return rows


def summary(rows):
    return {label: dict(Counter(r["evidence_verdict"] for r in rows if label in r["data_class"]))
            for label in ("real_frozen_ds007", "synthetic_counterexample")}


def main():
    rows = cases()
    output = Path(__file__).with_name("claims.csv")
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                             if isinstance(v, (dict, list)) or v is None else v for k, v in row.items()})
    print(json.dumps(summary(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
