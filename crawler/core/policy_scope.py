"""Fail-closed municipality targeting for question answering."""
import re

def validate_policy_answer_scope(
    answer_enabled: bool, explicit_ids: tuple[str, ...], parsed_id: str | None
) -> tuple[str, ...]:
    """Never answer about a city using ambiguous or conflicting municipal filters."""
    if len(set(explicit_ids)) != len(explicit_ids):
        raise ValueError("duplicate --municipality-id supplied")
    if any(not re.fullmatch(r"[0-9]{8}", ags) for ags in explicit_ids):
        raise ValueError("municipality scope requires 8-digit AGS identifiers")
    if parsed_id and explicit_ids and parsed_id not in explicit_ids:
        raise ValueError("Question municipality contradicts --municipality-id")
    selected = explicit_ids or ((parsed_id,) if parsed_id else ())
    if answer_enabled and len(selected) != 1:
        raise ValueError(
            "Municipality-specific policy answers require exactly one AGS. "
            "Pass --municipality-id 0XXXXXXX or ask an unambiguous municipality question."
        )
    return selected
