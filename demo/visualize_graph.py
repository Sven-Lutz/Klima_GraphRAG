"""
visualize_graph.py  —  Interactive HTML graph from graph_triplets.

Opens a force-directed graph in your browser. Nodes are colour-coded by
entity type; edges show the relationship type on hover.

Requirements:
    pip install pyvis

Usage:
    python demo/visualize_graph.py                       # top-100 entities
    python demo/visualize_graph.py --top 200 --muni 09161000
    python demo/visualize_graph.py --top 50 --type Förderprogramm
    python demo/visualize_graph.py --out my_graph.html   # custom output path
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Optional

from pyvis.network import Network

DEMO_DB = Path("demo/demo.sqlite")

# ── colours per entity type ───────────────────────────────────────────────────
TYPE_COLOUR = {
    "Akteur":          "#4A90D9",   # blue
    "Infrastruktur":   "#27AE60",   # green
    "Förderprogramm":  "#F39C12",   # orange
    "Maßnahme":        "#8E44AD",   # purple
    "Konzept/Ziel":    "#E74C3C",   # red
    "Dokument":        "#95A5A6",   # grey
}
DEFAULT_COLOUR = "#BDC3C7"

MUNI_LABEL = {
    "09161000": "Ingolstadt",
    "09362000": "Regensburg",
    "09761000": "Augsburg",
}

REL_COLOUR = {
    "FÖRDERT":           "#F39C12",
    "BAUT":              "#27AE60",
    "BESCHLIESST":       "#E74C3C",
    "IMPLEMENTIERT":     "#8E44AD",
    "PLANT":             "#3498DB",
    "GEHÖRT_ZU":         "#95A5A6",
    "KOOPERIERT_MIT":    "#1ABC9C",
    "FINANZIERT_DURCH":  "#E67E22",
    "BEZIEHT_SICH_AUF":  "#BDC3C7",
}


# ── noise filter ─────────────────────────────────────────────────────────────

_NOISE_TOKENS = frozenset({
    "aktuelles", "mehr", "hier", "startseite", "suche", "navigation",
    "home", "weiter", "zurück", "seite", "inhalt", "springe", "menü",
    "imhofstr", "hermanstraße", "altstadt", "donau", "www", "http",
    "stadt",   # too generic on its own
})

_MIN_NAME_LEN = 5
_MAX_NAME_LEN = 80


def _is_noise(name: str) -> bool:
    if not (_MIN_NAME_LEN <= len(name) <= _MAX_NAME_LEN):
        return True
    if name.lower() in _NOISE_TOKENS:
        return True
    # Navigation fragments: contain multiple ">" or "|"
    if name.count(">") > 1 or name.count("|") > 1:
        return True
    return False


# ── load graph ────────────────────────────────────────────────────────────────

def load_graph(
    db: Path,
    top_n: int,
    muni_filter: Optional[str],
    type_filter: Optional[str],
) -> tuple[dict[str, dict], list[dict]]:
    """
    Returns (nodes_dict, edges_list).
    nodes_dict: {name: {type, category, count, muni_set}}
    edges_list: [{src, rel, tgt, evidence}]
    """
    con = sqlite3.connect(str(db))

    q = "SELECT municipality_id, graph_json FROM graph_triplets"
    params: list = []
    if muni_filter:
        q += " WHERE municipality_id = ?"
        params.append(muni_filter)

    rows = con.execute(q, params).fetchall()
    con.close()

    entity_counter: Counter = Counter()
    node_meta:  dict[str, dict] = {}
    raw_edges:  list[dict]      = []

    for muni_id, graph_json in rows:
        try:
            kg = json.loads(graph_json)
        except json.JSONDecodeError:
            continue

        for ent in kg.get("entities", []):
            name = ent.get("name", "").strip()
            if not name or _is_noise(name):
                continue
            if type_filter and ent.get("type") != type_filter:
                continue
            entity_counter[name] += 1
            if name not in node_meta:
                node_meta[name] = {
                    "type":     ent.get("type", "Akteur"),
                    "category": ent.get("category", "Sonstiges"),
                    "munis":    set(),
                }
            node_meta[name]["munis"].add(muni_id)

        for rel in kg.get("relationships", []):
            src = rel.get("source_entity", "").strip()
            tgt = rel.get("target_entity", "").strip()
            if src and tgt and not _is_noise(src) and not _is_noise(tgt):
                raw_edges.append({
                    "src":      src,
                    "rel":      rel.get("relation_type", "?"),
                    "tgt":      tgt,
                    "evidence": rel.get("evidence", ""),
                    "muni":     muni_id,
                })

    # Keep only top-N entities by frequency
    top_names = {name for name, _ in entity_counter.most_common(top_n)}

    nodes = {
        name: {**meta, "count": entity_counter[name]}
        for name, meta in node_meta.items()
        if name in top_names
    }

    # Keep only edges where both endpoints are in top_names
    edges = [
        e for e in raw_edges
        if e["src"] in top_names and e["tgt"] in top_names
    ]

    return nodes, edges


# ── build pyvis network ───────────────────────────────────────────────────────

def build_network(
    nodes: dict[str, dict],
    edges: list[dict],
    title: str,
) -> Network:
    net = Network(
        height="900px",
        width="100%",
        bgcolor="#1a1a2e",
        font_color="#e0e0e0",
        directed=True,
        notebook=False,
    )
    net.barnes_hut(
        gravity=-8000,
        central_gravity=0.3,
        spring_length=120,
        spring_strength=0.05,
        damping=0.09,
    )

    # Add nodes
    for name, meta in nodes.items():
        colour = TYPE_COLOUR.get(meta["type"], DEFAULT_COLOUR)
        muni_names = ", ".join(MUNI_LABEL.get(m, m) for m in sorted(meta["munis"]))
        size = max(10, min(40, 10 + meta["count"] * 2))
        tooltip = (
            f"<b>{name}</b><br>"
            f"Typ: {meta['type']}<br>"
            f"Kategorie: {meta['category']}<br>"
            f"Kommunen: {muni_names}<br>"
            f"Häufigkeit: {meta['count']}"
        )
        net.add_node(
            name,
            label=name if len(name) <= 30 else name[:28] + "…",
            title=tooltip,
            color=colour,
            size=size,
            borderWidth=2,
            borderWidthSelected=4,
        )

    # Add edges (deduplicate by src+rel+tgt)
    seen_edges: set[tuple] = set()
    for e in edges:
        key = (e["src"], e["rel"], e["tgt"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        colour = REL_COLOUR.get(e["rel"], "#666666")
        tooltip = f"<b>{e['rel']}</b><br>{e['evidence'][:200]}" if e["evidence"] else e["rel"]
        net.add_edge(
            e["src"], e["tgt"],
            title=tooltip,
            label=e["rel"],
            color={"color": colour, "opacity": 0.7},
            arrows="to",
            font={"size": 9, "color": "#aaaaaa"},
            smooth={"type": "curvedCW", "roundness": 0.1},
        )

    # Legend as HTML in the page title area
    legend_items = "".join(
        f'<span style="background:{c};padding:2px 8px;border-radius:3px;margin:2px;display:inline-block">'
        f'{t}</span>'
        for t, c in TYPE_COLOUR.items()
    )
    net.set_options("""
    {
      "interaction": {
        "hover": true,
        "navigationButtons": true,
        "keyboard": { "enabled": true }
      },
      "physics": {
        "enabled": true,
        "stabilization": { "iterations": 200 }
      }
    }
    """)

    return net


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Visualise KlimaCrawler graph as interactive HTML")
    ap.add_argument("--db",    default=str(DEMO_DB))
    ap.add_argument("--top",   type=int, default=100,
                    help="Max entities to show (by frequency, default: 100)")
    ap.add_argument("--muni",  default=None,
                    help="Filter by municipality_id, e.g. 09161000 (Ingolstadt)")
    ap.add_argument("--type",  default=None,
                    help="Filter by entity type, e.g. Förderprogramm")
    ap.add_argument("--out",   default="demo/graph.html",
                    help="Output HTML file (default: demo/graph.html)")
    ap.add_argument("--no-open", action="store_true",
                    help="Don't automatically open the browser")
    args = ap.parse_args()

    db = Path(args.db)
    out = Path(args.out)

    print(f"Lade Graph aus {db} …")
    nodes, edges = load_graph(
        db,
        top_n=args.top,
        muni_filter=args.muni,
        type_filter=args.type,
    )

    if not nodes:
        print("[!] Keine Entitäten gefunden. Bitte zuerst extract_graph_spacy.py ausführen.")
        return

    muni_label = MUNI_LABEL.get(args.muni, args.muni) if args.muni else "alle Kommunen"
    type_label  = args.type or "alle Typen"
    title = f"KlimaCrawler — {muni_label} · {type_label} · Top-{args.top}"

    print(f"  Knoten:   {len(nodes)}")
    print(f"  Kanten:   {len(set((e['src'],e['rel'],e['tgt']) for e in edges))}")

    net = build_network(nodes, edges, title)

    out.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(out))

    print(f"\n  ✓ Graph gespeichert: {out.resolve()}")

    # Prepend a legend banner to the HTML
    html = out.read_text(encoding="utf-8")
    legend = (
        '<div style="position:fixed;top:8px;left:8px;z-index:9999;'
        'background:rgba(26,26,46,0.92);padding:8px 12px;border-radius:6px;'
        'font-family:sans-serif;font-size:12px;color:#eee">'
        f'<b>{title}</b><br><br>'
        + "".join(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:{c};border-radius:2px;margin-right:4px;vertical-align:middle"></span>'
            f'{t}&nbsp;&nbsp;'
            for t, c in TYPE_COLOUR.items()
        )
        + "<br><br>"
        + "".join(
            f'<span style="color:{c}">━</span> {r}&nbsp;&nbsp;'
            for r, c in list(REL_COLOUR.items())[:5]
        )
        + "</div>"
    )
    out.write_text(html.replace("<body>", f"<body>\n{legend}", 1), encoding="utf-8")

    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
        print("  Browser wird geöffnet …")
    else:
        print(f"  Öffnen mit:  open {out}")


if __name__ == "__main__":
    main()
