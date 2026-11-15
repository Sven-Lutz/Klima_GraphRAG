# ruff: noqa: E501
"""Deterministic full-reader/guide book build; no crawl, research run, or inference.
Run with a local Python containing markdown, matplotlib, PyMuPDF and Playwright.
"""

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import markdown
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pymupdf
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "docs/research-knowledge-base"
E = html.escape
CSS = """
@page{size:A4}*{box-sizing:border-box}body{font-family:Georgia,'Times New Roman',serif;font-size:10.8pt;line-height:1.49;color:#202f40;margin:0}h1,h2,h3,h4,nav,caption,figcaption,.eyebrow{font-family:Arial,sans-serif}h1{font-size:25pt;line-height:1.18;letter-spacing:-.4pt;color:#163d4f;margin:0 0 9mm}h2{font-size:14pt;margin:7mm 0 3mm;color:#163d4f;break-after:avoid}h3{font-size:11.5pt;break-after:avoid}p{margin:0 0 3mm;orphans:3;widows:3}a{color:#205e75;text-decoration:none}code{font:8.1pt/1.4 monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef3f5;border-left:2pt solid #2f7b8c;padding:4mm;font-size:8.3pt}table{border-collapse:collapse;width:100%;table-layout:fixed;font-family:Arial,sans-serif;font-size:8.1pt;line-height:1.35;margin:4mm 0 5mm}thead{display:table-header-group}th{background:#163d4f;color:white;text-align:left}th,td{padding:2.4mm;border-bottom:.4pt solid #cdd8dc;vertical-align:top;overflow-wrap:anywhere}tr{break-inside:avoid}tr:nth-child(even) td{background:#f0f5f6}section.chapter{break-before:page}section.chapter:first-child{break-before:auto}figure{margin:5mm 0;break-inside:avoid}figure svg,figure img{width:100%;height:auto}figcaption{font-size:8pt;line-height:1.4;color:#415967;margin:2mm 0}li{margin:1mm 0}ul,ol{padding-left:6mm}.eyebrow{font-size:9pt;letter-spacing:1.4pt;text-transform:uppercase;color:#32788a;margin-bottom:5mm}.notice{padding:4mm;border-left:3pt solid #bb943f;background:#faf7ed;margin:4mm 0}.toc{break-after:page}.toc-row{display:flex;gap:4mm;border-bottom:.4pt solid #cdd8dc;padding:2.1mm 0;font:10pt/1.4 Arial}.toc-row a{flex:1}.toc-row span{width:10mm;text-align:right}.cover{height:248mm;display:flex;flex-direction:column;justify-content:space-between;border-top:5pt solid #163d4f;padding-top:14mm}.cover h1{font-size:35pt;line-height:1.13}.cover .subtitle{font:15pt/1.5 Arial;color:#32788a}.cover .meta{font:10pt/1.6 Arial}.math-display{margin:4mm 0;overflow:visible;text-align:center;font-size:10pt}mjx-container{max-width:100%}mjx-container[jax="SVG"]>svg{max-width:100%;height:auto}.source-ref{font:8pt Arial;color:#526b75}.registry{font-size:8.4pt;overflow-wrap:anywhere}.chapter-number{font:9pt Arial;letter-spacing:1pt;text-transform:uppercase;color:#32788a;margin-bottom:4mm}blockquote{margin:4mm 0;padding:3mm 5mm;border-left:2pt solid #32788a;background:#eff5f7}
"""


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def diagram(code, number):
    code = code.replace("[*]", "START")
    nodes = dict(re.findall(r"([\w]+)\[([^\]]+)\]", code))
    edges = []
    for line in code.splitlines():
        line = re.sub(r"\[([^\]]+)\]", "", line)
        m = re.search(r"([\w*]+)\s*(?:-->|-\..*?\.->)\s*([\w]+)", line)
        if m:
            edges.append(m.groups())
    for a, b in edges:
        for n in (a, b):
            nodes.setdefault(n, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n).replace("*", "Start"))
    rank = {n: 0 for n in nodes}
    for _ in range(len(nodes)):
        for a, b in edges:
            rank[b] = max(rank[b], min(len(nodes), rank[a] + 1))
    depth = max(rank.values(), default=0)
    horizontal = "LR" in code and depth < 7
    groups = {r: [n for n in nodes if rank[n] == r] for r in set(rank.values())}
    columns = max((len(v) for v in groups.values()), default=1)
    width = 820 if horizontal else max(680, columns * 320 + 20)
    cellw = width / max(1, depth + 1) if horizontal else 320
    positions = {}
    for r in sorted(groups):
        for j, n in enumerate(groups[r]):
            positions[n] = (
                (r * cellw + 8, 22 + j * 110) if horizontal else (12 + j * 320, 12 + r * 110)
            )
    height = max(135, columns * 110 + 25) if horizontal else (depth + 1) * 110 + 25
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img"><title>Architekturdiagramm {number}</title><defs><marker id="arrow{number}" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#527988"/></marker></defs>'
    ]
    bw = cellw - 17 if horizontal else 300
    bh = 90
    font_size = 14 if horizontal else 18
    for a, b in edges:
        x, y = positions[a]
        xx, yy = positions[b]
        if horizontal:
            x1, y1, x2, y2 = x + bw, y + bh / 2, xx, yy + bh / 2
        else:
            x1, y1, x2, y2 = x + bw / 2, y + bh, xx + bw / 2, yy
        if not horizontal and rank[b] - rank[a] > 1:
            route = f"M{x1},{y1} L{width - 5},{y1 + 10} L{width - 5},{y2 - 10} L{x2},{y2 - 10} L{x2},{y2}"
            parts.append(
                f'<path d="{route}" fill="none" stroke="#527988" stroke-width="1.7" marker-end="url(#arrow{number})"/>'
            )
        else:
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#527988" stroke-width="1.7" marker-end="url(#arrow{number})"/>'
            )
    for n, (x, y) in positions.items():
        assert x + bw <= width and y + bh <= height, "Diagram would clip a node"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="6" fill="#eff5f6" stroke="#32788a"/>'
        )
        lines = textwrap.wrap(nodes[n], width=max(10, int(bw / (font_size * 0.55))))
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{x + bw / 2}" y="{y + 21 + j * 20}" text-anchor="middle" font-family="Arial" font-size="{font_size}" fill="#163d4f">{E(line)}</text>'
            )
    return (
        "<figure>"
        + "".join(parts)
        + "</svg><figcaption>Abbildung "
        + str(number)
        + ": Datenfluss bzw. Zustandsübergänge aus dem Kapitel. Gestrichelte bzw. beschriftete Erweiterungen im Ausgangsdiagramm sind methodische Möglichkeiten; ihre Implementierung folgt dem Kapiteltext.</figcaption></figure>"
    )


