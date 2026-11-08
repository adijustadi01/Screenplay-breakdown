"""Export a script's breakdown — all of it, or only the parts the user picks — to a printable PDF.

Built from the same tables as the Excel export (`sections.py`). Landscape A4. Each part starts
on a new page; the scene breakdown is printed as one breakdown sheet per scene (wide tables
would be unreadable on paper). CHECK rows are yellow and night scenes blue, as in Excel.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from reportlab.platypus import Table as RLTable
from sqlalchemy.orm import Session

from . import sections as sec
from .excel import is_night
from .sections import CHECK, Table

NAVY = colors.HexColor("#1F3A5F")
CHECK_BG = colors.HexColor("#FFF2B3")
NIGHT_BG = colors.HexColor("#DCE3F0")
CARD_BG = colors.HexColor("#EEF1F6")
LINE = colors.HexColor("#C8CCD4")
GREY = colors.HexColor("#555555")
MAX_CELL = 2500          # characters; longer cells are cut (the Excel file has the full text)
MAX_COLS = 18            # wider tables are split into several column blocks

PAGE = landscape(A4)
MARGIN = 12 * mm
WIDTH = PAGE[0] - 2 * MARGIN


def _fonts() -> tuple[str, str, str]:
    """A Unicode TrueType font if the computer has one (better coverage of accents, dashes, ×),
    otherwise the built-in Helvetica."""
    candidates = [
        ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ]
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    candidates.insert(0, ("Arial", os.path.join(win, "arial.ttf"), os.path.join(win, "arialbd.ttf"),
                          os.path.join(win, "ariali.ttf")))
    for name, reg, bold, ital in candidates:
        if all(os.path.exists(p) for p in (reg, bold, ital)):
            try:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, reg))
                    pdfmetrics.registerFont(TTFont(name + "-Bold", bold))
                    pdfmetrics.registerFont(TTFont(name + "-Italic", ital))
                return name, name + "-Bold", name + "-Italic"
            except Exception:  # noqa: BLE001 - a broken font file must not stop the export
                continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _styles(size: float = 7.5):
    reg, bold, ital = _fonts()
    lead = size * 1.25
    return {
        "cell": ParagraphStyle("cell", fontName=reg, fontSize=size, leading=lead),
        "bold": ParagraphStyle("bold", fontName=bold, fontSize=size, leading=lead),
        "head": ParagraphStyle("head", fontName=bold, fontSize=size, leading=lead, textColor=colors.white),
        "label": ParagraphStyle("label", fontName=bold, fontSize=8, leading=10, textColor=GREY),
        "value": ParagraphStyle("value", fontName=reg, fontSize=8.5, leading=11),
        "card": ParagraphStyle("card", fontName=bold, fontSize=10, leading=13, textColor=colors.white),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, spaceAfter=4),
        "note": ParagraphStyle("note", fontName=ital, fontSize=8.5, leading=11, textColor=GREY, spaceAfter=6),
        "title": ParagraphStyle("title", fontName=bold, fontSize=24, leading=30, alignment=TA_CENTER),
        "sub": ParagraphStyle("sub", fontName=reg, fontSize=11, leading=15, alignment=TA_CENTER, textColor=GREY),
        "fonts": (reg, bold, ital),
    }


def _p(v, style) -> Paragraph:
    s = "" if v is None else str(v)
    if len(s) > MAX_CELL:
        s = s[:MAX_CELL] + " … (cut — see the Excel file for the full text)"
    return Paragraph(escape(s).replace("\n", "<br/>"), style)


def _col_widths(t: Table, headers: list[str], total: float) -> list[float]:
    raw = [t.widths.get(h, max(8, min(30, len(h) + 4))) for h in headers]
    scale = total / sum(raw)
    return [w * scale for w in raw]


def _chunks(t: Table) -> list[list[int]]:
    """Column index groups so a very wide table (e.g. a long DOOD) prints in readable blocks."""
    n = len(t.headers)
    if n <= MAX_COLS:
        return [list(range(n))]
    keep = list(range(t.repeat_cols))
    rest = list(range(t.repeat_cols, n))
    step = MAX_COLS - t.repeat_cols
    return [keep + rest[i:i + step] for i in range(0, len(rest), step)]


def _grid(t: Table, st) -> list:
    if not t.rows:
        return [_p("Nothing to show yet.", st["note"])]
    idx = {h: i for i, h in enumerate(t.headers)}
    check_i, night_i, bold_i = idx.get(t.check_col), idx.get(t.night_col), idx.get(t.bold_col)
    out = []
    chunks = _chunks(t)
    for n, cols in enumerate(chunks):
        headers = [t.headers[i] for i in cols]
        data = [[_p(h, st["head"]) for h in headers]]
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for r, row in enumerate(t.rows, start=1):
            bold = bold_i is not None and bool(row[bold_i])
            data.append([_p(row[i], st["bold"] if bold else st["cell"]) for i in cols])
            if check_i is not None and row[check_i] == CHECK:
                style.append(("BACKGROUND", (0, r), (-1, r), CHECK_BG))
            elif night_i is not None and is_night(row[night_i]):
                style.append(("BACKGROUND", (0, r), (-1, r), NIGHT_BG))
        if len(chunks) > 1:
            out.append(_p(f"Columns part {n + 1} of {len(chunks)}", st["note"]))
        tbl = RLTable(data, colWidths=_col_widths(t, headers, WIDTH), repeatRows=1, splitInRow=1)
        tbl.setStyle(TableStyle(style))
        out += [tbl, Spacer(1, 6)]
    return out


def _cards(t: Table, st) -> list:
    """One breakdown sheet per row: a title bar, then label / value lines for what is filled in."""
    if not t.rows:
        return [_p("Nothing to show yet.", st["note"])]
    idx = {h: i for i, h in enumerate(t.headers)}
    title_cols = [idx[h] for h in t.card_title if h in idx]
    out = []
    for row in t.rows:
        title = "   ·   ".join(str(row[i]) for i in title_cols if row[i] not in (None, ""))
        if t.card_title:
            title = f"Scene {title}"
        flagged = t.check_col in idx and row[idx[t.check_col]] == CHECK
        night = t.night_col in idx and is_night(row[idx[t.night_col]])
        data = [[_p(title + ("   —   CHECK" if flagged else ""), st["card"]), ""]]
        for i, h in enumerate(t.headers):
            v = row[i]
            if i in title_cols or h == t.check_col or v in (None, ""):
                continue
            data.append([_p(h, st["label"]), _p(v, st["value"])])
        style = [
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (1, 0), NAVY),
            ("BACKGROUND", (0, 1), (0, -1), CHECK_BG if flagged else NIGHT_BG if night else CARD_BG),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.6, NAVY),
            ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        tbl = RLTable(data, colWidths=[42 * mm, WIDTH - 42 * mm], splitInRow=1)
        tbl.setStyle(TableStyle(style))
        out += [CondPageBreak(45 * mm), KeepTogether([tbl]), Spacer(1, 8)]
    return out


def _pairs(t: Table, st) -> list:
    data = [[_p(a, st["label"]), _p(b, st["value"])] for a, b in t.rows if a or b]
    tbl = RLTable(data, colWidths=[60 * mm, WIDTH - 60 * mm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return [tbl]


def build_pdf(session: Session, version_id: int, keys: list[str] | None = None) -> tuple[bytes, str]:
    """keys: which sections to include (see sections.catalog); None = everything that has content."""
    ctx = sec.Context(session, version_id)
    st = _styles()
    reg = st["fonts"][0]
    keys = sec.resolve(ctx, keys)
    labels = {c["key"]: c["label"] for c in sec.catalog(ctx)}
    exported = datetime.now().strftime("%d %b %Y, %H:%M")

    story: list = [Spacer(1, 50 * mm), _p(ctx.title, st["title"]), Spacer(1, 4 * mm),
                   _p("Production breakdown", st["sub"]),
                   _p(f"{ctx.version.source_filename}  ·  exported {exported}", st["sub"]), Spacer(1, 12 * mm)]
    if keys:
        story.append(_p("In this document: " + "  ·  ".join(labels[k] for k in keys), st["sub"]))
    else:
        story.append(_p("Nothing was selected to export.", st["sub"]))
    for key in keys:
        t = sec.build(ctx, key)
        story.append(PageBreak())
        story.append(_p(labels[key] if key != "summary" else t.note, st["h1"]))
        if t.note and key != "summary":
            story.append(_p(t.note, st["note"]))
        story += (_pairs if t.layout == "pairs" else _cards if t.layout == "cards" else _grid)(t, st)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(MARGIN, 7 * mm, f"{ctx.title} — production breakdown  ·  {exported}")
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                            bottomMargin=14 * mm, title=f"{ctx.title} — production breakdown",
                            author="Screenplay Breakdown")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), f"{sec.file_stem(ctx)} - breakdown.pdf"
