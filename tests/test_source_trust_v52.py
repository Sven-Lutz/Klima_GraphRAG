from crawler.core.engine import Engine, _looks_like_operational_utility_host


def _engine_for(muni_id, municipality_name, seed_domain):
    e = Engine.__new__(Engine)
    e.allowed_domains_by_muni = {muni_id: {seed_domain}}
    e._seed_domains_by_muni = {muni_id: {seed_domain}}
    e._discovered_authoritative_domains = {}
    e._authority_depth_by_muni = {}
    e._municipality_name_cache = {muni_id: municipality_name}
    return e


def test_affinity_accepts_nuernberg_utility_homepage():
    e = _engine_for("09564000", "Nürnberg", "nuernberg.de")
    assert e._candidate_has_municipality_affinity(
        "09564000",
        "https://fernwaerme.n-ergie.de/unsere-fernwaerme/ausbaugebiete",
        "Onlinekarte zeigt Ausbaugebiete der Fernwärme",
        "Fernwärme für Nürnberg. Die N-ERGIE liefert Fernwärme im Stadtgebiet Nürnberg.",
    )


def test_affinity_rejects_wrong_city_utility():
    e = _engine_for("09362000", "Regensburg", "regensburg.de")
    assert not e._candidate_has_municipality_affinity(
        "09362000",
        "https://www.stadtwerke-straubing.com/",
        "Stadtwerke",
        "Stadtwerke Straubing versorgen Straubing mit Strom, Gas und Wasser.",
    )


def test_operational_host_accepts_fernwaerme_but_not_solarserver():
    assert _looks_like_operational_utility_host("fernwaerme.n-ergie.de")
    assert not _looks_like_operational_utility_host("solarserver.de")


def test_direct_nuernberg_to_fernwaerme_is_allowed():
    e = _engine_for("09564000", "Nürnberg", "nuernberg.de")
    e._fetch_external_homepage_text = lambda url: (
        "Fernwärme für Nürnberg. Die N-ERGIE liefert Fernwärme im Stadtgebiet Nürnberg."
    )
    assert e._maybe_expand_allowed_domain(
        "09564000",
        "https://www.nuernberg.de/internet/klima/",
        "https://fernwaerme.n-ergie.de/unsere-fernwaerme/ausbaugebiete",
        "Onlinekarte zeigt Ausbaugebiete der Fernwärme",
    )


def test_regensburg_does_not_admit_straubing_second_hop():
    e = _engine_for("09362000", "Regensburg", "regensburg.de")
    e._fetch_external_homepage_text = lambda url: (
        "Stadtwerke Regensburg. Strom Gas Wärme Wasser. "
        "Wir versorgen Kunden. Tarife und Preise."
    )
    assert e._maybe_expand_allowed_domain(
        "09362000",
        "https://www.regensburg.de/",
        "https://www.das-stadtwerk-regensburg.de/",
        "das Stadtwerk Regensburg",
    )

    e._fetch_external_homepage_text = lambda url: (
        "Stadtwerke Straubing versorgen Straubing mit Strom, Gas und Wasser. "
        "Tarife und Preise."
    )
    assert not e._maybe_expand_allowed_domain(
        "09362000",
        "https://www.das-stadtwerk-regensburg.de/",
        "https://www.stadtwerke-straubing.com/",
        "Stadtwerke Straubing",
    )
