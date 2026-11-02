"""PDF importer: text layer via PyMuPDF, OCR (Tesseract) for image-only pages.

Uses word geometry, not just text, because the layout carries meaning:
indents give element types, the margins carry scene numbers, and vertical
extent gives 1/8-page lengths.
"""
from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from ..models.enums import ElementType, PageTextSource, Severity, SourceFormat
from ..scene_detection.headings import clean_scene_number, has_heading_prefix, is_scene_number_token
from .base import ImportIssue, ImportResult, RawElement, RawPage, nonspace_len
from .layout import VisualLine, classify_lines, dominant_left

PT = 72.0                     # points per inch
OCR_DPI = 300
MIN_TEXT_CHARS = 15           # fewer than this on a page -> treat as image, OCR it
LOW_LINE_CONF = 60.0          # Tesseract word confidence (0–100)
LOW_PAGE_CONF = 75.0

_PAGE_NUM_RE = re.compile(r"^(?:PAGE\s+)?(\d{1,4}[A-Z]{0,2})\.?$", re.I)
_CONTINUED_RE = re.compile(
    r"^(?:[A-Z]{0,2}\d{1,4}[A-Z]{0,3}\s+)?\(?CONTINUED\)?:?(?:\s*\(\d+\))?(?:\s+[A-Z]{0,2}\d{1,4}[A-Z]{0,3})?$",
    re.I,
)
_FURNITURE_RE = re.compile(r"^\((?:MORE|CONTINUED)\)$|^\(?CONT(?:'|’)?D\)?$", re.I)
_REVISION_RE = re.compile(r"\b(REV(?:ISION|ISED|\.)?|DRAFT)\b.*\d", re.I)


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    conf: float | None = None


@dataclass
class _Line:
    words: list[_Word]

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)


