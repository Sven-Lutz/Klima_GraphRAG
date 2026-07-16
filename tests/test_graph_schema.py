"""Tests für das KnowledgeGraph-Schema (Stage 2b — Graph-Extraktion).

Das Pydantic-Schema ist der Vertrag zwischen LLM-Output und allen
Downstream-Stages (resolve_entities, Neo4j-Export, Visualisierung).
"""
import pytest
from pydantic import ValidationError

from crawler.scripts.extract_graph_ollama import Entity, KnowledgeGraph, Relationship

VALID_ENTITY = {
    "name": "Stadtwerke Augsburg",
    "type": "Akteur",
    "category": "Strom",
    "status": "Existierend",
    "metrics": {"year": "2024"},
}

VALID_REL = {
    "source_entity": "Stadtwerke Augsburg",
    "relation_type": "BAUT",
    "target_entity": "PV-Anlage Messehalle",
    "evidence": "Die Stadtwerke errichten eine PV-Anlage auf der Messehalle.",
}


def test_valid_graph_parses():
    kg = KnowledgeGraph(entities=[VALID_ENTITY], relationships=[VALID_REL])
    assert kg.entities[0].name == "Stadtwerke Augsburg"
    assert kg.relationships[0].relation_type == "BAUT"


def test_empty_graph_is_valid():
    kg = KnowledgeGraph()
    assert kg.entities == [] and kg.relationships == []
    # Genau das Format der Gleaning-Abbruchbedingung
    kg2 = KnowledgeGraph.model_validate({"entities": [], "relationships": []})
    assert kg2.entities == []


def test_metrics_default_to_empty_dict():
    e = Entity(name="X", type="Maßnahme", category="Wärme", status="Geplant")
    assert e.metrics == {}


def test_invalid_entity_type_rejected():
    bad = dict(VALID_ENTITY, type="Firma")  # nicht im Literal
    with pytest.raises(ValidationError):
        Entity.model_validate(bad)


def test_invalid_category_rejected():
    bad = dict(VALID_ENTITY, category="Verkehr")
    with pytest.raises(ValidationError):
        Entity.model_validate(bad)


def test_invalid_relation_type_rejected():
    bad = dict(VALID_REL, relation_type="UNTERSTÜTZT")
    with pytest.raises(ValidationError):
        Relationship.model_validate(bad)


def test_missing_evidence_rejected():
    bad = {k: v for k, v in VALID_REL.items() if k != "evidence"}
    with pytest.raises(ValidationError):
        Relationship.model_validate(bad)


def test_llm_json_roundtrip():
    """Simuliert den Weg LLM-JSON → Pydantic → graph_json-Spalte → zurück."""
    kg = KnowledgeGraph(entities=[VALID_ENTITY], relationships=[VALID_REL])
    restored = KnowledgeGraph.model_validate_json(kg.model_dump_json())
    assert restored == kg
