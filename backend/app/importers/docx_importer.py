"""DOCX importer.

Two kinds of Word screenplays exist in the wild:
  1. Exported from screenwriting software — paragraphs carry styles named
     "Scene Heading", "Character", "Dialogue"... -> typed directly.
  2. Typed in plain Word — everything is "Normal", with indents, tabs or spaces
     doing the layout -> typed by indent with the same rules as PDF.
"""
from __future__ import annotations

from pathlib import Path

import docx
from docx.shared import Length

from ..models.enums import ElementType, Severity, SourceFormat
from .base import ImportIssue, ImportResult, RawElement, nonspace_len
from .layout import VisualLine, classify_lines

STYLE_MAP = {
    "scene heading": ElementType.SCENE_HEADING,
    "slugline": ElementType.SCENE_HEADING,
    "action": ElementType.ACTION,
    "general": ElementType.ACTION,
    "character": ElementType.CHARACTER,
    "dialogue": ElementType.DIALOGUE,
    "parenthetical": ElementType.PARENTHETICAL,
    "transition": ElementType.TRANSITION,
    "shot": ElementType.SHOT,
}
EMU_PER_INCH = 914400


def _indent_in(par) -> float:
    """Effective left indent in inches, following the style chain, plus leading spaces/tabs."""
    ind: Length | None = par.paragraph_format.left_indent
    style = par.style
    while ind is None and style is not None:
        ind = style.paragraph_format.left_indent
        style = style.base_style
    inches = (ind or 0) / EMU_PER_INCH
    text = par.text
    lead = text[: len(text) - len(text.lstrip(" \t"))]
    inches += lead.count("\t") * 0.5 + lead.count(" ") * 0.1
    align = par.paragraph_format.alignment
    if align is not None and int(align) == 2:  # right-aligned -> transition position
        inches = max(inches, 6.0)
    return inches


def import_docx(path: str | Path) -> ImportResult:
    d = docx.Document(str(path))
    pars = [p for p in d.paragraphs]
    issues: list[ImportIssue] = []
    source_chars = sum(nonspace_len(p.text) for p in pars)
    # Tables are sometimes used for dual dialogue or title pages: count them too.
    table_texts = [c.text for t in d.tables for r in t.rows for c in r.cells]
    source_chars += sum(nonspace_len(x) for x in table_texts)

    nonblank = [p for p in pars if p.text.strip()]
    styled = sum(1 for p in nonblank if p.style is not None and p.style.name.strip().lower() in STYLE_MAP)
    use_styles = nonblank and styled / len(nonblank) >= 0.5

    elements: list[RawElement] = []
    if use_styles:
        seen_heading = False
        for p in nonblank:
            name = p.style.name.strip().lower() if p.style is not None else ""
            etype = STYLE_MAP.get(name)
            conf = 1.0
            if etype == ElementType.SCENE_HEADING:
                seen_heading = True
            if etype is None:
                # Unstyled text before the first heading is the title page.
                etype, conf = (ElementType.TITLE_PAGE, 0.8) if not seen_heading else (ElementType.UNKNOWN, 0.3)
            elements.append(RawElement(etype, " ".join(p.text.split()), p.text,
                                       classification_confidence=conf,
                                       line_count=max(1, p.text.count("\n") + 1)))
    else:
        indents = [_indent_in(p) for p in nonblank]
        # Action margin = smallest indent used by a fair share of paragraphs.
        base = min((i for i in indents if sum(1 for j in indents if abs(j - i) < 0.15) >= max(2, len(indents) * 0.05)),
                   default=0.0)
        vls = [
            VisualLine(page_index=0, text=" ".join(p.text.split()), raw_text=p.text,
                       offset_in=_indent_in(p) - base, gap_before=True)
            for p in nonblank
        ]
        elements = classify_lines(vls)
        for e in elements:
            e.page_index = None

    for t in table_texts:
        if t.strip():
            elements.append(RawElement(ElementType.UNKNOWN, " ".join(t.split()), t,
                                       classification_confidence=0.2))
    if table_texts and any(t.strip() for t in table_texts):
        issues.append(ImportIssue("docx_tables",
                                  "Document contains tables (dual dialogue or title page?). "
                                  "Their text was imported as UNKNOWN elements at the end — review placement.",
                                  Severity.WARNING))

    return ImportResult(
        source_format=SourceFormat.DOCX,
        elements=elements,
        issues=issues,
        source_char_count=source_chars,
        needs_pagination=True,
        title=d.core_properties.title or None,
    )
