"""Source-unit, scope and date isolation for structural candidates; no human labels."""

from scripts.research.graph_chain_audit import chains_for


def test_graph_chain_audit_rejects_cross_scope_year_and_same_source_unit():
    entities = {
        "bridge": dict(label="Synthetic shared project", entity_type="Project"),
        "a": dict(label="Synthetic actor", entity_type="Organization"),
        "b": dict(label="Synthetic expected outcome", entity_type="ProjectedOutcome"),
    }

    def relation(rid, endpoint, scope, year):
        return dict(
            relation_id=rid,
            subject_id=endpoint,
            object_id="bridge",
            predicate="SYNTHETIC",
            evidence_id=rid,
            record=dict(claim_scope_ags=scope, reference_time=year, quote="Synthetic quote"),
        )

    relations = [
        relation("r1", "a", ["09375122"], "2021"),
        relation("r2", "b", ["09375122"], "2021"),
    ]
    evidence = {"r1": dict(unit_id="u1"), "r2": dict(unit_id="u2")}
    chains, _ = chains_for(entities, relations, evidence)
    assert len(chains) == 1 and chains[0]["source_unit_ids"] == ["u1", "u2"]
    assert chains[0]["temporal_alignment"] == "SHARED_YEAR_MENTION_NOT_VALIDITY"
    relations[1]["record"]["claim_scope_ags"] = ["09375162"]
    assert chains_for(entities, relations, evidence)[0] == []
    relations[1]["record"].update(claim_scope_ags=["09375122"], reference_time="2022")
    assert chains_for(entities, relations, evidence)[0] == []
    relations[1]["record"]["reference_time"] = None
    assert chains_for(entities, relations, evidence)[0][0]["temporal_alignment"] == "UNKNOWN"
    evidence["r2"]["unit_id"] = "u1"
    assert chains_for(entities, relations, evidence)[0] == []
