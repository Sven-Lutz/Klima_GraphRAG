"""
visualize_graph.py  —  Interactive HTML graph from graph_triplets.

Opens a force-directed graph in your browser. Only nodes that participate
in at least one relationship are shown — no isolated dots.

Requirements:
    pip install pyvis

Usage:
    python demo/visualize_graph.py                         # connected graph, top-60
    python demo/visualize_graph.py --top 120               # show more nodes
    python demo/visualize_graph.py --muni 09161000         # Ingolstadt only
    python demo/visualize_graph.py --type Förderprogramm   # one entity type
    python demo/visualize_graph.py --rel FÖRDERT           # one relation type
    python demo/visualize_graph.py --min-freq 3            # only frequent entities
    python demo/visualize_graph.py --out my_graph.html
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Optional

from pyvis.network import Network

DEMO_DB = Path("demo/demo.sqlite")

# ── colours ──────────────────────────────────────────────────────────────────
TYPE_COLOUR = {
    "Akteur":          "#4A90D9",
    "Infrastruktur":   "#27AE60",
    "Förderprogramm":  "#F39C12",
    "Maßnahme":        "#8E44AD",
    "Konzept/Ziel":    "#E74C3C",
    "Dokument":        "#95A5A6",
}
DEFAULT_COLOUR = "#BDC3C7"

MUNI_COLOUR = {
    "09161000": "#4A90D9",   # Ingolstadt  — blue border
    "09362000": "#27AE60",   # Regensburg  — green border
    "09761000": "#F39C12",   # Augsburg    — orange border
}

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
    "GEHÖRT_ZU":         "#7F8C8D",
    "KOOPERIERT_MIT":    "#1ABC9C",
    "FINANZIERT_DURCH":  "#E67E22",
    "BEZIEHT_SICH_AUF":  "#95A5A6",
}

# ── noise filter ─────────────────────────────────────────────────────────────
# Generic single nouns / navigation fragments that add no graph value
_NOISE_EXACT = frozenset({
    # UI / navigation
    "aktuelles", "mehr", "hier", "startseite", "suche", "navigation",
    "home", "weiter", "zurück", "seite", "inhalt", "springe", "menü",
    "kontakt", "impressum", "datenschutz", "sitemap", "login",
    # Too-generic domain words
    "stadt", "förderung", "anlage", "anlagen", "maßnahme", "maßnahmen",
    "ausbau", "vorhaben", "projekt", "projekte", "bereich", "bereich",
    "antrag", "information", "informationen", "hinweis", "hinweise",
    "service", "leistung", "leistungen", "themen", "inhalte",
    # Geography noise
    "donau", "altstadt", "innenstadt",
    # Web fragments
    "www", "http", "https",
})

_NOISE_PREFIXES = (
    "imhofstr", "hermanstr", "goethestr", "bahnhof",  # street addresses
)

_MIN_LEN = 5
_MAX_LEN = 70


def _is_noise(name: str) -> bool:
    if not (_MIN_LEN <= len(name) <= _MAX_LEN):
        return True
    nl = name.lower().strip()
    if nl in _NOISE_EXACT:
        return True
    if any(nl.startswith(p) for p in _NOISE_PREFIXES):
        return True
    # Navigation breadcrumbs / multiple separators
    if name.count(">") > 1 or name.count("|") > 1:
        return True
    # Looks like a URL fragment
    if nl.startswith("http") or "//" in name:
        return True
    return False


# ── load & filter ─────────────────────────────────────────────────────────────

def load_graph(
    db: Path,
    top_n: int,
    min_freq: int,
    muni_filter: Optional[str],
    type_filter: Optional[str],
    rel_filter: Optional[str],
) -> tuple[dict[str, dict], list[dict]]:
    con = sqlite3.connect(str(db))
    q = "SELECT municipality_id, graph_json FROM graph_triplets"
    params: list = []
    if muni_filter:
        q += " WHERE municipality_id = ?"
        params.append(muni_filter)
    rows = con.execute(q, params).fetchall()
    con.close()

    # --- pass 1: collect all entity metadata and raw edges ---
    entity_counter: Counter        = Counter()
    node_meta: dict[str, dict]     = {}
    raw_edges: list[dict]          = []

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
            rtype = rel.get("relation_type", "?")
            if rel_filter and rtype != rel_filter:
                continue
            src = rel.get("source_entity", "").strip()
            tgt = rel.get("target_entity", "").strip()
            if src and tgt and not _is_noise(src) and not _is_noise(tgt):
                raw_edges.append({
                    "src": src, "tgt": tgt, "rel": rtype,
                    "evidence": rel.get("evidence", ""),
                    "muni": muni_id,
                })

    # --- pass 2: keep only frequently-seen entities ---
    freq_names = {
        name for name, cnt in entity_counter.items()
        if cnt >= min_freq
    }

    # --- pass 3: take top-N by frequency from the freq-filtered set ---
    top_names = {
        name for name, _ in
        Counter({k: v for k, v in entity_counter.items() if k in freq_names})
        .most_common(top_n)
    }

    # --- pass 4: keep only edges where BOTH endpoints are in top_names ---
    # Deduplicate by (src, rel, tgt)
    seen: set[tuple] = set()
    clean_edges: list[dict] = []
    for e in raw_edges:
        if e["src"] not in top_names or e["tgt"] not in top_names:
            continue
        key = (e["src"], e["rel"], e["tgt"])
        if key in seen:
            continue
        seen.add(key)
        clean_edges.append(e)

    # --- pass 5: keep ONLY nodes that appear in at least one edge ---
    connected = {e["src"] for e in clean_edges} | {e["tgt"] for e in clean_edges}

    nodes = {
        name: {**meta, "count": entity_counter[name]}
        for name, meta in node_meta.items()
        if name in connected
    }

    return nodes, clean_edges


# ── build pyvis network ───────────────────────────────────────────────────────

def build_network(nodes: dict[str, dict], edges: list[dict], title: str) -> Network:
    net = Network(
        height="920px", width="100%",
        bgcolor="#16213e", font_color="#dde",
        directed=True, notebook=False,
    )
    net.barnes_hut(
        gravity=-12000,
        central_gravity=0.25,
        spring_length=160,
        spring_strength=0.04,
        damping=0.12,
        overlap=0,
    )

    for name, meta in nodes.items():
        fill   = TYPE_COLOUR.get(meta["type"], DEFAULT_COLOUR)
        # Border colour = first municipality the entity appears in
        munis  = sorted(meta["munis"])
        border = MUNI_COLOUR.get(munis[0], "#ffffff") if munis else "#ffffff"
        muni_names = ", ".join(MUNI_LABEL.get(m, m) for m in munis)
        size = max(12, min(45, 10 + meta["count"] * 3))

        label = name if len(name) <= 22 else name[:20] + "…"
        tooltip = (
            f"<b>{name}</b><br>"
            f"Typ: {meta['type']} · {meta['category']}<br>"
            f"Kommunen: {muni_names}<br>"
            f"Häufigkeit: {meta['count']}×"
        )
        net.add_node(
            name, label=label, title=tooltip,
            color={"background": fill, "border": border,
                   "highlight": {"background": fill, "border": "#ffffff"}},
            size=size,
            borderWidth=3, borderWidthSelected=5,
            font={"size": 11, "color": "#ffffff",
                  "strokeWidth": 3, "strokeColor": "#16213e"},
        )

    for e in edges:
        ec = REL_COLOUR.get(e["rel"], "#666")
        tip = f"<b>{e['rel']}</b>"
        if e["evidence"]:
            tip += f"<br><i>{e['evidence'][:220]}</i>"
        net.add_edge(
            e["src"], e["tgt"],
            title=tip,
            # No label on the edge itself — too cluttered; colour conveys type
            color={"color": ec, "opacity": 0.65,
                   "highlight": ec},
            arrows={"to": {"enabled": True, "scaleFactor": 0.6}},
            width=1.5,
            smooth={"type": "curvedCW", "roundness": 0.15},
        )

    net.set_options(json.dumps({
        "interaction": {
            "hover": True,
            "navigationButtons": True,
            "keyboard": {"enabled": True},
            "tooltipDelay": 150,
        },
        "physics": {
            "enabled": True,
            "stabilization": {"iterations": 300, "fit": True},
        },
        "edges": {
            "scaling": {"min": 1, "max": 3},
        },
    }))
    return net


# ── legend overlay ────────────────────────────────────────────────────────────

def _legend_html(title: str) -> str:
    node_items = "".join(
        f'<span style="display:inline-flex;align-items:center;margin:2px 6px 2px 0">'
        f'<span style="width:13px;height:13px;border-radius:50%;background:{c};'
        f'display:inline-block;margin-right:4px"></span>{t}</span>'
        for t, c in TYPE_COLOUR.items()
    )
    edge_items = "".join(
        f'<span style="color:{c};font-weight:bold;margin-right:10px">— {r}</span>'
        for r, c in REL_COLOUR.items()
    )
    muni_items = "".join(
        f'<span style="display:inline-flex;align-items:center;margin:2px 8px 2px 0">'
        f'<span style="width:13px;height:13px;border-radius:3px;background:{c};'
        f'border:2px solid {c};display:inline-block;margin-right:4px"></span>{lbl}</span>'
        for mid, c in MUNI_COLOUR.items()
        for lbl in [MUNI_LABEL.get(mid, mid)]
    )
    return (
        '<div style="position:fixed;top:8px;left:8px;z-index:9999;max-width:520px;'
        'background:rgba(16,20,40,0.93);padding:10px 14px;border-radius:8px;'
        'font-family:sans-serif;font-size:12px;color:#eee;line-height:1.7">'
        f'<div style="font-size:14px;font-weight:bold;margin-bottom:6px">{title}</div>'
        f'<div><b>Entitätstypen:</b> {node_items}</div>'
        f'<div style="margin-top:4px"><b>Knotenrahmen:</b> {muni_items}</div>'
        f'<div style="margin-top:4px"><b>Beziehungen (hover für Details):</b><br>{edge_items}</div>'
        '</div>'
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="KlimaCrawler — interaktiver Graph im Browser")
    ap.add_argument("--db",       default=str(DEMO_DB))
    ap.add_argument("--top",      type=int, default=60,
                    help="Max. Knoten (nach Häufigkeit, default: 60)")
    ap.add_argument("--min-freq", type=int, default=2,
                    help="Mindest-Häufigkeit einer Entität (default: 2)")
    ap.add_argument("--muni",     default=None,
                    help="Filter: AGS-Nummer, z.B. 09161000 (Ingolstadt)")
    ap.add_argument("--type",     default=None,
                    help="Filter: Entitätstyp, z.B. Förderprogramm")
    ap.add_argument("--rel",      default=None,
                    help="Filter: Beziehungstyp, z.B. FÖRDERT")
    ap.add_argument("--out",      default="demo/graph.html")
    ap.add_argument("--no-open",  action="store_true")
    args = ap.parse_args()

    db  = Path(args.db)
    out = Path(args.out)

    print(f"Lade Graph aus {db} …")
    nodes, edges = load_graph(
        db,
        top_n=args.top,
        min_freq=args.min_freq,
        muni_filter=args.muni,
        type_filter=args.type,
        rel_filter=args.rel,
    )

    if not nodes:
        print("[!] Keine verbundenen Entitäten gefunden.")
        print("    Tipp: --min-freq 1 oder --top 200 versuchen")
        return

    muni_label = MUNI_LABEL.get(args.muni, args.muni) if args.muni else "alle Kommunen"
    type_label = args.type or "alle Typen"
    title = (
        f"KlimaCrawler Graph — {muni_label} · {type_label}"
        f" · {len(nodes)} Knoten · {len(edges)} Kanten"
    )
    print(f"  Verbundene Knoten : {len(nodes)}")
    print(f"  Kanten            : {len(edges)}")

    net = build_network(nodes, edges, title)

    out.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(out))

    # Inject legend
    html = out.read_text(encoding="utf-8")
    out.write_text(
        html.replace("<body>", f"<body>\n{_legend_html(title)}", 1),
        encoding="utf-8",
    )

    print(f"\n  ✓ Gespeichert: {out.resolve()}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
        print("  Browser öffnet …")
    else:
        print(f"  Öffnen: open \"{out}\"")


if __name__ == "__main__":
    main()
