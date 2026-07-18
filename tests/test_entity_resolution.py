"""Tests für resolve_entities: kuratierte Aliase + konservative Substring-Regel."""
from crawler.scripts.resolve_entities import (
    RawEntity,
    normalise_entity_name,
    resolve,
    substring_merge_ok,
)


# ── Substring-Guards ─────────────────────────────────────────────────────────

def test_single_token_never_swallows_specific_name():
    # 'stadtwerke' darf NICHT 'stadtwerke münchen' schlucken
    assert substring_merge_ok("stadtwerke", "stadtwerke münchen") is False


def test_year_difference_blocks_merge():
    # Konzept vs. Konzept 2030 sind verschiedene Dokumente
    assert substring_merge_ok("integriertes klimaschutzkonzept",
                              "integriertes klimaschutzkonzept 2030") is False


def test_legitimate_substring_merges():
    # Echte Teilphrase mit >=2 Tokens und ohne Jahres-Differenz
    assert substring_merge_ok("stadtwerke augsburg",
                              "stadtwerke augsburg energie") is True


def test_no_partial_word_match():
    # 'solar anlage' ist keine Token-Folge in 'solaranlagenverbund x'
    assert substring_merge_ok("solar anlage", "solaranlagenverbund x") is False


# ── Kuratierte Aliase ────────────────────────────────────────────────────────

def _ent(name, etype="Förderprogramm", cat="Finanzen"):
    return RawEntity(name=name, entity_type=etype, category=cat)


def test_kfw_variants_resolve_to_alias():
    ents = [
        _ent("KfW"),
        _ent("KfW-Bank"),
        _ent("Kreditanstalt für Wiederaufbau"),
        _ent("kfw bankengruppe"),
    ]
    mapping = resolve(ents)
    canonicals = {mapping[(e.name, e.entity_type, e.category)] for e in ents}
    assert canonicals == {"KfW"}


def test_distinct_stadtwerke_stay_distinct():
    """Verschiedene Stadtwerke dürfen nicht zu einer Entität verschmelzen."""
    ents = [
        _ent("Stadtwerke München", etype="Akteur", cat="Strom"),
        _ent("Stadtwerke Augsburg", etype="Akteur", cat="Strom"),
        _ent("Stadtwerke", etype="Akteur", cat="Strom"),
    ]
    mapping = resolve(ents)
    muc = mapping[("Stadtwerke München", "Akteur", "Strom")]
    aux = mapping[("Stadtwerke Augsburg", "Akteur", "Strom")]
    assert muc != aux


def test_normalise_strips_legal_suffixes():
    assert normalise_entity_name("Stadtwerke Augsburg GmbH") == \
           normalise_entity_name("Stadtwerke Augsburg")
