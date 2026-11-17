import pytest

from crawler.core.engine import Engine, _place_name_variants

PILOT9 = {
    "09161000": "Ingolstadt", "09162000": "München", "09362000": "Regensburg",
    "09462000": "Bayreuth", "09562000": "Erlangen", "09563000": "Fürth",
    "09564000": "Nürnberg", "09663000": "Würzburg", "09761000": "Augsburg",
}


def _engine(muni_id, seed_domain, cache):
    e = Engine.__new__(Engine)
    e.allowed_domains_by_muni = {muni_id: {seed_domain}}
    e._seed_domains_by_muni = {muni_id: {seed_domain}}
    e._discovered_authoritative_domains = {}
    e._authority_depth_by_muni = {}
    if cache is not None:
        e._municipality_name_cache = cache
    return e


@pytest.mark.parametrize("ags,adjective", [
    ("09564000", "Nürnberger"), ("09362000", "Regensburger"), ("09761000", "Augsburger"),
    ("09663000", "Würzburger"), ("09161000", "Ingolstädter"), ("09162000", "Münchner"),
    ("09562000", "Erlanger"), ("09563000", "Fürther"), ("09462000", "Bayreuther"),
])
def test_adjective_forms_match(ags, adjective):
    e = _engine(ags, "x.de", PILOT9)
    assert e._candidate_has_municipality_affinity(
        ags, "https://utility.example/", "", f"Die {adjective} Stadtwerke liefern Strom")


@pytest.mark.parametrize("ags", list(PILOT9))
def test_base_name_still_matches(ags):
    e = _engine(ags, "x.de", PILOT9)
    assert e._candidate_has_municipality_affinity(
        ags, "https://utility.example/", "", f"Energie für {PILOT9[ags]}")


def test_other_city_still_rejected():
    e = _engine("09362000", "regensburg.de", PILOT9)
    assert not e._candidate_has_municipality_affinity(
        "09362000", "https://www.stadtwerke-straubing.de/", "Stadtwerke",
        "Stadtwerke Straubing versorgen Straubing mit Strom, Gas und Wasser.")


def test_prefix_of_another_word_is_not_a_match():
    e = _engine("09563000", "fuerth.de", PILOT9)
    assert not e._candidate_has_municipality_affinity(
        "09563000", "https://x.example/", "", "Fürthermore unrelated text")


def test_missing_registry_fails_closed_for_real_ags():
    e = _engine("09362000", "regensburg.de", {})
    e._fetch_external_homepage_text = lambda url: (
        "Stadtwerke Straubing versorgen Straubing mit Strom, Gas und Wasser. Tarife.")
    assert not e._maybe_expand_allowed_domain(
        "09362000", "https://www.regensburg.de/", "https://www.stadtwerke-straubing.de/",
        "Stadtwerke")


def test_missing_registry_keeps_passthrough_for_synthetic_ids():
    e = _engine("m", "stadt-example.de", {})
    assert e._candidate_has_municipality_affinity("m", "https://a.example/", "", "")


def test_variants_are_morphological_only():
    assert _place_name_variants("muenchen") >= {"muenchen", "muenchner"}
    assert _place_name_variants("ingolstadt") >= {"ingolstadt", "ingolstaedter"}
    assert _place_name_variants("erlangen") >= {"erlangen", "erlanger"}

@pytest.mark.parametrize("ags,url", [
    ("09362000", "https://www.landkreis-regensburg.de/"),
    ("09462000", "https://www.landkreis-bayreuth.de/"),
    ("09663000", "https://www.landkreis-wuerzburg.de/"),
])
def test_landkreis_domains_rejected_for_city_targets(ags, url):
    e = _engine(ags, "x.de", PILOT9)
    assert not e._candidate_has_municipality_affinity(
        ags, url, "", f"Landkreis {PILOT9[ags]}"
    )


def test_landratsamt_anchor_rejected_for_city_target():
    e = _engine("09362000", "regensburg.de", PILOT9)
    assert not e._candidate_has_municipality_affinity(
        "09362000",
        "https://authority.example/",
        "Landratsamt Regensburg",
        "Behörde für Regensburg",
    )


def test_utility_can_mention_landkreis_in_homepage_body():
    e = _engine("09362000", "regensburg.de", PILOT9)
    assert e._candidate_has_municipality_affinity(
        "09362000",
        "https://www.rewag.de/",
        "REWAG",
        "REWAG versorgt Stadt und Landkreis Regensburg mit Energie.",
    )

