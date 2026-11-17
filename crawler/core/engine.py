# crawler/core/engine.py
from __future__ import annotations

import html as _html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib import robotparser
from urllib.parse import urljoin as _urljoin
from urllib.parse import urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter

from crawler.core.canonical import Canonicalizer
from crawler.core.config import TLS_VERIFY
from crawler.core.models import CrawlTask, FetchResult
from crawler.core.parsers.html_parser import parse_html
from crawler.core.parsers.pdf_parser import parse_pdf
from crawler.core.scheduler import PriorityScheduler
from crawler.core.storage import Storage, default_worker_id



def _repair_html_query_url(url: str) -> str:
    """Fix escaped HTML parameter separators before canonicalization.

    E.g. ?amp%3BFID=...&ModID=10 -> ?FID=...&ModID=10.
    Only changes malformed query-key prefixes, not arbitrary query values.
    """
    from urllib.parse import urlsplit
    if not url or not isinstance(url, str):
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = parts.query
    if not re.search(r"(?i)(?:^|&)(?:amp;|amp%3b)", query):
        return url
    query = re.sub(r"(?i)&amp;", "&", query)
    query = re.sub(
        r"(?i)(^|&)(?:(?:amp%3b)|(?:amp;))+(?=[^&=]+=)",
        lambda match: match.group(1),
        query,
    )
    return parts._replace(query=query).geturl()

_HIGH_IMPACT_WORDS = [
    r"klima\w*",
    r"klimaschutz\w*",
    r"klimaanpassung\w*",
    r"klimaneutral\w*",
    r"co2\b",
    r"thg\b",
    r"treibhausgas\w*",
    r"emission\w*",
    r"treibhausgasbilanz\w*",
    r"energiebericht\w*",
    r"klimabericht\w*",
    r"klimaschutzkonzept\w*",
    r"klimafahrplan\w*",
    r"energie-?\s?und\s?klimakonzept\w*",
    r"energie\w*",
    r"erneuerbar\w*",
    r"fernw(?:ä|ae)rme\w*",
    r"nahw(?:ä|ae)rme\w*",
    r"w(?:ä|ae)rmenetz\w*",
    r"bhkw\b",
    r"blockheizkraftwerk\w*",
    r"w(?:ä|ae)rmepump\w*",
    r"geotherm\w*",
    r"biogas\w*",
    r"wasserkraft\w*",
    r"wasserstoff\b",
    r"h2\b",
    r"solar\w*",
    r"photovoltaik\w*",
    r"pv\b",
    r"windkraft\w*",
    r"windenergie\w*",
    r"speicher\w*",
    r"stromnetz\w*",
    r"smart\s?grid\w*",
    r"led\b",
    r"strombeschaffung\w*",
    r"sanier\w*",
    r"d(?:ä|ae)mm\w*",
    r"energieeffizienz\w*",
    r"passivhaus\w*",
    r"effizienzhaus\w*",
    r"kfw-\d+",
    r"geg\b",
    r"geb(?:ä|ae)udeenergiegesetz\w*",
    r"quartierskonzept\w*",
    r"fl(?:ä|ae)chennutzungsplan\w*",
    r"fnp\b",
    r"bebauungsplan\w*",
    r"nachverdichtung\w*",
    r"mobilit(?:ä|ae)t\w*",
    r"verkehr\w*",
    r"verkehrswende\w*",
    r"emobilit(?:ä|ae)t\w*",
    r"elektromobilit(?:ä|ae)t\w*",
    r"radverkehr\w*",
    r"radweg\w*",
    r"fahrrad\w*",
    r"verkehrsentwicklungsplan\w*",
    r"vep\b",
    r"lades(?:ä|ae)ul\w*",
    r"wallbox\w*",
    r"ladinfrastruktur\w*",
    r"carsharing\w*",
    r"(?:ö|oe)pnv\b",
    r"f(?:ö|oe)rder\w*",
    r"zuschuss\w*",
    r"mittel\w*",
    r"finanz\w*",
    r"investition\w*",
    r"projekttr(?:ä|ae)ger\w*",
    r"f(?:ö|oe)rderprogramm\w*",
    r"bewilligungsbescheid\w*",
    r"eigenanteil\w*",
    r"nki\b",
    r"kfw\b",
    r"bafa\b",
    r"efre\b",
    r"eler\b",
    r"eu-?f(?:ö|oe)rder\w*",
    r"beschluss\w*",
    r"beschlussvorlage\w*",
    r"antrag\w*",
    r"drucksache\w*",
    r"vorlage\w*",
    r"tagesordnungspunkt\w*",
    r"sitzung\w*",
    r"niederschrift\w*",
    r"protokoll\w*",
    r"klimabeirat\w*",
    r"umweltbeirat\w*",
    r"stadtwerk\w*",
]
HIGH_IMPACT_REGEX = re.compile(r"\b(?:" + "|".join(_HIGH_IMPACT_WORDS) + r")", re.IGNORECASE)

_NEGATIVE_WORDS = [
    r"impressum\b",
    r"datenschutz\b",
    r"barrierefreiheit\b",
    r"stellenangebot\w*",
    r"(?:ö|oe)ffnungszeiten\b",
    r"kontakt\b",
]
NEGATIVE_REGEX = re.compile(r"\b(?:" + "|".join(_NEGATIVE_WORDS) + r")", re.IGNORECASE)

# Policy-focused crawl signals. These are topic terms, never benchmark URLs/domains.
# They are intentionally reusable across municipalities and keep evaluation leakage out
# of the crawler while giving the frontier enough signal to find the policy pages.
_DIRECT_POLICY_WORDS = [
    r"balkonkraftwerk\w*",
    r"balkon[-\s]?pv\w*",
    r"stecker[-\s]?solar\w*",
    r"steckersolarger(?:ä|ae)t\w*",
    r"mini[-\s]?pv\w*",
]
DIRECT_POLICY_REGEX = re.compile(
    r"\b(?:" + "|".join(_DIRECT_POLICY_WORDS) + r")", re.IGNORECASE
)

_POLICY_CONTEXT_WORDS = [
    r"solar\w*",
    r"photovoltaik\w*",
    r"pv\b",
    r"f(?:ö|oe)rder\w*",
    r"zuschuss\w*",
    r"klimafonds?\w*",
    r"co2\b",
]
POLICY_CONTEXT_REGEX = re.compile(
    r"\b(?:" + "|".join(_POLICY_CONTEXT_WORDS) + r")", re.IGNORECASE
)

# URLs/anchors that are especially useful for discovering municipal utilities and
# other authoritative operators. These pages are valuable even when they do not
# themselves contain the final policy evidence.
_SOURCE_DISCOVERY_WORDS = [
    r"stadtwerk\w*",
    r"gemeindewerk\w*",
    r"kommunalwerk\w*",
    r"versorgung\w*",
    r"energieversorg\w*",
    r"netzbetreiber\w*",
    r"beteiligung\w*",
    r"tochtergesellschaft\w*",
    r"eigenbetrieb\w*",
    r"kommunalunternehmen\w*",
    r"daseinsvorsorge\w*",
    r"ratsinfo\w*",
    r"allris\w*",
]
SOURCE_DISCOVERY_REGEX = re.compile(
    r"\b(?:" + "|".join(_SOURCE_DISCOVERY_WORDS) + r")", re.IGNORECASE
)