def _words_text_layer(page) -> list[_Word]:
    return [_Word(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words") if w[4].strip()]


def _render_for_ocr(page):
    from PIL import Image

    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _ocr_image(img, lang: str) -> list[_Word]:
    import pytesseract

    data = pytesseract.image_to_data(img, lang=lang, config="--psm 6",
                                     output_type=pytesseract.Output.DICT)
    s = PT / OCR_DPI
    out = []
    for i, txt in enumerate(data["text"]):
        conf = float(data["conf"][i])
        if not txt.strip() or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append(_Word(x * s, y * s, (x + w) * s, (y + h) * s, txt, conf))
    return out


def _group_lines(words: list[_Word]) -> list[_Line]:
    words = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    lines: list[_Line] = []
    for w in words:
        yc = (w.y0 + w.y1) / 2
        if lines:
            last = lines[-1]
            lyc = (last.y0 + last.y1) / 2
            if abs(yc - lyc) <= max(2.5, 0.45 * (w.y1 - w.y0)):
                last.words.append(w)
                continue
        lines.append(_Line([w]))
    for ln in lines:
        ln.words.sort(key=lambda w: w.x0)
    return lines


def _spans(line: _Line, char_w: float) -> list[list[_Word]]:
    """Split a line into spans wherever the horizontal gap is wider than ~3 characters."""
    spans: list[list[_Word]] = [[line.words[0]]]
    for w in line.words[1:]:
        if w.x0 - spans[-1][-1].x1 > 3 * char_w:
            spans.append([w])
        else:
            spans[-1].append(w)
    return spans


def _span_text(span: list[_Word]) -> str:
    return " ".join(w.text for w in span)


def import_pdf(path: str | Path, ocr_lang: str = "eng") -> ImportResult:
    doc = pymupdf.open(str(path))
    issues: list[ImportIssue] = []
    pages: list[RawPage] = []
    page_lines: list[tuple[RawPage, list[_Line], float]] = []
    source_chars = 0
    ocr_unavailable = False

    # OCR image-only pages in parallel (Tesseract runs as a subprocess, so threads suffice).
    need_ocr = [pno for pno, page in enumerate(doc, start=1)
                if nonspace_len(page.get_text("text")) < MIN_TEXT_CHARS]
    ocr_results: dict[int, list[_Word]] = {}
    if need_ocr:
        from concurrent.futures import ThreadPoolExecutor

        # One thread per Tesseract process: we parallelise across pages ourselves, and
        # Tesseract's own OpenMP threads on top of that oversubscribe the CPU badly.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")

        images = {pno: _render_for_ocr(doc[pno - 1]) for pno in need_ocr}
        with ThreadPoolExecutor(max_workers=max(1, min(8, (os.cpu_count() or 2)))) as pool:
            futures = {pno: pool.submit(_ocr_image, img, ocr_lang) for pno, img in images.items()}
            for pno, fut in futures.items():
                try:
                    ocr_results[pno] = fut.result()
                except Exception as exc:  # tesseract missing, bad language pack, ...
                    ocr_results[pno] = []
                    if not ocr_unavailable:
                        issues.append(ImportIssue("ocr_unavailable", f"OCR failed: {exc}", Severity.ERROR, pno))
                        ocr_unavailable = True
        del images

    for pno, page in enumerate(doc, start=1):
        rp = RawPage(page_index=pno, text_source=PageTextSource.TEXT_LAYER)
        words = _words_text_layer(page)
        layer_text = page.get_text("text")
        if pno in ocr_results:
            ocr_words = ocr_results[pno]
            if ocr_words:
                words = ocr_words
                rp.text_source = PageTextSource.OCR
                rp.ocr_mean_confidence = statistics.fmean(w.conf for w in words)
                layer_text = "\n".join(w.text for w in words)
                if rp.ocr_mean_confidence < LOW_PAGE_CONF:
                    rp_msg = f"Page {pno}: OCR confidence {rp.ocr_mean_confidence:.0f}/100 — verify the text."
                    issues.append(ImportIssue("low_ocr_confidence", rp_msg, Severity.WARNING, pno))
        rp.raw_text = layer_text
        source_chars += nonspace_len(layer_text)
        if not words:
            issues.append(ImportIssue("empty_page", f"Page {pno} has no text.", Severity.INFO, pno))
        widths = [(w.x1 - w.x0) / max(1, len(w.text)) for w in words if len(w.text) >= 3]
        char_w = statistics.median(widths) if widths else 7.2
        page_lines.append((rp, _group_lines(words) if words else [], char_w))
        rp.body_bottom = page.rect.height
        pages.append(rp)

    # --- Document-wide action margin (left-most common x of non-number text) --------------
    starts = []
    for _, lines, char_w in page_lines:
        for ln in lines:
            sp = _spans(ln, char_w)
            first = sp[0]
            if is_scene_number_token(_span_text(first)) and len(sp) > 1:
                first = sp[1]
            starts.append(first[0].x0)
    action_x = dominant_left(starts) or 1.5 * PT

    visual: list[VisualLine] = []
    furniture: list[RawElement] = []
    body_tops: list[float] = []
    body_bottoms: list[float] = []

    for rp, lines, char_w in page_lines:
        page_h = rp.body_bottom
        prev_y = None
        pitches = [b.y0 - a.y0 for a, b in zip(lines, lines[1:]) if b.y0 - a.y0 > 1]
        pitch = statistics.median(pitches) if pitches else 12.0
        page_body: list[VisualLine] = []
        for ln in lines:
            sp = _spans(ln, char_w)
            raw = "   ".join(_span_text(s) for s in sp)
            ocr_conf = None
            confs = [w.conf for w in ln.words if w.conf is not None]
            if confs:
                ocr_conf = statistics.fmean(confs)
            left_num = right_num = None
            # Revision asterisks at the far right margin.
            while sp and _span_text(sp[-1]).strip("* ") == "" and sp[-1][0].x0 > action_x + 5.5 * PT:
                sp = sp[:-1]
            if len(sp) > 1 and is_scene_number_token(_span_text(sp[0])) and sp[0][-1].x1 < action_x - 0.15 * PT:
                left_num = clean_scene_number(_span_text(sp[0]))
                sp = sp[1:]
            if len(sp) > 1 and is_scene_number_token(_span_text(sp[-1])) and sp[-1][0].x0 > action_x + 4.8 * PT:
                right_num = clean_scene_number(_span_text(sp[-1]))
                sp = sp[:-1]
            if not sp:
                continue
            main = " ".join(_span_text(s) for s in sp).strip()
            # Strip trailing revision asterisk glued to the text.
            main = re.sub(r"\s+\*+$", "", main)
            y0, y1 = ln.y0, ln.y1
            in_header = y1 < 0.9 * PT
            in_footer = y0 > page_h - 0.75 * PT
            whole = re.sub(r"[\[\]|]", "", " ".join(_span_text(s) for s in _spans(ln, char_w))).strip()
            is_furn = (
                _CONTINUED_RE.match(whole) or _FURNITURE_RE.match(main)
                or ((in_header or in_footer) and len(main) <= 60)
                or (in_header and _REVISION_RE.search(main))
            )
            if is_furn:
                m = _PAGE_NUM_RE.match(main.strip())
                if m and (in_header or in_footer) and rp.page_label is None:
                    rp.page_label = m.group(1).upper()
                furniture.append(RawElement(ElementType.PAGE_HEADER, main, raw, rp.page_index, y0, y1,
                                            ocr_confidence=ocr_conf))
                continue
            # Dual dialogue: two content columns side by side.
            dual_split = None
            if len(sp) >= 2:
                gaps = [(sp[i + 1][0].x0 - sp[i][-1].x1, i) for i in range(len(sp) - 1)]
                g, gi = max(gaps)
                if g > 0.8 * PT and sp[0][0].x0 > action_x + 0.3 * PT:
                    dual_split = gi + 1
            gap_before = prev_y is None or (y0 - prev_y) > 1.5 * pitch
            prev_y = y0
            if dual_split is not None:
                for col, part in enumerate((sp[:dual_split], sp[dual_split:])):
                    t = " ".join(_span_text(s) for s in part)
                    page_body.append(VisualLine(rp.page_index, t, t, (part[0][0].x0 - action_x) / PT,
                                                y0, y1, None, None, ocr_conf, gap_before,
                                                dual=True, col=col))
                continue
            page_body.append(VisualLine(
                rp.page_index, main, raw, (sp[0][0].x0 - action_x) / PT, y0, y1,
                left_num, right_num, ocr_conf, gap_before,
            ))
        # Dual-dialogue lines come in pairs (left col, right col): reorder to left block, right block.
        page_body = _reorder_dual(page_body)
        if page_body:
            body_tops.append(min(v.y_top for v in page_body))
            body_bottoms.append(max(v.y_bottom for v in page_body))
        visual.extend(page_body)

    # --- Title page(s): leading pages with no heading and few lines -----------------------
    first_heading_page = next(
        (v.page_index for v in visual if v.left_num or has_heading_prefix(v.text)), None
    )
    title_pages = set()
    for rp, lines, _ in page_lines:
        if first_heading_page is None or rp.page_index >= first_heading_page:
            break
        body_lines = [v for v in visual if v.page_index == rp.page_index]
        if len(body_lines) < 40 and not any("FADE IN" in v.text.upper() for v in body_lines):
            rp.is_title_page = True
            title_pages.add(rp.page_index)

    title_elements = [
        RawElement(ElementType.TITLE_PAGE, v.text, v.raw_text, v.page_index, v.y_top, v.y_bottom,
                   ocr_confidence=v.ocr_conf)
        for v in visual if v.page_index in title_pages
    ]
    body_visual = [v for v in visual if v.page_index not in title_pages]
    elements = classify_lines(body_visual)

    # Merge furniture + title + body back into reading order.
    all_elements = sorted(
        title_elements + furniture + elements,
        key=lambda e: (e.page_index, e.y_top if e.y_top is not None else 0.0),
    )

    # Document body extent (used for 1/8 pages). Medians resist odd pages.
    body_top = statistics.median(body_tops) if body_tops else 1.0 * PT
    body_bottom = statistics.median(body_bottoms) if body_bottoms else 10.0 * PT
    for rp in pages:
        rp.body_top, rp.body_bottom = min(body_top, rp.body_bottom), body_bottom

    for idx, el in enumerate(all_elements):
        if el.ocr_confidence is not None and el.ocr_confidence < LOW_LINE_CONF:
            issues.append(ImportIssue(
                "low_ocr_line", f"Low OCR confidence ({el.ocr_confidence:.0f}) on page {el.page_index}: "
                f"{el.text[:70]!r}", Severity.WARNING, el.page_index, idx))

    # Page 1 of the script body conventionally carries no printed number.
    body_pages = [p for p in pages if not p.is_title_page]
    if len(body_pages) >= 2 and body_pages[0].page_label is None and body_pages[1].page_label == "2":
        body_pages[0].page_label = "1"

    title = title_elements[0].text if title_elements else None
    return ImportResult(
        source_format=SourceFormat.PDF,
        elements=all_elements,
        pages=pages,
        issues=issues,
        source_char_count=source_chars,
        needs_pagination=False,
        title=title,
    )


def _reorder_dual(lines: list[VisualLine]) -> list[VisualLine]:
    out: list[VisualLine] = []
    i = 0
    while i < len(lines):
        if not lines[i].dual:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].dual:
            j += 1
        block = lines[i:j]
        left = [v for v in block if v.col == 0]
        right = [v for v in block if v.col == 1]
        for k, v in enumerate(left + right):
            v.gap_before = k == 0 or k == len(left)
        out.extend(left + right)
        i = j
    return out