def charts(out):
    rel = ROOT / "research/bavaria/completion/release"
    report = json.loads((rel / "retrieval03/report.json").read_text())[
        "summary_evaluation_provisional_positive"
    ]
    methods = [
        "bm25_context",
        "semantic",
        "semantic_windows",
        "hybrid_rrf",
        "hybrid_windows_rrf",
        "bm25_same_relation_information",
        "sql_relations",
        "graph_traversal",
    ]
    available = list(report)
    methods = [
        m
        if m in report
        else next(
            (
                k
                for k in available
                if ("prefix" in k if m == "semantic" else "window" in k and "hybrid" not in k)
            ),
            m,
        )
        for m in methods
    ]
    methods = [m for m in dict.fromkeys(methods) if m in report]
    vals = [report[m]["complete_known_bundles"] for m in methods]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.barh(methods[::-1], vals[::-1], color=["#32788a"] * len(vals))
    ax.set_xlim(0, 30)
    ax.set_xlabel("Vollständige bekannte Belegbündel / 28")
    ax.grid(axis="x", alpha=0.2)
    ax.set_axisbelow(True)
    for i, v in enumerate(vals[::-1]):
        ax.text(v + 0.25, i, str(v), va="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "retrieval.svg")
    plt.close(fig)
    models = json.loads((rel / "MODEL_COMPARISON.json").read_text())["results"]
    rows = [models[i] for i in [0, 1, 2, 4, 5]]
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    names = ["Rules", "Qwen3 1.7B", "Hybrid 1.7B", "Qwen2.5 3B", "Hybrid 3B"]
    for ax, vs, title in [
        (axes[0], [r["f1"] for r in rows], "Striktes Field-F1"),
        (axes[1], [r["correct_unknown"] / 29 for r in rows], "UNKNOWN korrekt / 29"),
    ]:
        ax.barh(names[::-1], vs[::-1], color="#32788a")
        ax.set_xlim(0, 1.06)
        ax.set_title(title)
        ax.grid(axis="x", alpha=0.2)
        ax.set_axisbelow(True)
        for i, v in enumerate(vs[::-1]):
            ax.text(v + 0.015, i, f"{v:.3f}", va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "ie.svg")
    plt.close(fig)
    return [rel / "retrieval03/report.json", rel / "MODEL_COMPARISON.json"]


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    assets = out / "script_assets"
    assets.mkdir(exist_ok=True)
    mathjax = Path.home() / ".cache/exam-mastery-v05-node/node_modules/mathjax-full/es5/tex-svg.js"
    if not mathjax.exists():
        raise ValueError("Local MathJax bundle required; no network fallback")
    shutil.copy2(mathjax, assets / "tex-svg.js")
    prim = charts(assets)
    paths = (
        [KB / "README.md"]
        + sorted((KB / "chapters").glob("*.md"))
        + [
            KB / "VERTIEFUNG.md",
            KB / "ARCHITEKTUR.md",
            KB / "TRACEABILITY.md",
            KB / "GLOSSARY.md",
            KB / "QUELLEN.md",
            KB / "SUMMARY.md",
        ]
    )
    sources: dict[str, str] = {}
    figures = 0
    tables = 0
    equations = 0
    sections = []
    allhead = []
    coverage = []
    for j, p in enumerate(paths):
        raw = p.read_text()
        heading = re.search(r"^# (.+)", raw, re.M)
        if heading is None:
            raise ValueError("Missing document heading: " + str(p))
        title = heading.group(1)
        sid = "section-" + str(j)
        allhead.append((sid, title))
        raw = re.sub(r"^\[Übersicht\].*\n", "", raw, flags=re.M)
        raw = re.sub(r"^# .+\n", "", raw, count=1)
        math = []

        def keep_math(m):
            math.append(m.group(0))
            return "MATHPLACEHOLDER" + str(len(math) - 1) + "END"

        raw = re.sub(r"\$\$[\s\S]*?\$\$|(?<!\\)\$[^\n$]+\$", keep_math, raw)
        graphs = []

        def flow(m):
            nonlocal figures
            figures += 1
            graphs.append(diagram(m.group(1), figures))
            return "GRAPHPLACEHOLDER" + str(len(graphs) - 1) + "END"

        raw = re.sub(r"```mermaid\n([\s\S]*?)```", flow, raw)

        def link(m):
            label, target = m.groups()
            if target.startswith(("https://", "http://", "#")):
                return m.group(0)
            target = target.split("#")[0]
            local = (p.parent / target).resolve()
            if local in paths:
                return "[" + label + "](#section-" + str(paths.index(local)) + ")"
            if str(local) not in sources:
                sources[str(local)] = "P" + str(len(sources) + 1)
            return label + ' <span class="source-ref">[' + sources[str(local)] + "]</span>"

        raw = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, raw)
        body = markdown.markdown(raw, extensions=["tables", "fenced_code", "sane_lists"])
        for i, v in enumerate(math):
            display = v.startswith("$$")
            tex = v[2:-2] if display else v[1:-1]
            tag = "div" if display else "span"
            body = body.replace(
                "MATHPLACEHOLDER" + str(i) + "END",
                f'<{tag} class="math-display"'
                + ("" if display else ' style="display:inline"')
                + ">"
                + ("\\[" if display else "\\(")
                + E(tex)
                + ("\\]" if display else "\\)")
                + f"</{tag}>",
            )
        for i, g in enumerate(graphs):
            body = body.replace("<p>GRAPHPLACEHOLDER" + str(i) + "END</p>", g)
        equations += len(math)
        tables += body.count("<table>")
        coverage.append(
            {
                "file": str(p.relative_to(ROOT)),
                "sha256": sha(p),
                "words": len(p.read_text().split()),
                "tables": body.count("<table>"),
                "diagrams": len(graphs),
                "formulas": len(math),
            }
        )
        sections.append(
            f'<section class="chapter" id="{sid}"><p class="chapter-number">'
            + (
                "Kapitel " + p.name[:2]
                if p.parent.name == "chapters"
                else "Einleitung / Register / Vertiefung"
            )
            + f"</p><h1>{E(title)}</h1>"
            + body
            + "</section>"
        )
        if p.name == "12-experimente.md":
            sections.append(
                '<figure><img src="script_assets/retrieval.svg"><figcaption>Abbildung '
                + str(figures + 1)
                + ': Tatsächliche vollständige bekannte Belegbündel auf 28 positiven exponierten Aufgaben; keine erschöpfenden Human-Qrels. SQL/Graph erhalten kuratierte Relationen und formale Queries. Kein isolierter Formatvorteil.</figcaption></figure><figure><img src="script_assets/ie.svg"><figcaption>Abbildung '
                + str(figures + 2)
                + ": Identische acht Fälle, 27 positive und 29 unbekannte Slots. Strikte vorläufige Referenzen; kein Modell besteht das Qualifikationsgate.</figcaption></figure>"
            )
            figures += 2
    register = '<section class="chapter" id="project-register"><h1>Projektbelegregister und Reproduktionsanker</h1><p>Die P-Referenzen bezeichnen lokale Projektartefakte, keine öffentlich freigegebenen Originale. Pfade relativ zum Repository. Hashes beziehen sich auf den bei der PDF-Produktion gelesenen Stand; historische Verzeichnisse bleiben unverändert.</p><div class="registry">'
    for path, pid in sources.items():
        p = Path(path)
        name = str(p.relative_to(ROOT)) if ROOT in p.parents else str(p)
        register += (
            f"<p><strong>[{pid}]</strong> {E(name)}"
            + (
                "<br>SHA-256: <code>" + sha(p) + "</code>"
                if p.is_file()
                else "<br>Verzeichnis bzw. derzeit nicht vorhandener Pfad; keine Inhaltsprüfung behauptet."
            )
            + "</p>"
        )
    register += "</div></section>"
    sections.append(register)
    allhead.append(("project-register", "Projektbelegregister und Reproduktionsanker"))
    return (
        out,
        assets,
        paths,
        prim,
        allhead,
        sections,
        {
            "source_coverage": coverage,
            "formulas": equations,
            "tables": tables,
            "figures": figures,
            "headings": allhead,
        },
    )


