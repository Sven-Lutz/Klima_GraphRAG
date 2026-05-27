"""
Creates two OnePager PowerPoint files:
  1. docs/PreThesis_Todos.pptx  — team tasks before Bachelor's thesis starts
  2. docs/Bachelorarbeit_Ausschreibung.pptx — thesis announcement for professor
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm
import copy

# ── colour palette ────────────────────────────────────────────────────────────
C_DARK   = RGBColor(0x1A, 0x1A, 0x2E)   # dark navy
C_BLUE   = RGBColor(0x16, 0x21, 0x3E)   # section header bg
C_ACCENT = RGBColor(0x4A, 0x90, 0xD9)   # accent blue
C_GREEN  = RGBColor(0x27, 0xAE, 0x60)   # green accent
C_LIGHT  = RGBColor(0xF4, 0xF6, 0xF9)   # light bg
C_WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
C_GREY   = RGBColor(0x7F, 0x8C, 0x8D)
C_TEXT   = RGBColor(0x2C, 0x3E, 0x50)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)


def new_prs() -> Presentation:
    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def blank_slide(prs: Presentation):
    blank_layout = prs.slide_layouts[6]
    return prs.slides.add_slide(blank_layout)


def add_rect(slide, left, top, width, height, fill_rgb, alpha=None):
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        left, top, width, height
    )
    shape.line.fill.background()
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_rgb
    return shape


def add_textbox(slide, text, left, top, width, height,
                font_size=12, bold=False, color=C_TEXT,
                align=PP_ALIGN.LEFT, wrap=True):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    return txBox


def section_header(slide, text, top, accent=C_ACCENT):
    add_rect(slide, Cm(1.2), top, Cm(0.35), Cm(0.7), accent)
    add_textbox(slide, text,
                Cm(1.8), top - Cm(0.05), Cm(30), Cm(0.9),
                font_size=13, bold=True, color=C_DARK)


def bullet_lines(slide, items, left, top, width, line_height_cm=0.55, font_size=10.5):
    for i, (bullet, text) in enumerate(items):
        y = top + Cm(i * line_height_cm)
        add_textbox(slide, bullet, left, y, Cm(0.6), Cm(line_height_cm + 0.1),
                    font_size=font_size, color=C_ACCENT, bold=True)
        add_textbox(slide, text, left + Cm(0.65), y, width - Cm(0.65), Cm(line_height_cm + 0.1),
                    font_size=font_size, color=C_TEXT)


# ═════════════════════════════════════════════════════════════════════════════
# ONEPAGER 1 — Pre-Thesis Todos
# ═════════════════════════════════════════════════════════════════════════════

def make_prethesis(path: str):
    prs = new_prs()
    sl  = blank_slide(prs)

    # Background
    add_rect(sl, 0, 0, SLIDE_W, SLIDE_H, C_LIGHT)

    # Top header bar
    add_rect(sl, 0, 0, SLIDE_W, Cm(2.2), C_DARK)

    # Title
    add_textbox(sl, "KlimaCrawler — Vorarbeiten vor Beginn der Bachelorarbeit",
                Cm(1.2), Cm(0.3), Cm(28), Cm(1.0),
                font_size=18, bold=True, color=C_WHITE)
    add_textbox(sl, "Interne Aufgabenliste · Team · Stand Mai 2026",
                Cm(1.2), Cm(1.25), Cm(28), Cm(0.7),
                font_size=10, color=RGBColor(0xAA, 0xBB, 0xCC))

    # ── SECTION 1: Crawl-Qualität ──────────────────────────────────────────
    section_header(sl, "1 · Crawl-Qualität validieren", Cm(2.55), C_ACCENT)

    col1_items = [
        ("▸", "Coverage prüfen  —  Für jede der 96 Kommunen verifizieren, dass Klimaschutz-relevante Unterseiten erreicht wurden. 10 Kommunen manuell gegen offizielle Website abgleichen. Ergebnis: Tabelle Muni-ID · gecrawlte Seiten · relevante Seiten."),
        ("▸", "Inhaltsrelevanz  —  Impact-Score-Verteilung analysieren, Anteil Seiten mit Score ≥ 3 dokumentieren, negative Keywords auf Treffgenauigkeit prüfen. Ergebnis: Histogramm + Schwellwert-Empfehlung."),
        ("▸", "Duplikate entfernen  —  Über url_canonical und Content-Hash identifizieren, aus documents_raw entfernen oder markieren. Ziel: Duplikatrate < 5 %."),
        ("▸", "Segmentqualität  —  Stichprobe 50 Segmente manuell bewerten: vollständige Sätze, kein HTML-Müll, Länge 80–1500 Zeichen. Ergebnis: Qualitätsbericht als Markdown."),
        ("▸", "Abschlussdokument  —  Einseitiges Datenblatt: Kommunen, Dokumente, Segmente, Chunks, Zeitraum, Lücken → docs/dataset_card.md"),
    ]
    bullet_lines(sl, col1_items, Cm(1.5), Cm(3.4), Cm(16.2), line_height_cm=0.88, font_size=9.5)

    # ── SECTION 2: Datensatz verarbeiten ──────────────────────────────────
    section_header(sl, "2 · Datensatz vollständig verarbeiten", Cm(2.55), C_GREEN)
    # Right column
    col2_items = [
        ("▸", "Chunking abschließen  —  Tiktoken-Chunking (600 Tok / 60 Overlap) für alle 96 Kommunen. Vollständigkeitsprüfung: jedes Segment mit Score ≥ 0 besitzt einen Chunk. Ergebnis: chunks-Tabelle vollständig befüllt."),
        ("▸", "Embeddings berechnen  —  all-MiniLM-L6-v2 für alle Chunks (CPU oder HPC). Kein Chunk darf ohne Embedding bleiben. Ergebnis: embedding-Spalte lückenlos gefüllt."),
        ("▸", "Ollama auf HPC  —  llama3.1:8b auf HPC-Knoten installieren und testen. extract_graph_ollama.py für alle 96 Kommunen durchlaufen lassen. Ergebnis: graph_triplets-Tabelle mit LLM-Extraktion vollständig befüllt."),
    ]
    bullet_lines(sl, col2_items, Cm(18.2), Cm(3.4), Cm(14.5), line_height_cm=0.95, font_size=9.5)

    # ── Divider ──────────────────────────────────────────────────────────
    add_rect(sl, Cm(17.8), Cm(2.55), Cm(0.04), Cm(4.5), C_GREY)

    # ── Footer ───────────────────────────────────────────────────────────
    add_rect(sl, 0, SLIDE_H - Cm(0.9), SLIDE_W, Cm(0.9), C_DARK)
    add_textbox(sl, "Alle Punkte müssen vor Übergabe an die Bachelorstudierenden abgeschlossen sein · geschätzter Aufwand: 4–6 Wochen",
                Cm(1.2), SLIDE_H - Cm(0.82), Cm(32), Cm(0.75),
                font_size=8.5, color=RGBColor(0xAA, 0xBB, 0xCC))

    prs.save(path)
    print(f"  ✓ Gespeichert: {path}")


# ═════════════════════════════════════════════════════════════════════════════
# ONEPAGER 2 — Bachelorarbeit Ausschreibung
# ═════════════════════════════════════════════════════════════════════════════

def make_thesis(path: str):
    prs = new_prs()
    sl  = blank_slide(prs)

    # Background
    add_rect(sl, 0, 0, SLIDE_W, SLIDE_H, C_LIGHT)

    # Top header bar
    add_rect(sl, 0, 0, SLIDE_W, Cm(2.3), C_DARK)

    # Title
    add_textbox(sl,
                "Evaluierung und Aufbau eines GraphRAG-Systems zur Analyse kommunaler Klimaschutzmaßnahmen",
                Cm(1.2), Cm(0.25), Cm(29), Cm(1.1),
                font_size=16, bold=True, color=C_WHITE)
    add_textbox(sl, "Bachelorarbeit · Ausschreibung · Forschungsprojekt KlimaCrawler",
                Cm(1.2), Cm(1.3), Cm(28), Cm(0.7),
                font_size=10, color=RGBColor(0xAA, 0xBB, 0xCC))

    # ── Hintergrund ──────────────────────────────────────────────────────
    section_header(sl, "Hintergrund", Cm(2.55), C_ACCENT)
    add_textbox(sl,
                "Im Rahmen eines laufenden Forschungsprojekts wird ein System entwickelt, das Klimaschutzinformationen aller 96 bayerischen Kommunen automatisiert erfasst, strukturiert und durchsuchbar macht. Der technische Kern kombiniert Web-Crawling, semantische Suche (RAG) und wissensbasierte Graphen. Ein prototypischer GraphRAG-Ansatz ist bereits implementiert — wissenschaftlich belastbare Evaluation und ein produktionsreifer Graph fehlen noch.",
                Cm(1.5), Cm(3.25), Cm(31), Cm(1.4),
                font_size=9.8, color=C_TEXT)

    # ── Drei Aufgabenblöcke nebeneinander ────────────────────────────────
    top_blocks = Cm(4.85)
    block_w    = Cm(10.3)
    gap        = Cm(0.5)

    blocks = [
        ("Teil 1 · Benchmarking  (~40 %)", C_ACCENT, [
            "Goldstandard-Datensatz: 20–30 realistische Frage-Antwort-Paare zu bayerischen Klimaschutzmaßnahmen",
            "Systematischer Vergleich: Plain RAG · Hybrid RAG · Structured RAG · GraphRAG",
            "Metriken: Precision@k, Recall@k, MRR, ROUGE, BERTScore, LLM-as-Judge",
            "Identifikation von Stärken & Grenzen des GraphRAG-Ansatzes",
        ]),
        ("Teil 2 · Graph-Erstellung & Validierung  (~40 %)", C_GREEN, [
            "Verbesserung der LLM-basierten Extraktion von Entitäten und Relationen",
            "Automatische Qualitätsprüfung der Tripel via LLM-as-Judge",
            "Visuelle Inspektion & manuelle Validierung (interaktive HTML-Darstellung vorhanden)",
            "Dokumentation: Entitätstypen, Relationstypen, Abdeckung pro Kommune",
        ]),
        ("Teil 3 · Query-System  (~20 %)", RGBColor(0xE6, 0x7E, 0x22), [
            "Query-Agent: Natürlichsprachige Anfragen → strukturierte Graphabfragen",
            "Kombinierte Antworten aus Graphstruktur und Freitext",
            "Grundlage für spätere Multi-Agenten-Erweiterung (Masterarbeit)",
        ]),
    ]

    for i, (title, color, items) in enumerate(blocks):
        x = Cm(1.2) + i * (block_w + gap)
        add_rect(sl, x, top_blocks, block_w, Cm(0.55), color)
        add_textbox(sl, title, x + Cm(0.2), top_blocks + Cm(0.05), block_w - Cm(0.3), Cm(0.5),
                    font_size=9, bold=True, color=C_WHITE)
        add_rect(sl, x, top_blocks + Cm(0.55), block_w, Cm(2.5),
                 RGBColor(0xE8, 0xED, 0xF5))
        for j, item in enumerate(items):
            add_textbox(sl, f"▸  {item}",
                        x + Cm(0.2),
                        top_blocks + Cm(0.65) + Cm(j * 0.6),
                        block_w - Cm(0.3), Cm(0.6),
                        font_size=8.8, color=C_TEXT)

    # ── Ausblick + Voraussetzungen ────────────────────────────────────────
    section_header(sl, "Ausblick & Voraussetzungen", Cm(8.1), C_ACCENT)

    outlook_items = [
        ("▸", "Ausblick: Die Arbeit legt die Grundlage für eine Multi-Agenten-Erweiterung (Masterarbeit) — spezialisierte Agenten für Retrieval, Graph-Traversal und Antwortsynthese."),
        ("▸", "Voraussetzungen: Python · Grundkenntnisse ML/NLP · Interesse an Wissensgraphen oder Information Retrieval · Bereitschaft, sich in bestehenden Code einzuarbeiten."),
        ("▸", "Was Sie erwartet: Vollständiger Datensatz (96 Kommunen, >100.000 Chunks), lauffähiger Prototyp, direkte Betreuung, klar definierter Scope."),
    ]
    bullet_lines(sl, outlook_items, Cm(1.5), Cm(8.95), Cm(31), line_height_cm=0.65, font_size=9.5)

    # ── Footer ───────────────────────────────────────────────────────────
    add_rect(sl, 0, SLIDE_H - Cm(0.9), SLIDE_W, Cm(0.9), C_DARK)
    add_textbox(sl, "Forschungsprojekt KlimaCrawler · Bachelorarbeit · ca. 6 Monate · Beginn nach Abschluss der Vorarbeiten",
                Cm(1.2), SLIDE_H - Cm(0.82), Cm(32), Cm(0.75),
                font_size=8.5, color=RGBColor(0xAA, 0xBB, 0xCC))

    prs.save(path)
    print(f"  ✓ Gespeichert: {path}")


# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    os.makedirs("docs", exist_ok=True)
    make_prethesis("docs/PreThesis_Todos.pptx")
    make_thesis("docs/Bachelorarbeit_Ausschreibung.pptx")
    print("\nBeide OnePager erstellt.")
