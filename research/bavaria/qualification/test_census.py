"""Protect coverage denominators from cross-ledger duplicates and failure relabeling."""

import sqlite3
from collections import Counter

from .census import aggregate_text, failure_class


def test_dedup_preserves_source_and_context_distinctions():
    baseline, delta = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    for conn in (baseline, delta):
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE units(sha256,ordinal,unit_id,text,heading,page_ref,kind)")
    same = ("source-a", 0, "unit-a", "Wärme geplant 2025", "Plan", None, "paragraph")
    baseline.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?)", same)
    delta.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?)", same)
    delta.execute(
        "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
        ("source-a", 1, "unit-b", same[3], "Other context", None, "paragraph"),
    )
    delta.execute(
        "INSERT INTO units VALUES (?,?,?,?,?,?,?)",
        ("source-b", 0, "unit-c", same[3], "Plan", None, "paragraph"),
    )
    documents = {
        key: {
            "canonical_units": 0,
            "tag_units": Counter(),
            "units_with_yearlike_tokens": 0,
            "yearlike_occurrences": 0,
            "years": Counter(),
        }
        for key in ("source-a", "source-b")
    }
    result = aggregate_text({"baseline": baseline, "delta": delta}, documents)
    assert result["input_rows_total"] == 4
    assert result["unique_source_order_text_context_units"] == 3
    assert documents["source-a"]["canonical_units"] == 2
    assert documents["source-b"]["canonical_units"] == 1
    baseline.close()
    delta.close()


def test_access_boundaries_are_not_server_outages():
    assert failure_class("TECHNICALLY_UNREACHABLE", "BODY_LIMIT") == "BODY_SIZE_BOUND_DEFERRED"
    assert failure_class("TECHNICALLY_UNREACHABLE", "UNVERIFIED_REDIRECT_DOMAIN") == (
        "UNVERIFIED_REDIRECT_AUTHORITY_BOUNDARY"
    )
    assert failure_class("ROBOTS_UNAVAILABLE", "ROBOTS_UNAVAILABLE") == (
        "ROBOTS_UNAVAILABLE_NOT_DISALLOW"
    )
