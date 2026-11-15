# crawler/core/parsers/html_parser.py
from __future__ import annotations

import re
from copy import deepcopy
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from crawler.core.models import FetchResult, ParseResult, Segment

MIN_SEGMENT_LENGTH = 30

_ASSET_EXT_PATTERN = (
    r"\.(jpg|jpeg|png|gif|webp|svg|mp4|mp3|avi|mov|mkv|zip|rar|7z"
    r"|tar|gz|css|js|woff|woff2|ttf|ico)(?:\?|$)"
)
_RE_ASSET = re.compile(_ASSET_EXT_PATTERN, re.IGNORECASE)

_RE_WS = re.compile(r"\s+")
_RE_MULTI_SLASH = re.compile(r"/{2,}")


def _clean_text(text: str) -> str:
    return _RE_WS.sub(" ", text or "").strip()


def _build_heading_context(path: list[tuple[int, str]]) -> str | None:
    """Build a breadcrumb string from the current heading path,
    e.g. 'Klimaschutz > Solarenergie'."""
    if not path:
        return None
    return " > ".join(txt for _, txt in path)


def _table_text(table: Tag, table_number: int) -> str:
    """Preserve source table structure without guessing ambiguous header mappings.

    The ordinal locates the table in the cleaned document for a fixed source.
    Retained spans, header IDs/references and row groups express source relations.
    """
    clean = deepcopy(table)
    allowed_tags = {
        "table",
        "caption",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "colgroup",
        "col",
    }
    allowed_attrs = {"id", "headers", "scope", "rowspan", "colspan", "span", "abbr"}
    for node in [clean, *clean.find_all(True)]:
        if node.name not in allowed_tags:
            # Preserve boundaries around inline/block content inside cells.
            node.insert_before(" ")
            node.insert_after(" ")
            node.unwrap()
        else:
            node.attrs = {key: value for key, value in node.attrs.items() if key in allowed_attrs}
    return f"HTML table {table_number}\n{clean}"