_RIS_REGEX = re.compile(
    r"(?:session|ratsinfo|allris|/bi/(?:vo|si|kp)|(?:^|[./])ris[./])",
    re.IGNORECASE,
)

_TEXT_URL_REGEX = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"'\]\)]+"
)


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract naked web URLs from parsed text/PDFs.

    Municipal reports frequently name an authoritative provider only inside a PDF
    bibliography or company section. Treating those URLs as discovery candidates
    closes a major gap without hard-coding provider domains.
    """
    out: list[str] = []
    seen: Set[str] = set()
    for match in _TEXT_URL_REGEX.finditer(text or ""):
        raw = match.group(0).rstrip(".,;:!?}>")
        if raw.lower().startswith("www."):
            raw = "https://" + raw
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out

# External official/authoritative source signals.
#
# Municipal climate-policy evidence is not always hosted on the municipality's
# own domain. Relevant sources may live on municipal utilities, council
# information systems, administrative associations, or district websites.
#
# This is discovery logic only. It deliberately contains no benchmark-specific
# domains.
_AUTHORITATIVE_SOURCE_REGEX = re.compile(
    r"\b(?:"
    r"stadtwerke?|"
    r"gemeindewerke?|"
    r"kommunalwerke?|"
    r"energieversorgung|"
    r"versorgungsbetriebe?|"
    r"ratsinformationssystem|"
    r"b(?:ü|ue)rgerinformationssystem|"
    r"ratsinfo|"
    r"allris|"
    r"verwaltungsgemeinschaft|"
    r"landkreis|"
    r"kreisverwaltung|"
    r"landratsamt|"
    r"amtsblatt"
    r")\b",
    re.IGNORECASE,
)

_NON_AUTHORITATIVE_EXTERNAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}


def _is_authoritative_source_candidate(
    url: str,
    anchor: Optional[str] = None,
) -> bool:
    """Return True when an external link looks like an official related source.

    The decision is based on generic structural signals in the URL/domain and
    anchor text, never on benchmark-specific domains.
    """
    try:
        parsed = urlsplit(url or "")
    except Exception:
        return False

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False

    domain = parsed.hostname.lower().strip(".")

    if any(
        domain == blocked or domain.endswith("." + blocked)
        for blocked in _NON_AUTHORITATIVE_EXTERNAL_DOMAINS
    ):
        return False

    candidate_text = f"{domain} {parsed.path} {anchor or ''}"
    return bool(_AUTHORITATIVE_SOURCE_REGEX.search(candidate_text))


_UTILITY_SERVICE_REGEX = re.compile(
    r"\b(?:"
    r"strom|"
    r"(?:erd)?gas|"
    r"fernw(?:ä|ae)rme|"
    r"w(?:ä|ae)rme|"
    r"trinkwasser|"
    r"wasser|"
    r"energie|"
    r"(?:ö|oe)kostrom|"
    r"grundversorgung|"
    r"stromnetz|"
    r"gasnetz|"
    r"w(?:ä|ae)rmenetz"
    r")\b",
    re.IGNORECASE,
)

_UTILITY_ORGANIZATION_REGEX = re.compile(
    r"\b(?:"
    r"stadtwerke?|"
    r"gemeindewerke?|"
    r"kommunalwerke?|"
    r"energieversorger|"
    r"energieversorgung|"
    r"versorg(?:t|en|ung|er)|"
    r"grundversorgung|"
    r"privatkunden|"
    r"gesch(?:ä|ae)ftskunden|"
    r"netzbetreiber|"
    r"stadtwerke\s*/\s*kommunen"
    r")\b",
    re.IGNORECASE,
)

_UTILITY_STRONG_PROVIDER_REGEX = re.compile(
    r"\b(?:"
    r"stadtwerke?|"
    r"gemeindewerke?|"
    r"kommunalwerke?|"
    r"energieversorger|"
    r"privatkunden|"
    r"gesch(?:ä|ae)ftskunden|"
    r"versorg(?:t|en|er)"
    r")\b",
    re.IGNORECASE,
)


_UTILITY_CUSTOMER_REGEX = re.compile(
    r"\b(?:"
    r"privatkunden|"
    r"gesch(?:ä|ae)ftskunden|"
    r"grundversorgung|"
    r"netzbetreiber|"
    r"netzanschluss|"
    r"stromtarif\w*|"
    r"gast(?:a|ä|ae)rif\w*|"
    r"z(?:ä|ae)hler\w*|"
    r"liefervertrag\w*|"
    r"energieversorger"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_utility_provider_text(text: str) -> bool:
    text = text or ""

    service_hits = {
        match.group(0).lower()
        for match in _UTILITY_SERVICE_REGEX.finditer(text)
    }
    if len(service_hits) < 2:
        return False

    explicit_provider_signal = bool(
        re.search(
            r"\b(?:"
            r"stadtwerke?|"
            r"gemeindewerke?|"
            r"kommunalwerke?|"
            r"energieversorger|"
            r"privatkunden|"
            r"gesch(?:ä|ae)ftskunden|"
            r"netzbetreiber"
            r")\b",
            text,
            re.IGNORECASE,
        )
    )

    customer_supply_signal = bool(
        re.search(
            r"\bversorg\w*\b[\s\S]{0,80}\bkund(?:e|en|innen)\b",
            text,
            re.IGNORECASE,
        )
    )

    return explicit_provider_signal or customer_supply_signal


_UTILITY_PROVIDER_ACTION_REGEX = re.compile(
    r"\b(?:"
    r"liefert|liefern|"
    r"beliefert|beliefern|"
    r"versorgt|versorgen|"
    r"erzeugt|erzeugen|"
    r"betreibt|betreiben"
    r")\b",
    re.IGNORECASE,
)

_UTILITY_CUSTOMER_SERVICE_REGEX = re.compile(
    r"\b(?:"
    r"kundenportal|kundencenter|kundenservice|"
    r"privatkunden|gesch(?:ä|ae)ftskunden|"
    r"tarif\w*|"
    r"preis(?:e|blatt|garantie)?\w*|"
    r"hausanschluss|netzanschluss|anschluss\w*|"
    r"abrechnung\w*|rechnung\w*|"
    r"z(?:ä|ae)hler\w*|"
    r"st(?:ö|oe)rungsdienst|bereitschaftsdienst|"
    r"f(?:ö|oe)rder(?:ung|ungen|programm\w*)"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_operational_utility_text(text: str) -> bool:
    """Recognise a utility from service + operator + customer-facing signals."""
    text = text or ""
    service_hits = {
        match.group(0).lower()
        for match in _UTILITY_SERVICE_REGEX.finditer(text)
    }
    has_provider_action = bool(_UTILITY_PROVIDER_ACTION_REGEX.search(text))
    has_customer_service = bool(_UTILITY_CUSTOMER_SERVICE_REGEX.search(text))
    return len(service_hits) >= 2 and has_provider_action and has_customer_service



_AFFINITY_GENERIC_TOKENS = {
    "stadt", "markt", "gemeinde", "kreis", "landkreis",
    "bad", "der", "die", "das", "am", "an", "im", "in",
    "ober", "unter", "bayern", "bayerisch",
}

_UTILITY_HOST_SIGNAL_REGEX = re.compile(
    r"(?:^|[.-])(?:"
    r"fernwaerme|waerme|strom|gas|wasser|energie|"
    r"versorgung|versorg|netz"
    r")(?:[.-]|$)",
    re.IGNORECASE,
)


def _normalise_affinity_text(value: str) -> str:
    value = (value or "").lower()
    value = (
        value.replace("ä", "ae")
             .replace("ö", "oe")
             .replace("ü", "ue")
             .replace("ß", "ss")
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_operational_utility_host(domain: str) -> bool:
    return bool(_UTILITY_HOST_SIGNAL_REGEX.search((domain or "").lower()))



_REAL_AGS_REGEX = re.compile(r"\d{8}")


def _place_name_variants(token: str) -> set:
    """Base form plus common German place adjectives for one normalised token.

    nuernberg -> nuernberger, muenchen -> muenchner, erlangen -> erlanger,
    ingolstadt -> ingolstaedter. Purely morphological; no names are hardcoded.
    """
    token = (token or "").strip()
    if not token:
        return set()
    variants = {token, token + "er", token + "ern", token + "s"}
    if token.endswith("en") and len(token) > 4:
        stem = token[:-2]
        variants |= {stem + "er", stem + "ner"}
    if token.endswith("stadt"):
        variants.add(token[: -len("stadt")] + "staedter")
    return variants


def _matches_place(variants: set, haystack: str) -> bool:
    return any(re.search(rf"\b{re.escape(v)}\b", haystack) for v in variants if v)


def _segment_features(text: str) -> Tuple[int, int, int]:
    """
    Returns (impact_score, hit_count, is_negative) based on Engine's domain regex.
    This score is meant to be simple, explainable, and stable for paper methodology.
    """
    t = text or ""
    hits = len(HIGH_IMPACT_REGEX.findall(t))
    neg = 1 if NEGATIVE_REGEX.search(t) else 0

    # Strongly weight topical hits, penalize boilerplate-like segments.
    score = 25 * hits - (80 if neg else 0)

    # Slight preference for semantically richer segments.
    score += min(len(t) // 250, 8)

    return score, hits, neg


try:
    from crawler.core.traps import TrapDetector

    _trap_detector = TrapDetector(
        block_extensions=[
            "jpg",
            "jpeg",
            "png",
            "gif",
            "svg",
            "mp4",
            "zip",
            "rar",
            "css",
            "js",
            "xml",
            "exe",
            "doc",
            "docx",
        ],
        block_path_patterns=[
            "/kalender",
            "veranstaltungen",
            "termine",
            "sitzungskalender",
            "monat=",
            "jahr=",
            "month=",
            "year=",
            "datum=",
            "weinabend",
            "kirchencafe",
            "sprechstunde",
            "jahreshauptversammlung",
            "regio-cup",
            "firmung",
            "vereinsmeisterschaft",
            "print=",
            "drucken=",
            "ansicht=druck",
            "type=print",
            "sort=",
            "order=",
            "orderby=",
            "sortierung=",
            "/galerie",
            "/bilder",
            "gallery",
            "bildarchiv",
            "/login",
            "/register",
            "/buergerservice/anmeldung",
            "warenkorb",
            "wasseruntersuchung",
            "trinkwasser",
            "prüfbericht",
            "befund",
            "analytik",
            "abfallkalender",
            "abfuhrkalender",
            "müllgebühren",
            "abfalltrennung",
            "ferienprogramm",
            "kindergarten",
            "kinderhaus",
            "schule",
            "oberschule",
            "bos-flyer",
            "wahlergebnisse",
            "wahlvorschläge",
            "stimmzettel",
            "bekanntmachung",
            "umleitungsstrecke",
            "baustelle",
            "veranstaltungskalender",
        ],
        pagination_tokens=["page=", "offset=", "start=", "/page/"],
    )
except ImportError:
    _trap_detector = None


def _is_trap(url: str, depth: int) -> bool:
    if _trap_detector is None:
        return False
    return _trap_detector.should_block(url, depth)


@dataclass(frozen=True)
class EngineLimits:
    max_depth: int = 6
    max_pages_per_muni: int = 2000
    max_file_size_mb: int = 200
    # Coverage controls: newly discovered authoritative sources receive a small
    # guaranteed exploration window, while RIS sources get a soft (not hard) cap.
    source_min_explore_pages: int = 20
    source_sitemap_max_urls: int = 2000
    source_sitemap_max_files: int = 12
    ris_soft_cap_ratio: float = 0.35


class Engine:
    def __init__(
        self,
        keywords: dict,
        limits: EngineLimits = EngineLimits(),
        user_agent: str = "KIT-ClimatePolicyCrawler/1.0 (research)",
        request_timeout_seconds: int = 20,
        max_redirects: int = 10,
        respect_robots_txt: bool = True,
        tls_verify: Optional[bool] = None,
        min_delay_seconds_per_domain: float = 0.2,
        canonicalizer: Optional[Canonicalizer] = None,
        allowed_domains_by_muni: Optional[Mapping[str, Set[str]]] = None,
        worker_id: Optional[str] = None,
        seed_additional_paths: Optional[list[str]] = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.storage = Storage(db_path=db_path)
        self.scheduler = PriorityScheduler()
        self.keywords = keywords
        self.limits = limits

        self.worker_id = worker_id or default_worker_id()

        self.timeout_connect = 5.0
        self.timeout_read = float(request_timeout_seconds)
        self.max_redirects = int(max_redirects)
        self.respect_robots_txt = bool(respect_robots_txt)
        # Default aus config (KLIMA_TLS_VERIFY); kommunale Server haben oft
        # kaputte Zertifikatsketten, daher ist Verifikation standardmäßig aus
        self.tls_verify = TLS_VERIFY if tls_verify is None else bool(tls_verify)
        if not self.tls_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.min_delay_seconds_per_domain = float(min_delay_seconds_per_domain)

        self.canon = canonicalizer or Canonicalizer(
            strip_fragment=True,
            drop_query_prefixes=["utm_", "pk_"],
            drop_query_keys=[
                "fbclid",
                "gclid",
                "session",
                "jsessionid",
                "sid",
                "phpsessid",
                "msclkid",
            ],
            normalize_trailing_slash=True,
        )

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/pdf,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            }
        )
        self._session.max_redirects = self.max_redirects

        self.user_agent = user_agent
        self._last_request_ts_by_domain: Dict[str, float] = {}
        self._pages_by_muni: Dict[str, int] = {}
        self._pages_by_muni_domain: Dict[Tuple[str, str], int] = {}
        self._bootstrapped_domains: Set[Tuple[str, str]] = set()
        self._discovered_authoritative_domains: Dict[str, Set[str]] = {}
        # robots.txt-Cache pro Host: (parser|None, sitemap_urls)
        self._robots_cache: Dict[str, Tuple[Optional[robotparser.RobotFileParser], List[str]]] = {}
        # Enqueue-Dedup: verhindert, dass dieselbe URL den Heap mehrfach füllt
        self._enqueued: Set[str] = set()

        # Cache external-homepage probes used for related utility discovery.
        # This prevents repeatedly fetching the same external domain when a
        # municipality links to it from multiple pages.
        self._external_homepage_text_cache: Dict[str, str] = {}

        self.allowed_domains_by_muni: Dict[str, Set[str]] = {
            str(k): {self._norm_domain(d) for d in v if d}
            for k, v in (allowed_domains_by_muni or {}).items()
        }

        # Keep the original municipality domains separate from domains discovered
        # during crawling. Only links from these original domains may expand scope,
        # preventing recursive trust expansion.
        self._seed_domains_by_muni: Dict[str, Set[str]] = {
            muni_id: set(domains)
            for muni_id, domains in self.allowed_domains_by_muni.items()
        }

        self.seed_additional_paths: list[str] = list(seed_additional_paths or [])

    @staticmethod
    def _norm_domain(domain: str) -> str:
        d = (domain or "").strip().lower()
        if not d:
            return ""

        if "://" in d or d.startswith("//"):
            try:
                parsed = urlsplit(d if "://" in d else "http:" + d)
                d = parsed.netloc
            except Exception:
                pass
        d = d.split(":", 1)[0]
        if d.startswith("www."):
            d = d[4:]
        d = d.rstrip(".")

        # Reject malformed DNS labels, e.g. broken URLs extracted from PDF text
        # such as "www.ge-" or "lpv-augs-". Valid DNS labels may contain
        # hyphens internally, but must not begin or end with one.
        labels = d.split(".")
        if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
            return ""

        return d

    @staticmethod
    def _split(url: str):
        try:
            return urlsplit(url)
        except Exception:
            return None

    def _scheme_domain(self, url: str) -> Tuple[str, str]:
        s = self._split(url)
        if s is None:
            return "", ""
        return (s.scheme or "").lower(), self._norm_domain(s.netloc)

    def _is_allowed(self, muni_id: str, url: str) -> bool:
        scheme, domain = self._scheme_domain(url)
        if scheme not in ("http", "https") or not domain:
            return False
        allowed = self.allowed_domains_by_muni.get(muni_id)
        if not allowed:
            return False
        return domain in allowed

    def _origin_url(self, url: str) -> str:
        """Return scheme://host/ for robust root-relative seed generation."""
        parsed = self._split(url)
        if parsed is None or not parsed.netloc:
            return url
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/"

    def _frontier_priority(
        self,
        muni_id: str,
        url: str,
        anchor: Optional[str],
        extra_bonus: int = 0,
    ) -> int:
        """Dynamic priority that balances policy relevance and source coverage."""
        muni_id = str(muni_id)
        prio = self.score(url, anchor) + int(extra_bonus)
        _, domain = self._scheme_domain(url)
        if not domain:
            return prio

        pages_on_domain = self._pages_by_muni_domain.get((muni_id, domain), 0)
        seed_domains = self._seed_domains_by_muni.get(muni_id, set())
        discovered = self._discovered_authoritative_domains.get(muni_id, set())

        # Ensure a newly validated authority gets enough early exploration to
        # expose its own navigation/sitemaps instead of losing to a huge RIS queue.
        min_explore = max(0, int(self.limits.source_min_explore_pages))
        if domain in discovered and domain not in seed_domains and pages_on_domain < min_explore:
            prio += max(80, 320 - 10 * pages_on_domain)

        # RIS is valuable, but it must not consume nearly the whole municipal
        # budget when municipality/utility sources are also available. This is
        # deliberately a soft penalty; RIS remains crawlable when useful.
        soft_cap = max(20, int(self.limits.max_pages_per_muni * self.limits.ris_soft_cap_ratio))
        if _RIS_REGEX.search(url or "") and pages_on_domain >= soft_cap:
            prio -= 350

        return prio

    def _enqueue_if_new(
        self,
        muni_id: str,
        url: str,
        depth: int,
        parent_url: Optional[str],
        anchor: Optional[str] = None,
        extra_bonus: int = 0,
    ) -> bool:
        canon = lambda raw_url: self.canon.normalize(_repair_html_query_url(raw_url))
        url_c = canon(url)
        if not url_c or url_c in self._enqueued:
            return False
        if not self._is_allowed(str(muni_id), url_c) or _is_trap(url_c, depth):
            return False
        self.scheduler.enqueue(
            CrawlTask(
                str(muni_id),
                url_c,
                depth=depth,
                parent_url=parent_url,
                anchor_text=anchor or "",
            ),
            self._frontier_priority(str(muni_id), url_c, anchor, extra_bonus),
        )
        self._enqueued.add(url_c)
        return True

    def _bootstrap_authoritative_domain(
        self,
        muni_id: str,
        entry_url: str,
        parent_url: Optional[str],
        depth: int,
    ) -> None:
        """Seed a newly trusted authority from homepage, sitemap and policy paths.

        This is the core source-coverage fix: discovering a utility is not enough;
        we immediately inspect its own authoritative URL inventory for policy pages.
        """
        muni_id = str(muni_id)
        _, domain = self._scheme_domain(entry_url)
        if not domain:
            return
        key = (muni_id, domain)
        if key in self._bootstrapped_domains:
            return
        self._bootstrapped_domains.add(key)

        root = self._origin_url(entry_url)
        next_depth = min(depth + 1, int(self.limits.max_depth))

        # Homepage and a small set of generic policy entry points. 404/5xx pages
        # are filtered before storage, so these probes do not consume document budget.
        self._enqueue_if_new(muni_id, root, next_depth, parent_url, extra_bonus=260)
        for path in (
            "foerderung",
            "foerderungen",
            "foerderprogramme",
            "solar",
            "photovoltaik",
            "balkonkraftwerk",
            "steckersolar",
            "klima",
            "co2",
        ):
            self._enqueue_if_new(
                muni_id,
                _urljoin(root, path),
                next_depth,
                parent_url,
                extra_bonus=220,
            )

        # Use robots-declared sitemaps; when absent, try the two conventional
        # locations. Sitemap URLs are filtered by policy/discovery relevance.
        _, sitemaps = self._load_robots(root)
        if not sitemaps:
            sitemaps = [_urljoin(root, "sitemap.xml"), _urljoin(root, "sitemap_index.xml")]

        max_files = max(1, int(self.limits.source_sitemap_max_files))
        max_urls = max(100, int(self.limits.source_sitemap_max_urls))
        seen_urls = 0
        for sm_url in sitemaps[:max_files]:
            remaining = max_urls - seen_urls
            if remaining <= 0:
                break
            for su in self._sitemap_urls(sm_url, max_urls=remaining):
                seen_urls += 1
                sc = self.canon.normalize(su)
                if not sc or not self._is_allowed(muni_id, sc) or _is_trap(sc, next_depth):
                    continue
                bonus = 0
                if DIRECT_POLICY_REGEX.search(sc):
                    bonus = 520
                elif POLICY_CONTEXT_REGEX.search(sc):
                    bonus = 300
                elif SOURCE_DISCOVERY_REGEX.search(sc):
                    bonus = 180
                elif sc.lower().endswith(".pdf") and HIGH_IMPACT_REGEX.search(sc):
                    bonus = 140
                else:
                    continue
                self._enqueue_if_new(
                    muni_id, sc, next_depth, parent_url, extra_bonus=bonus
                )

    def _discover_authorities_from_segments(
        self,
        muni_id: str,
        source_url: str,
        segments: Iterable[object],
        parent_url: Optional[str],
        depth: int,
    ) -> int:
        """Do not promote naked URLs from HTML/PDF text into crawl authority.

        Text URLs remain preserved in the parsed segments as provenance/evidence,
        but only explicit HTML links may expand the trusted crawl scope.
        """
        return 0

        discovered = 0
        for segment in segments:
            text = getattr(segment, "text", "") or ""
            for candidate in _extract_urls_from_text(text):
                candidate_c = self.canon.normalize(candidate)
                if not candidate_c:
                    continue
                if self._is_allowed(str(muni_id), candidate_c):
                    continue
                expanded = self._maybe_expand_allowed_domain(
                    str(muni_id),
                    source_url=source_url,
                    link_url=candidate_c,
                    anchor=candidate,
                    require_utility_like=True,
                )
                if not expanded:
                    continue
                discovered += 1
                next_depth = min(depth + 1, int(self.limits.max_depth))
                queued = self._enqueue_if_new(
                    str(muni_id),
                    candidate_c,
                    depth=next_depth,
                    parent_url=parent_url,
                    anchor=candidate,
                    extra_bonus=420,
                )
                if queued:
                    self._bootstrap_authoritative_domain(
                        str(muni_id),
                        entry_url=candidate_c,
                        parent_url=parent_url,
                        depth=next_depth,
                    )
        return discovered

    def _fetch_external_homepage_text(
        self,
        url: str,
        max_chars: int = 12000,
    ) -> str:
        """Fetch a small text sample from an external domain homepage.

        This is used only as a second-stage check for opaque utility brands
        whose domain/anchor does not itself contain structural terms such as
        ``Stadtwerke``. Results are cached per normalized domain.
        """
        scheme, domain = self._scheme_domain(url)
        if scheme not in {"http", "https"} or not domain:
            return ""

        cache = getattr(self, "_external_homepage_text_cache", None)
        if cache is None:
            cache = {}
            self._external_homepage_text_cache = cache
        if domain in cache:
            return cache[domain]

        # Do not probe known social-platform domains.
        if any(
            domain == blocked or domain.endswith("." + blocked)
            for blocked in _NON_AUTHORITATIVE_EXTERNAL_DOMAINS
        ):
            cache[domain] = ""
            return ""

        parsed = self._split(url)
        if parsed is None or not parsed.netloc:
            cache[domain] = ""
            return ""

        homepage = f"{parsed.scheme or 'https'}://{parsed.netloc}/"

        try:
            fr = self.fetch(homepage, max_attempts=2)
        except Exception:
            cache[domain] = ""
            return ""

        if int(fr.status_code) >= 400 or not fr.body:
            cache[domain] = ""
            return ""

        # Do not transfer trust through a homepage redirect to another domain.
        _, final_domain = self._scheme_domain(fr.url_final)
        if final_domain != domain:
            cache[domain] = ""
            return ""

        if not self._looks_like_html(fr.url_final, fr.content_type):
            cache[domain] = ""
            return ""

        raw = fr.body.decode("utf-8", errors="ignore")
        raw = re.sub(
            r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
            " ",
            raw,
        )
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        visible_text = re.sub(r"\s+", " ", _html.unescape(raw)).strip()
        visible_text = visible_text[:max_chars]

        cache[domain] = visible_text
        return visible_text

    def _municipality_registry_path(self) -> Optional[Path]:
        """Locate municipalities.sqlite relative to the repo, then the CWD."""
        candidates = [
            Path(__file__).resolve().parents[2] / "crawler" / "data" / "db"
            / "municipalities.sqlite",
            Path("crawler") / "data" / "db" / "municipalities.sqlite",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _municipality_name(self, muni_id: str) -> str:
        """Return the registry name for one municipality, cached in memory."""
        cache = getattr(self, "_municipality_name_cache", None)
        if cache is None:
            cache = {}
            registry = self._municipality_registry_path()
            if registry is None:
                print(
                    "[affinity] municipalities.sqlite not found - external scope "
                    "expansion is disabled for real AGS ids (fail-closed)",
                    flush=True,
                )
            else:
                try:
                    import sqlite3

                    con = sqlite3.connect(str(registry))
                    try:
                        for ags, name in con.execute("SELECT ags, name FROM municipalities"):
                            if ags is not None and name:
                                cache[str(ags).strip()] = str(name).strip()
                    finally:
                        con.close()
                except Exception as exc:
                    print(f"[affinity] registry unreadable ({exc}) - fail-closed", flush=True)
                    cache = {}
            self._municipality_name_cache = cache
        return str(cache.get(str(muni_id), "") or "")

    def _candidate_has_municipality_affinity(
        self,
        muni_id: str,
        link_url: str,
        anchor: Optional[str],
        homepage_text: str,
    ) -> bool:
        """Require evidence that an external authority belongs to this municipality.

        Fail-closed: a real 8-digit AGS without a registry name is rejected.
        Only synthetic (non-AGS) ids used by unit tests keep the pass-through.
        """
        muni_id = str(muni_id)
        name = self._municipality_name(muni_id)
        if not name:
            return not _REAL_AGS_REGEX.fullmatch(muni_id)

        name_norm = _normalise_affinity_text(name)
        haystack = _normalise_affinity_text(
            f"{link_url or ''} {anchor or ''} {homepage_text or ''}"
        )
        if not name_norm or not haystack:
            return False

        # Administrative-boundary guard:
        # a municipality must not inherit authority from a Landkreis/Landratsamt.
        # Inspect only candidate identity signals here. Homepage body text may
        # legitimately mention both a city and its surrounding Landkreis.
        if _REAL_AGS_REGEX.fullmatch(muni_id):
            from urllib.parse import urlsplit

            candidate_host = _normalise_affinity_text(
                (urlsplit(link_url or "").hostname or "")
            )
            host_tokens = set(candidate_host.split())
            anchor_norm = _normalise_affinity_text(anchor or "")

            if {"landkreis", "landratsamt"}.intersection(host_tokens):
                return False

            if (
                anchor_norm.startswith("landkreis ")
                or anchor_norm.startswith("landratsamt ")
            ):
                return False

        words = name_norm.split()
        head = " ".join(words[:-1])
        full_variants = {
            f"{head} {variant}".strip() for variant in _place_name_variants(words[-1])
        }
        if _matches_place(full_variants, haystack):
            return True

        tokens = [
            token
            for token in words
            if len(token) >= 5 and token not in _AFFINITY_GENERIC_TOKENS
        ]
        if not tokens:
            tokens = [token for token in words if len(token) >= 3]
        if not tokens:
            return False

        strongest = max(tokens, key=len)
        return _matches_place(_place_name_variants(strongest), haystack)

    def _maybe_expand_allowed_domain(
        self,
        muni_id: str,
        source_url: str,
        link_url: str,
        anchor: Optional[str] = None,
        require_utility_like: bool = False,
    ) -> bool:
        """Expand scope only for bounded authorities tied to this municipality."""
        muni_id = str(muni_id)

        _, source_domain = self._scheme_domain(source_url)
        _, link_domain = self._scheme_domain(link_url)
        if not source_domain or not link_domain:
            return False

        # Business/provider directories may be useful documents, but their
        # outbound company links must not grant crawl authority.
        source_parts = self._split(source_url)
        source_path = (source_parts.path or '').lower() if source_parts else ''
        if re.search(r'/(?:branchenbuch|firmenverzeichnis|unternehmensverzeichnis|gewerbeverzeichnis|anbieter(?:verzeichnis)?)(?:/|$)', source_path):
            return False

        allowed_map = getattr(self, "allowed_domains_by_muni", {})
        if link_domain in allowed_map.get(muni_id, set()):
            return True

        seed_domains = getattr(self, "_seed_domains_by_muni", {}).get(muni_id, set())

        discovered_map = getattr(self, "_discovered_authoritative_domains", None)
        if discovered_map is None:
            discovered_map = {}
            self._discovered_authoritative_domains = discovered_map
        discovered = discovered_map.get(muni_id, set())

        depth_by_muni = getattr(self, "_authority_depth_by_muni", None)
        if depth_by_muni is None:
            depth_by_muni = {}
            self._authority_depth_by_muni = depth_by_muni

        depth_map = depth_by_muni.setdefault(
            muni_id,
            {domain: 0 for domain in seed_domains},
        )
        for domain in seed_domains:
            depth_map.setdefault(domain, 0)
        for domain in discovered:
            depth_map.setdefault(domain, 1)

        source_depth = depth_map.get(source_domain)
        if source_depth is None:
            return False

        if source_depth >= 2:
            return False

        if any(
            link_domain == blocked or link_domain.endswith("." + blocked)
            for blocked in _NON_AUTHORITATIVE_EXTERNAL_DOMAINS
        ):
            return False

        # V5.2 always samples the target's own homepage so an external site must
        # be connected to the current municipality, not merely look like a utility.
        homepage_text = self._fetch_external_homepage_text(link_url)

        if not self._candidate_has_municipality_affinity(
            muni_id,
            link_url=link_url,
            anchor=anchor,
            homepage_text=homepage_text,
        ):
            return False

        structural = _is_authoritative_source_candidate(link_url, anchor)
        utility_like = (
            _looks_like_utility_provider_text(homepage_text)
            or _looks_like_operational_utility_text(homepage_text)
            or _looks_like_operational_utility_host(link_domain)
        )

        # Depth 0 can also admit municipality-specific official systems such as RIS.
        # Depth 1 -> 2 must independently look like an operational utility/provider.
        authoritative = (
            utility_like
            if require_utility_like
            else ((structural or utility_like) if source_depth == 0 else utility_like)
        )
        if not authoritative:
            return False

        self.allowed_domains_by_muni.setdefault(muni_id, set()).add(link_domain)
        self._discovered_authoritative_domains.setdefault(muni_id, set()).add(link_domain)
        depth_map[link_domain] = source_depth + 1
        return True

    def score(self, url: str, anchor: Optional[str]) -> int:
        u = (url or "").lower()
        a = (anchor or "").lower()
        score = 10

        if ".pdf" in u:
            score += 260

        if DIRECT_POLICY_REGEX.search(u):
            score += 520
        if a and DIRECT_POLICY_REGEX.search(a):
            score += 420

        if POLICY_CONTEXT_REGEX.search(u):
            score += 220
        if a and POLICY_CONTEXT_REGEX.search(a):
            score += 160

        if SOURCE_DISCOVERY_REGEX.search(u):
            score += 180
        if a and SOURCE_DISCOVERY_REGEX.search(a):
            score += 140

        if _RIS_REGEX.search(u):
            score += 120

        if HIGH_IMPACT_REGEX.search(u):
            score += 100
        if a and HIGH_IMPACT_REGEX.search(a):
            score += 70

        if NEGATIVE_REGEX.search(u):
            score -= 50
        if a and NEGATIVE_REGEX.search(a):
            score -= 20

        return score

    # ── robots.txt + Sitemaps ────────────────────────────────────────────────

    def _load_robots(self, url: str) -> Tuple[Optional[robotparser.RobotFileParser], List[str]]:
        """Lädt robots.txt einmal pro Host; liefert (Parser, Sitemap-URLs).

        Parser None = keine/nicht lesbare robots.txt → alles erlaubt.
        """
        s = self._split(url)
        if s is None or not s.netloc:
            return None, []
        host = s.netloc.lower()
        if host in self._robots_cache:
            return self._robots_cache[host]

        rp: Optional[robotparser.RobotFileParser] = None
        sitemaps: List[str] = []
        robots_url = f"{s.scheme or 'https'}://{s.netloc}/robots.txt"
        try:
            resp = self._session.get(robots_url, timeout=(5, 10), verify=self.tls_verify)
            if resp.status_code == 200 and resp.text:
                rp = robotparser.RobotFileParser()
                rp.parse(resp.text.splitlines())
                sitemaps = [
                    line.split(":", 1)[1].strip()
                    for line in resp.text.splitlines()
                    if line.lower().startswith("sitemap:")
                ]
        except Exception:
            rp = None

        self._robots_cache[host] = (rp, sitemaps)
        return rp, sitemaps

    def _robots_allowed(self, url: str) -> bool:
        if not self.respect_robots_txt:
            return True
        rp, _ = self._load_robots(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    _RE_SITEMAP_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)

    def _sitemap_urls(self, sitemap_url: str, max_urls: int = 500, _depth: int = 0) -> List[str]:
        """Extrahiert URLs aus einer Sitemap (eine Ebene sitemapindex-Verschachtelung)."""
        if _depth > 1 or max_urls <= 0:
            return []
        try:
            resp = self._session.get(sitemap_url, timeout=(5, 15), verify=self.tls_verify)
            if resp.status_code != 200:
                return []
            text = resp.text
        except Exception:
            return []

        locs = self._RE_SITEMAP_LOC.findall(text)
        if "<sitemapindex" in text.lower():
            out: List[str] = []
            for child in locs[:10]:
                out.extend(self._sitemap_urls(child, max_urls - len(out), _depth + 1))
                if len(out) >= max_urls:
                    break
            return out[:max_urls]
        return locs[:max_urls]

    def _polite_sleep(self, domain: str) -> None:
        if not domain or self.min_delay_seconds_per_domain <= 0:
            return
        now = time.time()
        last = self._last_request_ts_by_domain.get(domain)
        if last is not None:
            dt = now - last
            if dt < self.min_delay_seconds_per_domain:
                time.sleep(self.min_delay_seconds_per_domain - dt)
        self._last_request_ts_by_domain[domain] = time.time()

    _RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def fetch(self, url: str, max_attempts: int = 3) -> FetchResult:
        """Fetch mit Retry + Backoff für transiente Fehler (Timeout, 429, 5xx).

        Ohne Retry 'verbrennt' ein einzelner transienter Fehler die URL
        bis zum nächsten kompletten Re-Run.
        """
        _, domain = self._scheme_domain(url)
        lim_bytes = int(self.limits.max_file_size_mb) * 1024 * 1024

        last_err: Optional[Exception] = None
        for attempt in range(max_attempts):
            self._polite_sleep(domain)
            try:
                with self._session.get(
                    url,
                    timeout=(self.timeout_connect, self.timeout_read),
                    allow_redirects=True,
                    headers={"Connection": "close"},
                    verify=self.tls_verify,
                    stream=True,  # stream; enforce hard size limit below
                ) as resp:
                    status = int(resp.status_code)
                    headers = {str(k): str(v) for k, v in (resp.headers or {}).items()}
                    content_type = str(resp.headers.get("Content-Type") or "") or None
                    url_final = str(resp.url)

                    # Transienter Serverfehler → Backoff und neuer Versuch
                    if status in self._RETRYABLE_STATUS and attempt < max_attempts - 1:
                        retry_after = headers.get("Retry-After", "")
                        delay = int(retry_after) if retry_after.isdigit() else 2**attempt
                        time.sleep(min(delay, 30))
                        continue

                    cl = headers.get("Content-Length")
                    if cl and cl.isdigit() and int(cl) > lim_bytes:
                        body = b""
                    else:
                        data = bytearray()
                        for chunk in resp.iter_content(chunk_size=1024 * 64):
                            if not chunk:
                                break
                            data.extend(chunk)
                            if len(data) > lim_bytes:
                                data = bytearray()
                                break
                        body = bytes(data)

                return FetchResult(
                    url_final=url_final,
                    status_code=status,
                    content_type=content_type,
                    body=body,
                    headers=headers,
                )

            except (
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
            ) as e:
                last_err = e
                if attempt < max_attempts - 1:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"timeout:{type(e).__name__}") from e
            except requests.exceptions.RequestException as e:
                # Nicht-transient (z.B. TooManyRedirects, InvalidURL) → sofort raus
                raise RuntimeError(f"request_error:{type(e).__name__}:{e}") from e

        raise RuntimeError(f"timeout:{type(last_err).__name__ if last_err else 'unknown'}")

    def _over_size_limit(self, fr: FetchResult) -> bool:
        lim_bytes = int(self.limits.max_file_size_mb) * 1024 * 1024
        cl = fr.headers.get("Content-Length")
        if cl:
            try:
                return int(cl) > lim_bytes
            except ValueError:
                return False
        return False

    @staticmethod
    def _looks_like_html(url: str, content_type: Optional[str]) -> bool:
        ctype = (content_type or "").lower()
        if "text/html" in ctype or "application/xhtml" in ctype:
            return True
        u = (url or "").lower()
        return u.endswith((".html", ".htm"))

    @staticmethod
    def _looks_like_pdf(url: str, content_type: Optional[str]) -> bool:
        ctype = (content_type or "").lower()
        if "application/pdf" in ctype:
            return True
        u = (url or "").lower()
        return u.endswith(".pdf")

    def run_claimed_batch(self, batch_size: int = 1) -> None:
        seeds = self.storage.claim_next_seed_jobs(worker_id=self.worker_id, limit=batch_size)
        if not seeds:
            print("[engine] no pending seed_jobs", flush=True)
            return
        self.run(seeds)

    def run(self, seeds: Iterable[Tuple[str, str]]) -> None:
        canon = lambda raw_url: self.canon.normalize(_repair_html_query_url(raw_url))
        max_depth = int(self.limits.max_depth)
        max_pages = int(self.limits.max_pages_per_muni)
        lim_bytes = int(self.limits.max_file_size_mb) * 1024 * 1024
        autostop_threshold = 0  # No PDF does not mean no policy evidence

        # Materialise seeds into a list so the iterable can be traversed
        # twice (seed enqueue + finish_seed_job loop) even when passed as a generator.
        seeds = list(seeds)

        # pages_since_last_pdf is tracked per-municipality to prevent one
        # PDF-poor municipality from triggering autostop for the next one.
        pages_since_last_pdf: Dict[str, int] = {}
        aborted_munis: Set[str] = set()
        self._enqueued = set()

        for muni_id, url in seeds:
            muni_id = str(muni_id)
            self._pages_by_muni.setdefault(muni_id, 0)

            if muni_id not in self.allowed_domains_by_muni:
                _, d = self._scheme_domain(url)
                if d:
                    self.allowed_domains_by_muni[muni_id] = {d}
                    self._seed_domains_by_muni[muni_id] = {d}

            url_c_seed = canon(url) or url
            if not self._is_allowed(muni_id, url):
                self.storage.mark_visited(url_c_seed, -1, "seed out of scope")
                continue

            self.scheduler.enqueue(CrawlTask(muni_id, url, depth=0), 100000)
            self._enqueued.add(url_c_seed)

            # Seed additional paths from the site ORIGIN, not from a file-like
            # homepage path (e.g. .../rathaus.html). This fixes systematic 404s.
            seed_root = self._origin_url(url)
            for path in self.seed_additional_paths:
                extra_url = _urljoin(seed_root, path.lstrip("/"))
                self._enqueue_if_new(
                    muni_id, extra_url, depth=1, parent_url=url_c_seed, extra_bonus=70
                )

            # Sitemaps are the highest-recall reproducible way to find deep policy
            # pages. Besides climate terms, include source-discovery URLs so pages
            # such as Beteiligungen/Stadtwerke can reveal opaque utility brands.
            _, sitemaps = self._load_robots(seed_root)
            if not sitemaps:
                sitemaps = [
                    _urljoin(seed_root, "sitemap.xml"),
                    _urljoin(seed_root, "sitemap_index.xml"),
                ]
            n_sitemap = 0
            max_files = max(1, int(self.limits.source_sitemap_max_files))
            max_urls = max(100, int(self.limits.source_sitemap_max_urls))
            seen_urls = 0
            for sm_url in sitemaps[:max_files]:
                remaining = max_urls - seen_urls
                if remaining <= 0:
                    break
                for su in self._sitemap_urls(sm_url, max_urls=remaining):
                    seen_urls += 1
                    sc = canon(su)
                    if (
                        not sc
                        or sc in self._enqueued
                        or not self._is_allowed(muni_id, sc)
                        or _is_trap(sc, 1)
                    ):
                        continue
                    bonus = 0
                    if DIRECT_POLICY_REGEX.search(sc):
                        bonus = 520
                    elif POLICY_CONTEXT_REGEX.search(sc):
                        bonus = 300
                    elif SOURCE_DISCOVERY_REGEX.search(sc):
                        bonus = 240
                    elif HIGH_IMPACT_REGEX.search(sc) or sc.lower().endswith(".pdf"):
                        bonus = 80
                    else:
                        continue
                    if self._enqueue_if_new(
                        muni_id, sc, depth=1, parent_url=url_c_seed, extra_bonus=bonus
                    ):
                        n_sitemap += 1
            if n_sitemap:
                print(
                    f"[sitemap] {muni_id}: {n_sitemap} policy/source URLs queued",
                    flush=True,
                )

        while self.scheduler.has_next():
            task = self.scheduler.next()

            # Autostop: Kommune einmal markieren, restliche Tasks in O(1) verwerfen
            if task.municipality_id in aborted_munis:
                continue
            if autostop_threshold and pages_since_last_pdf.get(task.municipality_id, 0) >= autostop_threshold:
                print(
                    f"🛑 Autostop für {task.municipality_id}: Seit {autostop_threshold} "
                    f"Seiten kein PDF. Breche Kommune ab.",
                    flush=True,
                )
                aborted_munis.add(task.municipality_id)
                continue

            if task.depth > max_depth:
                continue
            if not self._is_allowed(task.municipality_id, task.url):
                continue
            if _is_trap(task.url, task.depth):
                continue

            pages = self._pages_by_muni.get(task.municipality_id, 0)
            if pages >= max_pages:
                continue

            url_c = canon(task.url)
            if not url_c:
                continue

            if not self._robots_allowed(task.url):
                self.storage.mark_visited(url_c, -1, "robots disallow")
                continue

            # Ein Query statt vier: visited-Status, Fehler-Flag und doc_id
            visited, errored, doc_id = self.storage.get_visited_status(url_c)
            if visited:
                if doc_id is not None:
                    self.storage.link_document_to_municipality(task.municipality_id, doc_id)
                if errored:
                    print(f"[retry] {task.url} (previously errored)", flush=True)
                else:
                    print(f"[skip] {task.url} (already visited)", flush=True)
                    continue

            try:
                print(
                    f"[fetch] depth={task.depth} muni={task.municipality_id} url={task.url}",
                    flush=True,
                )

                fr = self.fetch(task.url)

                muni = task.municipality_id
                if self._looks_like_pdf(task.url, fr.content_type):
                    pages_since_last_pdf[muni] = 0
                else:
                    pages_since_last_pdf[muni] = pages_since_last_pdf.get(muni, 0) + 1

                status = int(fr.status_code)

                # Do not store 4xx/5xx error pages as policy documents and do not
                # let guessed/fallback URLs consume the municipal document budget.
                if status >= 400:
                    self.storage.mark_visited(url_c, status, f"http {status}")
                    continue

                if not self._is_allowed(task.municipality_id, fr.url_final):
                    self.storage.mark_visited(url_c, status, "redirect out of scope")
                    continue

                if self._over_size_limit(fr):
                    self.storage.mark_visited(url_c, status, "oversize")
                    continue

                if not fr.body or len(fr.body) > lim_bytes:
                    self.storage.mark_visited(url_c, status, "body error/limit")
                    continue

                with self.storage.transaction():
                    doc_id = self.storage.store_raw(task.municipality_id, url_c, fr)

                    if self._looks_like_html(task.url, fr.content_type):
                        parse_result = parse_html(fr, fr.url_final)

                        if len(parse_result.segments) > 0:
                            self.storage.store_segments_scored(
                                doc_id, parse_result.segments, _segment_features
                            )
                            n_text_sources = self._discover_authorities_from_segments(
                                task.municipality_id,
                                source_url=fr.url_final,
                                segments=parse_result.segments,
                                parent_url=url_c,
                                depth=task.depth,
                            )
                            if n_text_sources:
                                print(
                                    f"[source-discovery] {task.municipality_id}: "
                                    f"{n_text_sources} authority URL(s) found in page text",
                                    flush=True,
                                )

                        # Store HTML meta tags (description, date, author, ...)
                        if parse_result.meta:
                            self.storage.update_document_meta(doc_id, parse_result.meta)

                        # Links EINMAL kanonisieren; Ergebnis für Outlink-Store
                        # und Enqueue wiederverwenden
                        canonical_links: list[tuple[str, str]] = []
                        for link in parse_result.out_links:
                            link_url = (
                                link[0] if isinstance(link, tuple) else getattr(link, "url", link)
                            )
                            anchor = (
                                link[1] if isinstance(link, tuple) else getattr(link, "anchor", "")
                            )
                            lc = canon(link_url)
                            if lc:
                                canonical_links.append((lc, anchor))

                        # Persist outlinks for GraphRAG document link graph
                        if canonical_links:
                            self.storage.store_outlinks(doc_id, canonical_links)

                        next_depth = task.depth + 1
                        if next_depth <= max_depth:
                            for link_c, anchor in canonical_links:
                                if link_c in self._enqueued:
                                    continue

                                new_authoritative_source = False

                                if not self._is_allowed(task.municipality_id, link_c):
                                    expanded = self._maybe_expand_allowed_domain(
                                        task.municipality_id,
                                        source_url=fr.url_final,
                                        link_url=link_c,
                                        anchor=anchor,
                                    )
                                    if not expanded:
                                        continue
                                    new_authoritative_source = True

                                bonus = 360 if new_authoritative_source else 0
                                queued = self._enqueue_if_new(
                                    task.municipality_id,
                                    link_c,
                                    depth=next_depth,
                                    parent_url=url_c,
                                    anchor=anchor,
                                    extra_bonus=bonus,
                                )

                                if queued and new_authoritative_source:
                                    self._bootstrap_authoritative_domain(
                                        task.municipality_id,
                                        entry_url=link_c,
                                        parent_url=url_c,
                                        depth=next_depth,
                                    )

                    elif self._looks_like_pdf(task.url, fr.content_type):
                        print(f"[parse] Extrahiere PDF: {fr.url_final}", flush=True)
                        parse_result = parse_pdf(fr, fr.url_final)

                        if len(parse_result.segments) > 0:
                            self.storage.store_segments_scored(
                                doc_id, parse_result.segments, _segment_features
                            )
                            n_text_sources = self._discover_authorities_from_segments(
                                task.municipality_id,
                                source_url=fr.url_final,
                                segments=parse_result.segments,
                                parent_url=url_c,
                                depth=task.depth,
                            )
                            if n_text_sources:
                                print(
                                    f"[source-discovery] {task.municipality_id}: "
                                    f"{n_text_sources} authority URL(s) found in PDF text",
                                    flush=True,
                                )

                    self.storage.mark_visited(url_c, status)

                self._pages_by_muni[task.municipality_id] = pages + 1
                _, stored_domain = self._scheme_domain(fr.url_final)
                if stored_domain:
                    key = (task.municipality_id, stored_domain)
                    self._pages_by_muni_domain[key] = self._pages_by_muni_domain.get(key, 0) + 1

            except Exception as e:
                self.storage.mark_visited(url_c, -1, str(e))

        for muni_id, _ in seeds:
            try:
                self.storage.finish_seed_job(str(muni_id), ok=True)
            except Exception:
                pass
