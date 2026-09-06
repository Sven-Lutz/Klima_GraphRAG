from crawler.core.engine import (
    Engine,
    _is_authoritative_source_candidate,
    _looks_like_utility_provider_text,
)


def test_detects_authoritative_external_sources():
    assert _is_authoritative_source_candidate(
        "https://werke-example.de/solar",
        "Stadtwerke Beispiel",
    )

    assert _is_authoritative_source_candidate(
        "https://ris-example.de/sitzungen",
        "Ratsinformationssystem",
    )

    assert _is_authoritative_source_candidate(
        "https://vg-example.de/",
        "Verwaltungsgemeinschaft Beispiel",
    )


def test_rejects_generic_external_sources():
    assert not _is_authoritative_source_candidate(
        "https://facebook.com/example",
        "Facebook",
    )

    assert not _is_authoritative_source_candidate(
        "https://example-news.de/article",
        "Weitere Informationen",
    )

    assert not _is_authoritative_source_candidate(
        "https://tourismus-example.de/",
        "Tourismus",
    )


def test_authoritative_domain_expansion_is_one_hop_only():
    engine = Engine.__new__(Engine)

    engine.allowed_domains_by_muni = {
        "test-muni": {"stadt-example.de"},
    }
    engine._seed_domains_by_muni = {
        "test-muni": {"stadt-example.de"},
    }

    allowed = engine._maybe_expand_allowed_domain(
        "test-muni",
        source_url="https://stadt-example.de/klimaschutz",
        link_url="https://stadtwerke-example.de/solar",
        anchor="Stadtwerke Beispiel",
    )

    assert allowed
    assert "stadtwerke-example.de" in engine.allowed_domains_by_muni["test-muni"]

    allowed_second_hop = engine._maybe_expand_allowed_domain(
        "test-muni",
        source_url="https://stadtwerke-example.de/solar",
        link_url="https://werke-another-example.de/",
        anchor="Stadtwerke Andere",
    )

    assert not allowed_second_hop
    assert "werke-another-example.de" not in engine.allowed_domains_by_muni["test-muni"]


def test_detects_utility_provider_from_generic_homepage_text():
    assert _looks_like_utility_provider_text(
        """
        Privatkunden Geschäftskunden
        Strom Erdgas Wasser Fernwärme
        Grundversorgung Zähler
        Stadtwerke Kommunen
        """
    )

    assert _looks_like_utility_provider_text(
        """
        Wir versorgen Kunden mit Strom, Erdgas,
        Wärme und Trinkwasser und fördern
        erneuerbare Energien.
        """
    )


def test_rejects_non_utility_homepage_text():
    assert not _looks_like_utility_provider_text(
        """
        Willkommen in unserer Region.
        Tourismus, Veranstaltungen, Kultur,
        Hotels und Freizeitangebote.
        """
    )


def test_expands_opaque_utility_domain_from_homepage_text():
    engine = Engine.__new__(Engine)

    engine.allowed_domains_by_muni = {
        "test-muni": {"stadt-example.de"},
    }
    engine._seed_domains_by_muni = {
        "test-muni": {"stadt-example.de"},
    }

    engine._fetch_external_homepage_text = lambda url: (
        "Privatkunden Geschäftskunden "
        "Strom Erdgas Wasser Fernwärme "
        "Grundversorgung Energieberatung"
    )

    allowed = engine._maybe_expand_allowed_domain(
        "test-muni",
        source_url="https://stadt-example.de/klimaschutz",
        link_url="https://utility-example.de/energie",
        anchor="Mehr erfahren",
    )

    assert allowed
    assert "utility-example.de" in engine.allowed_domains_by_muni["test-muni"]
def test_rejects_energy_authority_with_single_provider_signal():
    text = (
        "Informationen zu Energie, Gas, Trinkwasser und Wasser. "
        "Themen der Energieversorgung und des Umweltschutzes."
    )

    assert not _looks_like_utility_provider_text(text)


def test_rejects_network_regulator_with_single_provider_signal():
    text = (
        "Informationen zu Energie, Strom, Gas, Stromnetz "
        "und Grundversorgung sowie regulatorischen Verfahren."
    )

    assert not _looks_like_utility_provider_text(text)
