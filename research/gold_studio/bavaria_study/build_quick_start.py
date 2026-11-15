import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

root = Path(__file__).resolve().parents[3]
font = "/System/Library/Fonts/Supplemental/Arial.ttf"
pdfmetrics.registerFont(TTFont("Review", font))
pdfmetrics.registerFont(TTFont("ReviewBold", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"))
pdfmetrics.registerFontFamily("Review", normal="Review", bold="ReviewBold")
s = getSampleStyleSheet()
s["BodyText"].fontName = "Review"
s["BodyText"].fontSize = 10.5
s["BodyText"].leading = 15
s["BodyText"].spaceAfter = 10
s["Title"].fontName = "Review"
s["Title"].fontSize = 24
s["Title"].leading = 28
s["Title"].textColor = HexColor("#123a42")
text = (root / "research/gold_studio/bavaria_study/REVIEWER_QUICK_START.md").read_text()
text = re.sub(r"\n(?=[2-5]\. )", "\n\n", text)
parts = text.strip().split("\n\n")
flow = [
    Paragraph("KLIMA · RESEARCH GOLD STUDIO", s["BodyText"]),
    Paragraph(parts[0].lstrip("# "), s["Title"]),
    Spacer(1, 12),
]
for part in parts[1:]:
    part = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", part).replace("`", "")
    flow.append(Paragraph(part, s["BodyText"]))


def footer(c, d):
    c.setFont("Review", 9)
    c.setFillColor(HexColor("#52626a"))
    c.drawString(42, 27, "Original-first · unabhängige Einzelurteile · explizites Datei-Backup")
    c.drawRightString(A4[0] - 42, 27, str(d.page))


SimpleDocTemplate(
    str(root / "deliverables/Reviewer_Quick_Start.pdf"),
    pagesize=A4,
    leftMargin=42,
    rightMargin=42,
    topMargin=36,
    bottomMargin=42,
).build(flow, onFirstPage=footer, onLaterPages=footer)
