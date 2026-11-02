"""Render the Fountain sample into PDF / scanned PDF / FDX / DOCX (styled + plain).

    python samples/build_samples.py            # from the repo root

The PDF uses standard screenplay layout (Courier 12, 1.5" action margin, scene
numbers in both margins, page numbers, (CONTINUED)/CONTINUED: markers), so it
exercises the layout-based importer the same way a real exported script does.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

import pymupdf  # noqa: E402
from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK  # noqa: E402
from docx.shared import Inches, Pt  # noqa: E402

from app.importers.fountain import import_fountain  # noqa: E402
from app.models.enums import ElementType as E  # noqa: E402
from app.scene_detection.headings import parse_heading  # noqa: E402

SRC = HERE / "monsoon_ledger.fountain"
STEM = "monsoon_ledger"

# --- layout constants (points) ---------------------------------------------------------
PT = 72
TOP, LINE, BODY_LINES = 1.0 * PT, 12.0, 54
X = {E.ACTION: 1.5 * PT, E.SCENE_HEADING: 1.5 * PT, E.SHOT: 1.5 * PT, E.DIALOGUE: 2.5 * PT,
     E.PARENTHETICAL: 3.1 * PT, E.CHARACTER: 3.7 * PT, E.TRANSITION: None}
WIDTH = {E.ACTION: 60, E.SCENE_HEADING: 60, E.SHOT: 60, E.DIALOGUE: 35, E.PARENTHETICAL: 24,
         E.CHARACTER: 38, E.TRANSITION: 20}
NUM_LEFT_X, NUM_RIGHT_X, RIGHT_EDGE = 0.85 * PT, 7.35 * PT, 7.1 * PT
CHAR_W = 7.2


def load():
    res = import_fountain(SRC)
    els = res.elements
    title = [e for e in els if e.element_type == E.TITLE_PAGE]
    body = [e for e in els if e.element_type not in (E.TITLE_PAGE, E.NON_PRINTING)]
    return title, body


def heading_parts(el):
    h = parse_heading(el.raw_text)
    return h.text, h.scene_number


# ======================================================================================
# PDF
# ======================================================================================
def layout_pages(body):
    """-> list of pages; each page = list of (kind, x, text, extra) lines (None = blank)."""
    pages, cur = [], []
    scene_no = None

    def lines_for(el):
        if el.element_type == E.SCENE_HEADING:
            text, num = heading_parts(el)
            return [text], num
        txt = el.text
        if el.element_type == E.ACTION and el.text.startswith("THE END"):
            return ["THE END"], None
        return textwrap.wrap(txt, WIDTH.get(el.element_type, 60)) or [""], None

    # group dialogue blocks so they never split
    blocks, i = [], 0
    while i < len(body):
        el = body[i]
        if el.element_type == E.CHARACTER:
            j = i + 1
            while j < len(body) and body[j].element_type in (E.DIALOGUE, E.PARENTHETICAL):
                j += 1
            blocks.append(body[i:j])
            i = j
        else:
            blocks.append([el])
            i += 1

    def new_page(continuing):
        nonlocal cur
        if continuing and scene_no:
            cur.append(("furn", "(CONTINUED)", scene_no))
        pages.append(cur)
        cur = []
        if continuing and scene_no:
            cur.append(("cont", f"CONTINUED:", scene_no))
            cur.append(None)

    for b in blocks:
        first = b[0]
        rendered = []
        for el in b:
            ls, num = lines_for(el)
            for k, ln in enumerate(ls):
                rendered.append((el.element_type, ln, num if k == 0 else None))
        space = 0 if not cur else 1
        if first.element_type == E.SCENE_HEADING and cur:
            space = 1
        need = space + len(rendered) + (2 if first.element_type == E.SCENE_HEADING else 0)
        body_used = sum(1 for x in cur)
        if body_used + need > BODY_LINES:
            if first.element_type == E.ACTION and BODY_LINES - body_used - space >= 2:
                # split long action across the page break
                fit = BODY_LINES - body_used - space
                if space:
                    cur.append(None)
                for r in rendered[:fit]:
                    cur.append(("el",) + r)
                rendered = rendered[fit:]
                new_page(True)
                space = 0
            else:
                new_page(first.element_type != E.SCENE_HEADING)
                space = 0
        if first.element_type == E.SCENE_HEADING:
            scene_no = rendered[0][2]
        if space:
            cur.append(None)
        for r in rendered:
            cur.append(("el",) + r)
    if cur:
        pages.append(cur)
    return pages


def build_pdf(title, body, path):
    doc = pymupdf.open()
    # title page
    pg = doc.new_page(width=612, height=792)
    tl = [e.text.split(":", 1)[1].strip() for e in title]
    tmap = {e.text.split(":", 1)[0].strip().lower(): e.text.split(":", 1)[1].strip() for e in title}
    y = 3.5 * PT
    for line in (tmap.get("title", ""), "", tmap.get("credit", ""), "", tmap.get("author", "")):
        if line:
            w = len(line) * CHAR_W
            pg.insert_text(((612 - w) / 2, y), line, fontname="cour", fontsize=12)
        y += LINE
    pg.insert_text((1.5 * PT, 9.5 * PT), tmap.get("draft date", ""), fontname="cour", fontsize=12)
    pg.insert_text((1.5 * PT, 9.5 * PT + LINE), tmap.get("contact", ""), fontname="cour", fontsize=12)
    del tl

    for pno, lines in enumerate(layout_pages(body), start=1):
        pg = doc.new_page(width=612, height=792)
        if pno > 1:
            label = f"{pno}."
            pg.insert_text((RIGHT_EDGE + 0.3 * PT - len(label) * CHAR_W, 0.5 * PT + 9), label,
                           fontname="cour", fontsize=12)
        y = TOP
        for ln in lines:
            base = y + 9.5
            if ln is None:
                y += LINE
                continue
            if ln[0] == "furn":
                txt = ln[1]
                pg.insert_text((RIGHT_EDGE - len(txt) * CHAR_W, TOP + BODY_LINES * LINE + 12 + 9.5), txt,
                               fontname="cour", fontsize=12)
                continue
            if ln[0] == "cont":
                pg.insert_text((NUM_LEFT_X, base), ln[2], fontname="cour", fontsize=12)
                pg.insert_text((1.5 * PT, base), ln[1], fontname="cour", fontsize=12)
                pg.insert_text((NUM_RIGHT_X, base), ln[2], fontname="cour", fontsize=12)
                y += LINE
                continue
            _, etype, text, num = ln
            if etype == E.TRANSITION:
                x = RIGHT_EDGE - len(text) * CHAR_W
            elif etype == E.ACTION and text == "THE END":
                x = (612 - len(text) * CHAR_W) / 2
            else:
                x = X.get(etype) or 1.5 * PT
            pg.insert_text((x, base), text, fontname="cour", fontsize=12)
            if etype == E.SCENE_HEADING and num:
                pg.insert_text((NUM_LEFT_X, base), num, fontname="cour", fontsize=12)
                pg.insert_text((NUM_RIGHT_X, base), num, fontname="cour", fontsize=12)
            y += LINE
    doc.save(str(path))
    return doc.page_count


def build_scanned(pdf_path, out_path, dpi=200):
    src = pymupdf.open(str(pdf_path))
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        np_ = out.new_page(width=page.rect.width, height=page.rect.height)
        np_.insert_image(np_.rect, pixmap=pix)
    out.save(str(out_path), deflate=True)


# ======================================================================================
# FDX
# ======================================================================================
FDX_TYPE = {E.SCENE_HEADING: "Scene Heading", E.ACTION: "Action", E.CHARACTER: "Character",
            E.DIALOGUE: "Dialogue", E.PARENTHETICAL: "Parenthetical", E.TRANSITION: "Transition",
            E.SHOT: "Shot"}


def build_fdx(title, body, path):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
           '<FinalDraft DocumentType="Script" Template="No" Version="5">', "<Content>"]
    for el in body:
        t = FDX_TYPE.get(el.element_type, "General")
        if el.element_type == E.SCENE_HEADING:
            text, num = heading_parts(el)
            attr = f' Number="{escape(num)}"' if num else ""
            out.append(f'<Paragraph Type="{t}"{attr}><Text>{escape(text)}</Text></Paragraph>')
        else:
            out.append(f'<Paragraph Type="{t}"><Text>{escape(el.text)}</Text></Paragraph>')
    out.append("</Content><TitlePage><Content>")
    for e in title:
        out.append(f'<Paragraph Type="Action"><Text>{escape(e.text.split(":", 1)[1].strip())}</Text></Paragraph>')
    out.append("</Content></TitlePage></FinalDraft>")
    path.write_text("\n".join(out), encoding="utf-8")


# ======================================================================================
# DOCX
# ======================================================================================
DOCX_INDENT = {E.ACTION: 0.0, E.SCENE_HEADING: 0.0, E.SHOT: 0.0, E.DIALOGUE: 1.0,
               E.PARENTHETICAL: 1.6, E.CHARACTER: 2.2, E.TRANSITION: 4.0}
DOCX_STYLE = {E.ACTION: "Action", E.SCENE_HEADING: "Scene Heading", E.SHOT: "Shot",
              E.DIALOGUE: "Dialogue", E.PARENTHETICAL: "Parenthetical", E.CHARACTER: "Character",
              E.TRANSITION: "Transition"}


def build_docx(title, body, path, styled: bool):
    d = Document()
    sec = d.sections[0]
    sec.left_margin, sec.right_margin = Inches(1.5), Inches(1.0)
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = "Courier New", Pt(12)
    if styled:
        for etype, name in DOCX_STYLE.items():
            st = d.styles.add_style(name, 1)
            st.base_style = normal
            st.paragraph_format.left_indent = Inches(DOCX_INDENT[etype])
    for e in title:
        p = d.add_paragraph(e.text.split(":", 1)[1].strip())
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)
    for el in body:
        text = el.text
        if el.element_type == E.SCENE_HEADING:
            h, num = heading_parts(el)
            text = h if styled or not num else f"{num}\t{h}\t\t{num}"
        if styled:
            p = d.add_paragraph(text, style=DOCX_STYLE.get(el.element_type, "Action"))
        else:
            p = d.add_paragraph(text)
            p.paragraph_format.left_indent = Inches(DOCX_INDENT.get(el.element_type, 0.0))
        if el.element_type == E.TRANSITION:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.save(str(path))


def main():
    title, body = load()
    n = build_pdf(title, body, HERE / f"{STEM}.pdf")
    build_scanned(HERE / f"{STEM}.pdf", HERE / f"{STEM}_scanned.pdf")
    build_fdx(title, body, HERE / f"{STEM}.fdx")
    build_docx(title, body, HERE / f"{STEM}.docx", styled=True)
    build_docx(title, body, HERE / f"{STEM}_plain.docx", styled=False)
    print(f"built PDF ({n} pages), scanned PDF, FDX, DOCX (styled), DOCX (plain) in {HERE}")


if __name__ == "__main__":
    main()
