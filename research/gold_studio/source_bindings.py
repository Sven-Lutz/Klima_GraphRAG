"""Separate cited claim sources from required review context without changing IDs."""


def claim_source_partition(payload, sources):
    available = {source.get("unit_id") or source.get("evidence_id") for source in sources}
    if not available or None in available:
        raise ValueError("Exact source-unit identities required for claim binding")
    if "cited_source_ids" not in payload and "context_source_ids" not in payload:
        # Historical objects used every source as a citation. Preserve that contract.
        return sorted(available), []
    cited, context = payload.get("cited_source_ids"), payload.get("context_source_ids")
    for ids in (cited, context):
        if (
            not isinstance(ids, list)
            or any(not isinstance(i, str) or not i for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("Explicit distinct cited/context source IDs required")
    if not cited or set(cited) & set(context) or set(cited) | set(context) != available:
        raise ValueError("Cited/context partition must cover exactly the anchored sources")
    return sorted(cited), sorted(context)


def required_claim_context_ids(cited_source_ids, items):
    cited = set(cited_source_ids)
    return sorted(
        {
            source_id
            for item in items
            if item.get("source_id") in cited
            for assertion in item.get("assertions", [])
            for source_id in assertion.get("required_source_ids", [])
        }
        - cited
    )