def page(body, title):
    return (
        '<!doctype html><html lang="de"><meta charset="utf-8"><title>'
        + E(title)
        + "</title><style>"
        + CSS
        + '</style><script>window.MathJax={tex:{inlineMath:[["\\\\(","\\\\)"]],displayMath:[["\\\\[","\\\\]"]]},svg:{fontCache:"local"}};</script><script defer src="script_assets/tex-svg.js"></script><body>'
        + body
        + "</body></html>"
    )


def render_pdf(out, assets, heads, sections, title, name, meta, subtitle):
    cover = (
        '<section class="cover"><div><p class="eyebrow">Klima_GraphRAG · Forschungsedition</p><h1>'
        + E(title)
        + '</h1><p class="subtitle">'
        + E(subtitle)
        + '</p></div><div class="notice">Wissenschaftlicher Fachentwurf<br>Explorative Ergebnisse; unabhängige menschliche Validierung ausstehend.</div><div class="meta">Projektverantwortung: Sven Lutz<br>Stand: 9. November 2026<br>Lokaler Referenzcommit: '
        + subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT)
        .decode()
        .strip()
        + "<br>Vollständige Kapitel, Originalprovenienz und transparente Ergebnisgrenzen.</div></section>"
    )
    cov = out / (name + ".cover.html")
    cov.write_text(page(cover, title))
    htmlpath = out / (name + ".html")
    body = "".join(sections)
    dest = out / (name + ".pdf")
    tocpages: dict[str, str | int] = {sid: "–" for sid, _ in heads}
    footer = '<div style="font:8px Arial;width:100%;margin:0 20mm;color:#526b75;display:flex;justify-content:space-between"><span>Klima_GraphRAG · wissenschaftlicher Fachentwurf</span><span class="pageNumber"></span></div>'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()

        def print_page(path, pdf, foot=False):
            pg.goto(path.as_uri())
            pg.wait_for_function("window.MathJax && MathJax.startup && MathJax.startup.promise")
            pg.evaluate("MathJax.startup.promise")
            pg.evaluate("document.fonts.ready")
            pg.pdf(
                path=str(pdf),
                format="A4",
                print_background=True,
                display_header_footer=foot,
                header_template="<span></span>",
                footer_template=footer,
                margin={"top": "19mm", "bottom": "20mm", "left": "21mm", "right": "21mm"},
                prefer_css_page_size=True,
            )

        print_page(cov, out / (name + ".cover.pdf"))
        for iteration in range(3):
            toc = (
                '<nav class="toc"><p class="eyebrow">Lesepfad und Navigation</p><h1>Inhaltsverzeichnis</h1>'
                + "".join(
                    f'<div class="toc-row"><a href="#{sid}">{E(t)}</a><span>{tocpages[sid]}</span></div>'
                    for sid, t in heads
                )
                + "</nav>"
            )
            if len(heads) == 1:
                toc = ""
            htmlpath.write_text(page(toc + body, title))
            print_page(htmlpath, out / (name + ".body.pdf"), True)
            doc = pymupdf.open(out / (name + ".body.pdf"))
            nexttoc: dict[str, str | int] = {}
            bookmarks = []

            def normalize(s):
                return re.sub(r"[^\w]", "", s).casefold()

            for sid, t in heads:
                # Main content starts only after the TOC, and every chapter starts a page.
                matches = [
                    i
                    for i, pg_ in enumerate(doc)
                    if normalize(t) in normalize(pg_.get_text())
                    and "Lesepfad und Navigation" not in pg_.get_text()
                    and "Inhaltsverzeichnis" not in pg_.get_text()[:150]
                ]
                if not matches:
                    raise ValueError("Missing PDF chapter heading " + t)
                i = matches[-1]
                nexttoc[sid] = i + 1
                bookmarks.append([1, t, i + 2])
            if nexttoc == tocpages:
                break
            tocpages = nexttoc
        final = pymupdf.open(out / (name + ".cover.pdf"))
        final.insert_pdf(doc)
        final.set_toc(bookmarks)
        final.set_metadata(
            {
                "title": title,
                "author": "Sven Lutz",
                "subject": subtitle,
                "keywords": "Climate Policy, GraphRAG, Research Gold, PROVISIONAL",
            }
        )
        final.save(dest, deflate=True)
        errors = pg.evaluate(
            'Array.from(document.querySelectorAll("mjx-merror")).map(x=>x.textContent)'
        )
        overflow = pg.evaluate(
            'Array.from(document.querySelectorAll("table,figure,mjx-container")).filter(x=>x.getBoundingClientRect().width>document.body.getBoundingClientRect().width+3).map(x=>x.tagName)'
        )
        browser.close()
    meta.update(
        pdf=str(dest.relative_to(ROOT)),
        pages=len(final),
        bookmarks=len(bookmarks),
        math_errors=errors,
        horizontal_overflow=overflow,
        toc_pages=tocpages,
        pdf_sha256=sha(dest),
    )
    if errors or overflow:
        raise ValueError("Formula or layout overflow: " + str(errors) + str(overflow))
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "deliverables")
    a = ap.parse_args()
    out, assets, paths, prim, heads, sections, meta = build(a.out.resolve())
    report = render_pdf(
        out,
        assets,
        heads,
        sections,
        "Kommunale Klimapolitik verstehen und evidenzbasiert untersuchen",
        "Forschungsskript_Klima_GraphRAG",
        meta,
        "Forschungsdesign · Daten · Information Extraction · SQL und Graphen · Retrieval · wissenschaftliche Validierung",
    )
    guide = ROOT / "research/gold_studio/PORTABLE_REVIEWER_GUIDE.md"
    text = guide.read_text()
    title = "Reviewer-Anleitung"
    content = markdown.markdown(
        re.sub(r"^# .+\n", "", text, count=1), extensions=["tables", "fenced_code"]
    )
    g = render_pdf(
        out,
        assets,
        [("guide", "Portable Kalibrierung: Schritt für Schritt")],
        [
            '<section class="chapter" id="guide"><h1>Portable Kalibrierung: Schritt für Schritt</h1>'
            + content
            + "</section>"
        ],
        title,
        "Reviewer_Anleitung",
        {},
        "Original-first · Offline arbeiten · Backup und Rückgabe",
    )
    report["guide"] = g
    report["input_hashes"] = {str(p.relative_to(ROOT)): sha(p) for p in paths + prim + [guide]}
    report["status"] = "RENDERED_PENDING_VISUAL_QA"
    (out / "DOCUMENT_BUILD.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "reader_pages": report["pages"],
                "guide_pages": g["pages"],
                "chapters": 13,
                "formulas": report["formulas"],
                "tables": report["tables"],
                "figures": report["figures"],
            }
        )
    )


if __name__ == "__main__":
    main()