# Block boundaries partition the DOM; descendants are never flattened a second time.
_CONTENT_BLOCKS = {
    "html",
    "body",
    "main",
    "article",
    "section",
    "div",
    "header",
    "blockquote",
    "p",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "pre",
    "figure",
    "figcaption",
    "address",
    "details",
    "summary",
    "hr",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
_SEGMENT_EXCLUDED = {
    "head",
    "nav",
    "footer",
    "aside",
    "script",
    "style",
    "noscript",
    "svg",
    "button",
    "input",
    "select",
    "textarea",
    "option",
}
_EXCLUDED_ROLES = {"navigation", "menu", "menubar", "button"}


def _content_events(parent: Tag):
    """Yield inline text nodes or whole block boundaries, in source order."""
    for child in parent.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            yield child
        elif isinstance(child, Tag):
            roles = set(str(child.get("role", "")).lower().split())
            if child.name in _SEGMENT_EXCLUDED or roles & _EXCLUDED_ROLES:
                continue
            if child.name in _CONTENT_BLOCKS:
                yield child
            elif child.name == "br":
                yield " "
            else:
                yield from _content_events(child)


def _short_field(text: str, has_context: bool) -> bool:
    """Generic field/value or numeric evidence, not a climate keyword allowlist."""
    if re.search(r"\S[^:]*:\s*\S", text):
        return True
    return bool(
        re.search(r"\d", text)
        and (
            has_context
            or re.search(r"[^\W\d_]", text)
            or re.fullmatch(r"\d{1,4}(?:[./-]\d{1,4}){1,2}\.?", text)
        )
    )


def _content_segments(soup: BeautifulSoup) -> list[Segment]:
    segments: list[Segment] = []
    heading_path: list[tuple[int, str]] = []
    table_number = 0
    min_len = int(MIN_SEGMENT_LENGTH)

    def emit(text: str, kind: str, local_context: list[str]) -> None:
        context = [t for _, t in heading_path] + local_context
        segments.append(
            Segment(
                order_index=len(segments),
                segment_type=kind,
                text=text,
                heading_context=" > ".join(context) if context and kind != "heading" else None,
            )
        )

    def walk(node: Tag, local_context: list[str]) -> str | None:
        nonlocal heading_path, table_number
        name = node.name or ""
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = _clean_text(node.get_text(" ", strip=True))
            if text:
                level = int(name[1])
                heading_path = [(lvl, t) for lvl, t in heading_path if lvl < level]
                heading_path.append((level, text))
                emit(text, "heading", [])
            return None
        if name == "table":
            table_number += 1
            if _clean_text(node.get_text(" ", strip=True)):
                emit(_table_text(node, table_number), "table", local_context)
            return None

        parts: list[str] = []
        has_nonlink_text = False
        emitted: list[str] = []

        def flush() -> str | None:
            nonlocal has_nonlink_text
            text = _clean_text(" ".join(parts))
            # A numeric menu link is not a short factual field. Long legacy p/li
            # links remain possible evidence; new bare link containers are not.
            link_only = not has_nonlink_text
            keep = bool(text) and (
                len(text) >= min_len
                or (not link_only and _short_field(text, bool(heading_path or local_context)))
            )
            if link_only and name not in {"p", "li"}:
                keep = False
            parts.clear()
            has_nonlink_text = False
            if not keep:
                return None
            emit(text, "list_item" if name == "li" else "paragraph", local_context)
            emitted.append(text)
            return text if len(text) < min_len else None

        lead = None
        for event in _content_events(node):
            if isinstance(event, Tag):
                if any(_clean_text(part) for part in parts):
                    lead = flush()
                else:
                    parts.clear()
                # A short introductory field immediately before a list stays
                # attached to that list only (e.g. its data year), not the page.
                child_context = local_context + (
                    [lead] if lead and event.name in {"ul", "ol"} else []
                )
                child_lead = walk(event, child_context)
                if event.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                    local_context = []
                lead = child_lead if event.name == "p" else None
            else:
                parts.append(str(event))
                if isinstance(event, NavigableString) and _clean_text(str(event)):
                    has_nonlink_text |= event.find_parent("a") is None
        flush()
        return emitted[0] if len(emitted) == 1 and len(emitted[0]) < min_len else None

    walk(soup.body or soup, [])
    return segments


def parse_html(fetch_result: FetchResult, base_url: str) -> ParseResult:
    body = fetch_result.body or b""
    if not body:
        return ParseResult(text="", segments=[], out_links=[], meta={})

    try:
        soup = BeautifulSoup(body, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(body, "html.parser")
        except Exception:
            return ParseResult(text="", segments=[], out_links=[], meta={})

    # Discard non-content elements before discovery, but retain navigation links.
    for tag in soup.find_all(["script", "style", "noscript", "svg"]):
        tag.decompose()

    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        abs_url = urljoin(base_url, href)

        abs_url = abs_url.split("#")[0]

        if not abs_url.startswith(("http://", "https://")):
            continue

        if _RE_ASSET.search(abs_url):
            continue

        if abs_url in seen:
            continue
        seen.add(abs_url)

        if "//" in abs_url[8:]:  # 8: skip "https://"
            try:
                p = urlsplit(abs_url)
                if p.path and "//" in p.path:
                    abs_url = abs_url.replace(p.path, _RE_MULTI_SLASH.sub("/", p.path), 1)
            except Exception:
                pass

        anchor = _clean_text(a.get_text(" ", strip=True))
        links.append((abs_url, anchor))

    for tag in soup.find_all(["nav", "footer", "aside"]):
        tag.decompose()

    segments = _content_segments(soup)

    title_tag = soup.find("title")
    title = _clean_text(title_tag.get_text()) if title_tag else None

    full_text = _clean_text(soup.get_text(" ", strip=True))

    meta = {}
    if title:
        meta["title"] = title
    if fetch_result.content_type:
        meta["content_type"] = fetch_result.content_type

    # Extract structured metadata from <meta> tags — free enrichment for GraphRAG
    _meta_map = {
        "description": "description",
        "og:description": "description",
        "og:title": "og_title",
        "article:published_time": "date",
        "date": "date",
        "dcterms.date": "date",
        "dcterms.modified": "date_modified",
        "author": "author",
        "dcterms.creator": "author",
        "keywords": "keywords",
        "og:type": "og_type",
    }
    for meta_tag in soup.find_all("meta"):
        attr_name = (meta_tag.get("name") or meta_tag.get("property") or "").lower().strip()
        content = (meta_tag.get("content") or "").strip()
        if not attr_name or not content:
            continue
        mapped = _meta_map.get(attr_name)
        if mapped:
            meta.setdefault(mapped, content)

    return ParseResult(
        text=full_text,
        segments=segments,
        out_links=links,
        meta=meta,
    )
