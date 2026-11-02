"""Final Draft (.fdx) importer. FDX is XML; every paragraph carries its Type,
scene headings carry their Number, and Final Draft often stores its own scene
length and page in <SceneProperties Length="1 5/8" Page="3">.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from ..models.enums import ElementType, Severity, SourceFormat
from .base import ImportIssue, ImportResult, RawElement, SceneLengthHint, nonspace_len

TYPE_MAP = {
    "scene heading": ElementType.SCENE_HEADING,
    "action": ElementType.ACTION,
    "general": ElementType.ACTION,
    "character": ElementType.CHARACTER,
    "dialogue": ElementType.DIALOGUE,
    "parenthetical": ElementType.PARENTHETICAL,
    "transition": ElementType.TRANSITION,
    "shot": ElementType.SHOT,
    "cast list": ElementType.ACTION,
    "lyrics": ElementType.DIALOGUE,
    "new act": ElementType.NON_PRINTING,
    "end of act": ElementType.NON_PRINTING,
}


def parse_eighths(length: str | None) -> int | None:
    """'1 5/8' -> 13, '3/8' -> 3, '2' -> 16."""
    if not length:
        return None
    m = re.fullmatch(r"\s*(?:(\d+)\s*)?(?:(\d)\s*/\s*8)?\s*", length)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    return int(m.group(1) or 0) * 8 + int(m.group(2) or 0)


def _para_text(p) -> str:
    return "".join(t.text or "" for t in p.findall("Text"))


def import_fdx(path: str | Path) -> ImportResult:
    tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True))
    root = tree.getroot()
    elements: list[RawElement] = []
    issues: list[ImportIssue] = []
    hints: dict[int, SceneLengthHint] = {}
    title = None

    # Title page first (non-scene).
    for p in root.iterfind("./TitlePage/Content/Paragraph"):
        t = _para_text(p)
        if t.strip():
            title = title or " ".join(t.split())
            elements.append(RawElement(ElementType.TITLE_PAGE, " ".join(t.split()), t))

    content = root.find("Content")
    if content is None:
        issues.append(ImportIssue("fdx_no_content", "FDX file has no <Content>.", Severity.ERROR))
        return ImportResult(SourceFormat.FDX, elements, issues=issues, needs_pagination=True)

    # Independent count of every paragraph's text, to prove nothing was skipped.
    source_chars = sum(nonspace_len(t.text or "") for t in root.iterfind(".//Paragraph/Text"))

    def handle(p, dual: bool = False):
        raw = _para_text(p)
        ptype = (p.get("Type") or "").strip().lower()
        etype = TYPE_MAP.get(ptype)
        conf = 1.0
        if etype is None:
            etype = ElementType.UNKNOWN
            conf = 0.3
            issues.append(ImportIssue("fdx_unknown_type",
                                      f"Unknown paragraph type {p.get('Type')!r}: {raw[:60]!r}",
                                      Severity.WARNING, element_index=len(elements)))
        if not raw.strip():
            return
        el = RawElement(etype, " ".join(raw.split()), raw, classification_confidence=conf,
                        is_dual_dialogue=dual, line_count=max(1, raw.count("\n") + 1))
        if etype == ElementType.SCENE_HEADING:
            num = p.get("Number")
            if num:
                el.scene_number_left = el.scene_number_right = num.strip().upper()
            sp = p.find("SceneProperties")
            if sp is not None:
                hints[len(elements)] = SceneLengthHint(parse_eighths(sp.get("Length")), sp.get("Page"))
        elements.append(el)

    for p in content.iterfind("Paragraph"):
        dd = p.find("DualDialogue")
        if dd is not None:
            for inner in dd.iterfind("Paragraph"):
                handle(inner, dual=True)
        else:
            handle(p)

    return ImportResult(
        source_format=SourceFormat.FDX,
        elements=elements,
        issues=issues,
        source_char_count=source_chars,
        length_hints=hints,
        needs_pagination=True,
        title=title,
    )
