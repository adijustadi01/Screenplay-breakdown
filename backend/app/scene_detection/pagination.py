"""Pagination (for formats without physical pages) and 1/8-page scene lengths.

Lengths are computed by partitioning each page's body into scene segments:
a segment starts at a scene heading (or the top of the page for a scene that
continues from the previous page) and ends at the next heading (or the
bottom of the body). Each page's segments are rounded to eighths with the
largest-remainder method so every full page sums to exactly 8/8, and every
scene that starts on a page gets at least 1/8. This is deterministic and
auditable: total eighths == 8 x body pages (last page pro rata).
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass

from ..importers.base import RawElement, RawPage
from ..models.enums import ElementType, PageTextSource

LINES_PER_PAGE = 55
# Characters per line by element type (Courier 12, standard margins).
WRAP = {
    ElementType.ACTION: 60, ElementType.SCENE_HEADING: 60, ElementType.SHOT: 60,
    ElementType.DIALOGUE: 35, ElementType.PARENTHETICAL: 25, ElementType.CHARACTER: 38,
    ElementType.TRANSITION: 20, ElementType.UNKNOWN: 60, ElementType.TITLE_PAGE: 60,
}
NO_SPACE_BEFORE = {ElementType.DIALOGUE, ElementType.PARENTHETICAL}
NOT_ON_PAGE = {ElementType.NON_PRINTING, ElementType.PAGE_HEADER}


def wrapped_lines(el: RawElement) -> int:
    width = WRAP.get(el.element_type, 60)
    total = 0
    for para in el.raw_text.split("\n") if el.element_type == ElementType.ACTION else [el.text]:
        total += max(1, len(textwrap.wrap(" ".join(para.split()), width)) or 1)
    return total


def paginate_structured(elements: list[RawElement]) -> list[RawPage]:
    """Assign page_index / y (in lines) to elements of DOCX/FDX/Fountain imports."""
    pages: list[RawPage] = []
    texts: dict[int, list[str]] = {}
    has_title = any(e.element_type == ElementType.TITLE_PAGE for e in elements)
    page = 1
    line = 0
    if has_title:
        pages.append(RawPage(1, PageTextSource.STRUCTURED, page_label=None, is_title_page=True,
                             body_top=0, body_bottom=LINES_PER_PAGE))
        page = 2
    started_body = False
    for el in elements:
        if el.element_type == ElementType.TITLE_PAGE:
            el.page_index, el.y_top, el.y_bottom = 1, 0.0, 1.0
            texts.setdefault(1, []).append(el.raw_text)
            continue
        if el.element_type == ElementType.NON_PRINTING and el.text == "===":  # forced break
            if line > 0:
                page += 1
                line = 0
        if el.element_type in NOT_ON_PAGE:
            el.page_index = page
            el.y_top = el.y_bottom = float(line)
            el.line_count = 0
            texts.setdefault(page, []).append(el.raw_text)
            continue
        n = wrapped_lines(el)
        space = 0 if (el.element_type in NO_SPACE_BEFORE or line == 0) else 1
        # Keep headings and cues with what follows: don't start them in the last 3 lines.
        keep = 3 if el.element_type in (ElementType.SCENE_HEADING, ElementType.CHARACTER) else 0
        if line + space + n + keep > LINES_PER_PAGE and line > 0:
            # Dialogue/action may split across pages in reality; we break at element level.
            page += 1
            line = 0
            space = 0
        line += space
        el.page_index = page
        el.y_top = float(line)
        el.line_count = n
        line += n
        el.y_bottom = float(line)
        texts.setdefault(page, []).append(el.raw_text)
        started_body = True
    last = page if started_body else len(pages)
    for p in range(len(pages) + 1, last + 1):
        label = str(p - 1) if has_title else str(p)
        pages.append(RawPage(p, PageTextSource.STRUCTURED, raw_text="\n".join(texts.get(p, [])),
                             page_label=label, body_top=0, body_bottom=LINES_PER_PAGE))
    if has_title and pages:
        pages[0].raw_text = "\n".join(texts.get(1, []))
    return pages


@dataclass
class SceneSpan:
    start_page: int
    end_page: int
    eighths: int


def compute_eighths(
    pages: list[RawPage],
    elements: list[RawElement],
    scene_starts: list[int],
    scene_ends: list[int],
) -> list[SceneSpan]:
    """scene_starts/ends: element index ranges [start, end] for each scene, in order."""
    page_by_idx = {p.page_index: p for p in pages}
    n_scenes = len(scene_starts)
    shares: dict[int, list[tuple[int, float, bool]]] = {}  # page -> [(scene, share, starts_here)]
    spans = [SceneSpan(0, 0, 0) for _ in range(n_scenes)]

    # Last body element per page gives the true bottom of a short last page.
    page_last_y: dict[int, float] = {}
    for el in elements:
        if el.page_index is not None and el.y_bottom is not None and el.element_type not in NOT_ON_PAGE \
                and el.element_type != ElementType.TITLE_PAGE:
            page_last_y[el.page_index] = max(page_last_y.get(el.page_index, 0.0), el.y_bottom)
    last_body_page = max(page_last_y) if page_last_y else 0

    # Boundaries: (page, y) where each scene starts; scene owns until the next start.
    starts = []
    for s, ei in enumerate(scene_starts):
        el = elements[ei]
        starts.append((el.page_index or 0, el.y_top or 0.0))
    for s in range(n_scenes):
        sp, sy = starts[s]
        last_el = elements[scene_ends[s]]
        ep = last_el.page_index or sp
        if s + 1 < n_scenes:
            np_, ny = starts[s + 1]
        else:
            np_, ny = last_body_page, page_last_y.get(last_body_page, 0.0)
            np_ = max(np_, ep)
        spans[s].start_page, spans[s].end_page = sp, ep
        for p in range(sp, max(np_, sp) + 1):
            page = page_by_idx.get(p)
            if page is None or page.is_title_page:
                continue
            top = sy if p == sp else page.body_top
            if s + 1 < n_scenes and p == np_:
                bottom = ny
            elif p == last_body_page:
                bottom = page_last_y.get(p, page.body_bottom)
            else:
                bottom = page.body_bottom
            height = max(1e-6, page.body_bottom - page.body_top)
            share = max(0.0, (bottom - top) / height)
            if share > 0 or p == sp:
                shares.setdefault(p, []).append((s, share, p == sp))

    for p, items in shares.items():
        total = sum(sh for _, sh, _ in items)
        target = round(total * 8)
        raw = [sh * 8 for _, sh, _ in items]
        base = [int(r) for r in raw]
        # Every scene that starts on this page gets at least 1/8.
        for i, (_, _, starts_here) in enumerate(items):
            if starts_here and base[i] == 0:
                base[i] = 1
        remaining = target - sum(base)
        order = sorted(range(len(items)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        k = 0
        while remaining > 0 and order:
            base[order[k % len(order)]] += 1
            remaining -= 1
            k += 1
        for i, (s, _, _) in enumerate(items):
            spans[s].eighths += base[i]
    return spans


def format_eighths(n: int | None) -> str:
    if n is None:
        return "—"
    pages, e = divmod(n, 8)
    if pages and e:
        return f"{pages} {e}/8"
    return f"{pages}" if pages else f"{e}/8"
