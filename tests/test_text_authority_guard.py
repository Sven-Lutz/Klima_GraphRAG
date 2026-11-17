import crawler.core.engine as engine_mod
from crawler.core.engine import Engine


def _engine():
    e = Engine.__new__(Engine)
    e.allowed_domains_by_muni = {"09761000": {"augsburg.de"}}
    e._seed_domains_by_muni = {"09761000": {"augsburg.de"}}
    e._discovered_authoritative_domains = {}
    e._authority_depth_by_muni = {}
    e._municipality_name_cache = {"09761000": "Augsburg"}
    e._fetch_external_homepage_text = lambda url: "Augsburg"
    e._candidate_has_municipality_affinity = lambda *args, **kwargs: True
    return e


def test_normal_structural_link_still_allowed(monkeypatch):
    e = _engine()
    monkeypatch.setattr(engine_mod, "_is_authoritative_source_candidate", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_utility_provider_text", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_text", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_host", lambda *a, **k: False)
    assert e._maybe_expand_allowed_domain(
        "09761000", "https://www.augsburg.de/", "https://official-system.example/", "Official system"
    )


def test_text_discovery_rejects_private_contractor(monkeypatch):
    e = _engine()
    monkeypatch.setattr(engine_mod, "_is_authoritative_source_candidate", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_utility_provider_text", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_text", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_host", lambda *a, **k: False)
    assert not e._maybe_expand_allowed_domain(
        "09761000", "https://www.augsburg.de/file.pdf", "https://private-installer.example/", "https://private-installer.example/", require_utility_like=True
    )


def test_text_discovery_keeps_real_utility(monkeypatch):
    e = _engine()
    monkeypatch.setattr(engine_mod, "_is_authoritative_source_candidate", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_utility_provider_text", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_text", lambda *a, **k: False)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_host", lambda *a, **k: False)
    assert e._maybe_expand_allowed_domain(
        "09761000", "https://www.augsburg.de/file.pdf", "https://utility.example/", "https://utility.example/", require_utility_like=True
    )


def test_business_directory_cannot_grant_external_authority(monkeypatch):
    e = _engine()
    monkeypatch.setattr(engine_mod, "_is_authoritative_source_candidate", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_utility_provider_text", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_text", lambda *a, **k: True)
    monkeypatch.setattr(engine_mod, "_looks_like_operational_utility_host", lambda *a, **k: True)
    assert not e._maybe_expand_allowed_domain(
        "09761000",
        "https://www.augsburg.de/service/branchenbuch/P/635.html",
        "https://solar24.gmbh/",
        "Homepage",
    )
