# ruff: noqa: E501
"""Shared passive original presentation; no annotation or research-state dependencies."""

from bs4 import BeautifulSoup


def sanitized_html(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for n in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "object",
            "embed",
            "form",
            "input",
            "button",
            "link",
            "meta",
            "base",
            "svg",
            "img",
            "audio",
            "video",
        ]
    ):
        n.decompose()
    for n in soup.find_all(True):
        n.attrs = {k: v for k, v in n.attrs.items() if k in ["colspan", "rowspan"]}
    return (
        '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Archiviertes Original – passive Ansicht</title><style>body{font:18px/1.6 Georgia;max-width:950px;margin:40px auto;padding:20px}td,th{border:1px solid #aaa;padding:8px}table{border-collapse:collapse}a{color:inherit}</style><h1>Archiviertes Original · passive Ansicht</h1><p>Originalstruktur, ohne aktive Skripte oder externe Inhalte. Byteidentisches Archiv separat im Paket.</p>'
        + str(soup.body or soup)
    )
