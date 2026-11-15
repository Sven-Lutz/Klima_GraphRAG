"""Validate the narrow statutory assessment, separately from copyright-holder consent."""

import hashlib
import json
from datetime import datetime, timezone


def content_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_minimal_policy(policy):
    if (
        policy.get("schema") != "klima-pilot-rights-v1"
        or policy.get("source_presentation") != "minimal_external_preview"
        or policy.get("release_status") != "SCOPED_STATUTORY_ASSESSMENT"
        or policy.get("technical_test_only", False)
        or policy.get("rights_holder_consent_claimed") is not False
        or policy.get("data_contract") != "minimal_external_preview-v1"
        or not policy.get("basis")
        or not policy.get("decision_by")
    ):
        raise ValueError("Explicit minimal statutory assessment required")
    owner = policy.get("owner_statement", {})
    if (
        owner.get("confirmed_by_owner") is not True
        or owner.get("operator_role") != "noncommercial_individual_researcher"
        or owner.get("institutional_operator") is not False
        or owner.get("purpose") != "joint_scientific_testing_and_quality_review"
        or not owner.get("confirmed_at")
    ):
        raise ValueError("Actual noncommercial individual-researcher declaration required")
    issued = datetime.fromisoformat(policy["issued_at"])
    expiry = datetime.fromisoformat(policy["expires_at"])
    if (
        issued.tzinfo is None
        or expiry.tzinfo is None
        or not 0 < (expiry - issued).total_seconds() <= 7 * 86400
        or issued > datetime.now(timezone.utc)
        or expiry <= datetime.now(timezone.utc)
    ):
        raise ValueError("Unexpired assessment of at most seven days required")
    spec = policy["minimal_facts"]
    if spec.get("deployment_approved") is not False or content_hash(spec) != policy.get(
        "facts_sha256"
    ):
        raise ValueError("Minimal factual registry changed")
    facts = spec["facts"]
    if not 1 <= len(facts) <= 8 or len({f["relation_id"] for f in facts}) != len(facts):
        raise ValueError("Bounded unique factual registry required")
    sources = policy["sources"]
    selected = {r["sha256"]: r for r in sources}
    if len(selected) != len(sources) or set(selected) != {f["source_sha256"] for f in facts}:
        raise ValueError("Minimal source selection differs from factual registry")
    for row in sources:
        if (
            row.get("internal_processing") != "STATUTORY_TDM_SCOPE"
            or row.get("source_metadata_display") != "SCOPED_FACTUAL_METADATA"
            or row.get("original_url_display") != "SCOPED_DIRECT_LINK"
            or row.get("fact_display") != "SCOPED_OWN_FACTS"
            or row.get("derived_graph_display") != "SCOPED_OWN_FACTS"
            or row.get("excerpt_display") != "DENIED"
            or row.get("archive_distribution") != "DENIED"
            or not row.get("basis")
        ):
            raise ValueError("Minimal field scope is incomplete or permits source copies")
    return selected
