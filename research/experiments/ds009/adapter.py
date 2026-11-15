"""Offline claim validation: finite relation grammars, no document-scope inheritance."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from crawler.core.attribution import EvidenceText, attribute

ROOT = Path(__file__).resolve().parents[3]
MUNICIPALITIES = {"09190118": "Burggen", "09564000": "Nürnberg"}
NAMES = ("Burggen", "Bernbeuren", "Nürnberg")


def sha(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Passage:
    text: str
    locator: dict
    acquisition_ags: str
    data_class: str


def load_passage(arm: str, document_id: str, order_index: int) -> Passage:
    """Explicit read-only database; verify raw-response candidates before use."""
    folder = ROOT / "generated/ds007" / arm
    run = json.loads((folder / "run.json").read_text())
    with sqlite3.connect(f"file:{folder / 'pilot.sqlite'}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT s.*, d.url_final FROM segments s JOIN documents_raw d USING(document_id) "
            "WHERE s.document_id=? AND s.order_index=?", (document_id, order_index)
        ).fetchone()
        if row is None:
            raise ValueError("Required frozen evidence segment unavailable")
    candidates = []
    for e in json.loads((folder / "transport.json").read_text())["requests"]:
        if e["url"] == row["url_final"] and e["status"] == 200 and e["complete"] and not e["error"]:
            if sha((folder / e["body_path"]).read_bytes()) != e["sha256"]:
                raise ValueError("Original response checksum mismatch")
            candidates.append(dict(sha256=e["sha256"], number=e["number"],
                body_path=f"generated/ds007/{arm}/{e['body_path']}", fetched_at=e["timestamp_utc"]))
    if not candidates:
        raise ValueError("No intact original response for evidence")
    return Passage(row["text"], dict(
        source_type="segment", source_id=str(row["segment_id"]), segment_id=str(row["segment_id"]),
        document_id=document_id, url=row["url_final"], order_index=order_index,
        page_ref=row["page_ref"], database=f"generated/ds007/{arm}/pilot.sqlite",
        database_sha256=sha((folder / "pilot.sqlite").read_bytes()),
        text_sha256=sha(row["text"].encode()), raw_response_candidates=candidates,
        span_start=0, span_end=len(row["text"])),
        run["ags"], "real_frozen_ds007")


def extract(passage: Passage, relation: str):
    """Return relation-bound facts only; no keyword-based territorial inference."""
    text = passage.text.strip()
    city = "(?:" + "|".join(NAMES) + ")"
    if relation == "municipal_body":
        m = re.fullmatch(
            rf"(?P<actor>Finanzausschuss|Energie- und Umweltausschuss|"
            rf"Umwelt-, Energie- und Gewässerausschuss) - (?P<city>{city})", text)
        if m:
            return dict(value={"actor": m["actor"]}, scopes=[m["city"]],
                        actor={"name": m["actor"], "role": "named_body_not_sector_mandate"},
                        time=None, quote=text)
    if relation == "climate_neutrality_target":
        m = re.match(
            rf"(?P<decision>\d{{4}}) hat der Stadtrat das Integrierte Klimaschutzkonzept "
            rf"\(IKSK\) beschlossen: 65 Prozent weniger Treibhausgase bis 2030, "
            rf"Klimaneutralität bis (?P<year>\d{{4}})\. Bis dahin darf (?P<city>{city}) insgesamt ",
            text,
        )
        if m:
            return dict(value={"year": int(m["year"]), "decision_year": int(m["decision"])},
                        scopes=[m["city"]], actor={"name": f"Stadtrat {m['city']}", "role": "reported_decision_body"},
                        time={"decision_year": m["decision"], "target_year": m["year"],
                              "valid_until": None, "publication_date": None},
                        quote=f"Klimaneutralität bis {m['year']}")
    if relation == "heat_operator":
        # Explicit structured counterexamples, not inferred from an occupation.
        m = re.fullmatch(
            rf"Wärmebetreiber: (?P<actor>[^\n]+)\.\nGeltungsbereich: "
            rf"(?P<cities>{city}(?: und {city})*)\.\nStand: (?P<year>\d{{4}})\.", text)
        if m:
            return dict(value={"actor": m["actor"], "as_of": m["year"]},
                        scopes=m["cities"].split(" und "),
                        actor={"name": m["actor"], "role": "explicit_heat_operator"},
                        time={"as_of_year": m["year"], "valid_until": None},
                        quote=text)
    return None


def evaluate_claim(claim_id, target_ags, relation, value, passages, *,
                   document_status, required_as_of=None):
    """Evaluate and render together; callers cannot pass a pre-approved status."""
    if target_ags not in MUNICIPALITIES:
        raise ValueError("Explicit known target AGS required")
    target = dict(ags=target_ags, name=MUNICIPALITIES[target_ags])
    facts, normalized, locators = [], [], []
    for p in passages:
        if not p.locator.get("source_id") or sha(p.text.encode()) != p.locator.get("text_sha256"):
            raise ValueError("Missing locator or changed evidence text")
        fact = extract(p, relation)
        locators.append(dict(p.locator, data_class=p.data_class,
                             acquisition_ags=p.acquisition_ags,
                             quote=fact["quote"] if fact else p.text[:120]))
        if fact:
            facts.append(fact)
            # Deterministic relation projection for the existing DS008 guard.
            # This is explicitly NOT a verbatim quotation from the source.
            normalized.append(EvidenceText(
                "Geltungsbereich: " + " und ".join(fact["scopes"]) + ".",
                "body", dict(p.locator, transformation="ds009_relation_to_scope_v1",
                             original_quote=fact["quote"])))
    guard = attribute(target, normalized, municipality_names=NAMES, unit="claim")
    status = guard["attribution_status"]
    reason = guard["attribution_reason"]
    values = {json.dumps(f["value"], sort_keys=True) for f in facts}
    if not facts or len(facts) != len(passages):
        status, reason = "unknown", "unsupported_relation_or_unresolved_evidence"
    elif len(values) != 1:
        status, reason = "unknown", "conflicting_relation_values"
    elif status != "foreign_scope":
        if next(iter(values)) != json.dumps(value, sort_keys=True):
            status, reason = "unknown", "claim_value_not_supported_by_extracted_relation"
        elif required_as_of is not None:
            status, reason = "unknown", "no_proof_of_validity_at_requested_cutoff"
    verdict = {"verified": "supported", "foreign_scope": "rejected_foreign_scope",
               "unknown": "not_supported"}[status]
    # Output is rendered ONLY from matched structured facts, never arbitrary prose.
    assertion = None
    if status == "verified":
        if relation == "municipal_body":
            assertion = f"Die archivierte Quelle bezeichnet {value['actor']} als Gremium von {target['name']}."
        elif relation == "climate_neutrality_target":
            assertion = (f"Die archivierte Quelle berichtet für {target['name']} einen Stadtratsbeschluss "
                         f"{value['decision_year']} mit dem Ziel Klimaneutralität bis {value['year']}.")
        elif relation == "heat_operator":
            assertion = f"Die Quelle nennt {value['actor']} als Wärmebetreiber für {target['name']} (Stand {value['as_of']})."
    return dict(claim_id=claim_id,
        candidate_statement=f"{target['name']}: {relation} = {json.dumps(value, ensure_ascii=False, sort_keys=True)}",
        claim={"relation": relation, "value": value,
        "target_municipality": target, "required_as_of": required_as_of},
        document_attribution_status=document_status, attribution_status=status,
        attribution_reason=reason, evidence_verdict=verdict,
        acquisition_municipalities=sorted({p.acquisition_ags for p in passages}),
        evidence_locator=locators, publisher=[],
        responsible_actor=[f["actor"] for f in facts],
        territorial_scope=guard["territorial_scope"],
        temporal_scope=[f["time"] for f in facts if f["time"] is not None],
        definitive_assertion=assertion, retained_for_review=status != "verified",
        guard_trace=guard, guard_trace_quote_type="normalized_projection_not_original_text",
        data_class=sorted({p.data_class for p in passages}),
        evaluation="posthoc_selected_cases_not_gold")
