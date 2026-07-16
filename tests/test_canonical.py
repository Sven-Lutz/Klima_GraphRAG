"""Tests für crawler/core/canonical.py — URL-Kanonisierung (Crawl-Dedup)."""
from crawler.core.canonical import Canonicalizer


def make(**kwargs) -> Canonicalizer:
    return Canonicalizer(enable_cache=False, **kwargs)


def test_fragment_is_stripped():
    c = make()
    assert c.normalize("https://stadt.de/klima#abschnitt") == "https://stadt.de/klima"


def test_host_is_lowercased():
    c = make()
    assert c.normalize("https://Stadt.DE/Klima") == "https://stadt.de/Klima"


def test_default_ports_stripped():
    c = make()
    assert c.normalize("https://stadt.de:443/klima") == "https://stadt.de/klima"
    assert c.normalize("http://stadt.de:80/klima") == "http://stadt.de/klima"
    # Non-Default-Port bleibt erhalten
    assert c.normalize("https://stadt.de:8443/klima") == "https://stadt.de:8443/klima"


def test_trailing_slash_normalized_except_root():
    c = make()
    assert c.normalize("https://stadt.de/klima/") == "https://stadt.de/klima"
    assert c.normalize("https://stadt.de/") == "https://stadt.de/"
    assert c.normalize("https://stadt.de") == "https://stadt.de/"


def test_multi_slash_collapsed():
    c = make()
    assert c.normalize("https://stadt.de//klima///schutz") == "https://stadt.de/klima/schutz"


def test_query_params_sorted_deterministically():
    c = make()
    a = c.normalize("https://stadt.de/s?b=2&a=1")
    b = c.normalize("https://stadt.de/s?a=1&b=2")
    assert a == b == "https://stadt.de/s?a=1&b=2"


def test_tracking_params_dropped_by_prefix():
    c = make(drop_query_prefixes=["utm_"])
    assert c.normalize("https://stadt.de/s?utm_source=x&id=5") == "https://stadt.de/s?id=5"


def test_drop_query_keys():
    c = make(drop_query_keys=["sessionid"])
    assert c.normalize("https://stadt.de/s?sessionid=abc&id=5") == "https://stadt.de/s?id=5"


def test_non_http_schemes_rejected():
    c = make()
    assert c.normalize("ftp://stadt.de/datei.pdf") == ""
    assert c.normalize("mailto:info@stadt.de") == ""
    assert c.normalize("javascript:void(0)") == ""
    assert c.normalize("") == ""


def test_strip_www_option():
    assert make(strip_www=True).normalize("https://www.stadt.de/") == "https://stadt.de/"
    assert make(strip_www=False).normalize("https://www.stadt.de/") == "https://www.stadt.de/"


def test_normalize_many_filters_unusable():
    c = make()
    urls = ["https://stadt.de/a", "ftp://x.de", "", "https://stadt.de/b"]
    assert c.normalize_many(urls) == ["https://stadt.de/a", "https://stadt.de/b"]


def test_normalize_with_change_flag():
    c = make()
    r1 = c.normalize_with_change("https://stadt.de/klima")
    assert r1.changed is False
    r2 = c.normalize_with_change("https://stadt.de/klima#x")
    assert r2.changed is True and r2.url == "https://stadt.de/klima"
