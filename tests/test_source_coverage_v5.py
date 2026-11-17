from crawler.core.engine import (
    DIRECT_POLICY_REGEX,
    POLICY_CONTEXT_REGEX,
    SOURCE_DISCOVERY_REGEX,
    _is_authoritative_source_candidate,
    _looks_like_utility_provider_text,
)


def test_direct_policy_aliases():
    for value in [
        "balkonkraftwerk",
        "Balkon-PV",
        "Steckersolar",
        "Steckersolargerät",
        "Mini-PV",
    ]:
        assert DIRECT_POLICY_REGEX.search(value), value


def test_policy_context_aliases():
    for value in ["Solarenergie", "Photovoltaik", "Förderprogramm", "Zuschuss", "CO2"]:
        assert POLICY_CONTEXT_REGEX.search(value), value


def test_source_discovery_aliases():
    for value in [
        "Stadtwerke",
        "Energieversorgung",
        "Beteiligungen",
        "Eigenbetrieb",
        "Netzbetreiber",
        "Ratsinfo",
    ]:
        assert SOURCE_DISCOVERY_REGEX.search(value), value


def test_structural_authority_candidate():
    assert _is_authoritative_source_candidate(
        "https://stadtwerke-example.de/solar", "Stadtwerke"
    )
    assert not _is_authoritative_source_candidate(
        "https://www.instagram.com/example", "Instagram"
    )


def test_opaque_utility_requires_customer_signal():
    utility = """
        Strom Gas Fernwärme Energie für Privatkunden und Geschäftskunden.
        Wir sind Energieversorger und Netzbetreiber; Tarife und Netzanschluss.
    """
    authority_like = """
        Informationen zu Strom, Gas, Energie und Wärme. Die Behörde informiert
        über Versorgung, Regulierung und Energiepolitik.
    """
    assert _looks_like_utility_provider_text(utility)
    assert not _looks_like_utility_provider_text(authority_like)
