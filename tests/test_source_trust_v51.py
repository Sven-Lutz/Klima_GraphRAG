from crawler.core.engine import Engine, _looks_like_operational_utility_text


def _bare_engine():
    engine = Engine.__new__(Engine)
    engine.allowed_domains_by_muni = {"m": {"stadt-example.de"}}
    engine._seed_domains_by_muni = {"m": {"stadt-example.de"}}
    engine._discovered_authoritative_domains = {}
    engine._authority_depth_by_muni = {}
    return engine


def test_operational_utility_detector_accepts_real_provider_language():
    text = (
        "Fernwärme und Strom. Die Energiegesellschaft liefert Wärme und erzeugt Strom. "
        "Preise, Förderprogramme und Anschlussinformationen."
    )
    assert _looks_like_operational_utility_text(text)


def test_operational_utility_detector_rejects_topic_only_authority_text():
    text = "Die Behörde informiert über Strom, Gas, Wärme, Netze, Regulierung und Energiepolitik."
    assert not _looks_like_operational_utility_text(text)


def test_verified_second_hop_allowed_but_third_hop_blocked():
    engine = _bare_engine()

    assert engine._maybe_expand_allowed_domain(
        "m",
        "https://stadt-example.de/klima",
        "https://stadtwerke-example.de/energie",
        "Stadtwerke Beispiel",
    )

    engine._fetch_external_homepage_text = lambda url: (
        "Strom Erdgas Wärme Trinkwasser. Wir versorgen Kunden zuverlässig. "
        "Tarife, Preise, Hausanschluss und Zähler."
    )

    assert engine._maybe_expand_allowed_domain(
        "m",
        "https://stadtwerke-example.de/energie",
        "https://utility-two.example/produkte",
        "Mehr erfahren",
    )

    assert not engine._maybe_expand_allowed_domain(
        "m",
        "https://utility-two.example/produkte",
        "https://utility-three.example/strom",
        "Strom",
    )
