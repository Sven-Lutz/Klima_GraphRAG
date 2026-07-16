"""Tests für die Graph-Integrität beim Neo4j-Export (Stage 3).

Kernregeln:
  1. Relationships zeigen nur auf tatsächlich extrahierte Entitäten
     (keine Phantom-Knoten aus LLM-Tippfehlern).
  2. Die Entity-Resolution-Map wird case-insensitiv angewandt —
     identisch zu extract_graph_ollama.py.
  3. Der Entity-Index ist global über alle Chunks (Chunk-übergreifende
     Beziehungen bleiben erhalten).
"""
import json
import sqlite3

import pytest

from crawler.scripts.export_graph_to_neo4j import (
    _load_canonical_map,
    _resolve,
    build_entity_key_index,
    entity_key,
    norm_name,
    safe_rel_type,
)


@pytest.fixture()
def db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "t.sqlite"))
    con.executescript(
        """
        CREATE TABLE graph_triplets (
            municipality_id TEXT, document_id TEXT, segment_rowid INTEGER,
            graph_json TEXT, extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE entity_canonical (
            original_name TEXT, entity_type TEXT, category TEXT,
            canonical_name TEXT, normalised_key TEXT, frequency INTEGER
        );
        """
    )
    yield con
    con.close()


def _insert_triplet(con, seg_rowid, entities, relationships=()):
    kg = {"entities": list(entities), "relationships": list(relationships)}
    con.execute(
        "INSERT INTO graph_triplets (municipality_id, document_id, segment_rowid, graph_json) "
        "VALUES ('09161000', 'd1', ?, ?)",
        (seg_rowid, json.dumps(kg, ensure_ascii=False)),
    )


ENT_STADTWERKE = {"name": "Stadtwerke Augsburg", "type": "Akteur", "category": "Strom"}
ENT_PV         = {"name": "PV-Anlage Messehalle", "type": "Infrastruktur", "category": "Strom"}


def test_canonical_map_is_case_insensitive(db):
    db.execute(
        "INSERT INTO entity_canonical VALUES "
        "('KFW Bank', 'Förderprogramm', 'Finanzen', 'KfW', 'kfw', 5)"
    )
    cmap = _load_canonical_map(db)
    # Schlüssel lowercase — Lookup funktioniert für jede Schreibweise
    assert _resolve("KFW Bank", cmap) == "KfW"
    assert _resolve("kfw bank", cmap) == "KfW"
    assert _resolve("Kfw BANK", cmap) == "KfW"
    # Unbekannte Namen bleiben unverändert
    assert _resolve("BAFA", cmap) == "BAFA"


def test_entity_index_is_global_across_chunks(db):
    # Entität in Chunk 1, Beziehung könnte aus Chunk 2 darauf zeigen
    _insert_triplet(db, 1, [ENT_STADTWERKE])
    _insert_triplet(db, 2, [ENT_PV])
    index = build_entity_key_index(db, {})
    assert "stadtwerke augsburg" in index
    assert "pv-anlage messehalle" in index
    assert index["stadtwerke augsburg"] == entity_key("Stadtwerke Augsburg", "Akteur", "Strom")


def test_entity_index_applies_canonical_resolution(db):
    db.execute(
        "INSERT INTO entity_canonical VALUES "
        "('Stadtwerke Augsburg GmbH', 'Akteur', 'Strom', 'Stadtwerke Augsburg', 'x', 3)"
    )
    _insert_triplet(db, 1, [dict(ENT_STADTWERKE, name="Stadtwerke Augsburg GmbH")])
    cmap = _load_canonical_map(db)
    index = build_entity_key_index(db, cmap)
    # Kanonischer Name im Index, nicht die Variante
    assert "stadtwerke augsburg" in index
    assert "stadtwerke augsburg gmbh" not in index


def test_dangling_relationship_endpoints_not_in_index(db):
    """Beziehung auf nie extrahierte Entität → Endpunkt fehlt im Index.
    Die Exportschleife verwirft solche Relationships (n_dangling)."""
    _insert_triplet(
        db, 1, [ENT_STADTWERKE],
        [{"source_entity": "Stadtwerke Augsburg",
          "relation_type": "BAUT",
          "target_entity": "Halluzinierte Anlage XY",   # nicht in entities!
          "evidence": "..."}],
    )
    index = build_entity_key_index(db, {})
    assert "stadtwerke augsburg" in index
    assert "halluzinierte anlage xy" not in index


def test_norm_name_and_rel_type():
    assert norm_name("  Stadtwerke   Augsburg ") == "Stadtwerke Augsburg"
    assert safe_rel_type("fördert") == "FÖRDERT"
    assert safe_rel_type("GEHÖRT ZU") == "GEHÖRT_ZU"
    # Unbekannte Typen fallen auf die generische Beziehung zurück
    assert safe_rel_type("ERFUNDEN") == "BEZIEHT_SICH_AUF"
