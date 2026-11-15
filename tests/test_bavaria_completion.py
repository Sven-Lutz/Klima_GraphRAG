"""Integrity checks for final research integration; no model/network calls."""

import json
import sqlite3

import pytest

from research.bavaria.completion.metrics import kappa, ranking_metrics
from research.bavaria.federation import source_connection


def ledger(path, *, uid="u1", text="source", name="Gemeinde"):
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE metadata(key TEXT,value TEXT);
        INSERT INTO metadata VALUES('schema','bavaria-acquisition-v1');
        CREATE TABLE municipalities(ags TEXT,record TEXT);
        CREATE TABLE documents(sha256 TEXT);
        CREATE TABLE units(unit_id TEXT,sha256 TEXT,text TEXT);
        CREATE TABLE associations(ags TEXT,sha256 TEXT);
        """)
        c.execute("INSERT INTO municipalities VALUES(?,?)", ("09", json.dumps({"name": name})))
        c.execute("INSERT INTO documents VALUES('sha')")
        c.execute("INSERT INTO units VALUES(?, 'sha', ?)", (uid, text))
        c.execute("INSERT INTO associations VALUES('09','sha')")
    return path


def test_federation_deduplicates_and_is_read_only(tmp_path):
    a = ledger(tmp_path / "a.sqlite")
    b = ledger(tmp_path / "b.sqlite")
    with source_connection(a, [b]) as c:
        assert c.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM units").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO s0.documents VALUES('new')")
    with pytest.raises(ValueError, match="Duplicate"):
        source_connection(a, [a])


def test_federation_checks_extra_to_extra_identity(tmp_path):
    a = ledger(tmp_path / "a.sqlite", uid="base")
    b = ledger(tmp_path / "b.sqlite", uid="extra")
    c = ledger(tmp_path / "c.sqlite", uid="extra", text="different")
    with pytest.raises(ValueError, match="Conflicting"):
        source_connection(a, [b, c])
    d = ledger(tmp_path / "d.sqlite", name="Wrong")
    with pytest.raises(ValueError, match="identity"):
        source_connection(a, [d])
    with pytest.raises(FileNotFoundError):
        source_connection(tmp_path / "missing.sqlite")


def test_metrics_count_alternative_spans_once_and_empty_is_unknown():
    groups = [{"group_id": "g", "eligible_units": ["a", "b"], "relevance": 2}]
    result = ranking_metrics(["a", "b"], groups)
    assert result["mrr"] == 1
    assert result["source_span_group_recall"] == 1
    assert kappa([])["cohen_kappa"] is None


def test_claim_metrics_preserve_missing_human_judgments():
    from research.bavaria.completion.metrics import claim_metrics

    assert claim_metrics([])["claim_support"] is None
    result = claim_metrics(
        [
            {
                "assertion_or_question": "assertion",
                "supported": True,
                "citation_attribution_correct": False,
            },
            {"assertion_or_question": "assertion", "supported": False},
            {"assertion_or_question": "question", "supported": True},
        ]
    )
    assert result["reviewed_assertions"] == 2
    assert result["claim_support"] == 0.5
    assert result["citation_denominator"] == 1
    assert not result["independent_research_gold_released"]


@pytest.mark.parametrize(
    "field,value", [("num_threads", True), ("num_threads", 1.5), ("context_window_tokens", 2048.5)]
)
def test_generation_resource_limits_require_integers(field, value):
    from crawler.core.answer_generation import AnswerContractError, GenerationParameters

    with pytest.raises(AnswerContractError):
        GenerationParameters(**{field: value}).validate()
