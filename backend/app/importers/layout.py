"""Element typing for layout-based sources (PDF, DOCX without screenplay styles).

Screenplays use fixed indents. Measured from the action margin (standard values):
    action / heading   +0.0"      dialogue        +1.0"
    parenthetical      +1.6"      character cue   +2.2"
    transition         +4.0" or right-aligned
Bands below are deliberately wide to tolerate different software and scans.
Position and content must agree; when they don't, confidence drops and the
element is surfaced for review rather than silently typed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..models.enums import ElementType
from ..scene_detection.headings import SCENE_NUMBER, has_heading_prefix, looks_like_unprefixed_heading
from .base import RawElement

TRANSITIONS = {
    "FADE IN:", "FADE IN", "FADE OUT.", "FADE OUT", "FADE TO BLACK.", "FADE TO BLACK",
    "CUT TO BLACK.", "CUT TO BLACK", "INTERCUT WITH:", "INTERCUT:", "INTERCUT",
    "END INTERCUT", "SMASH CUT:", "MATCH CUT:", "JUMP CUT:", "BACK TO:", "THE END",
    "TITLE CARD:", "INTERVAL", "INTERMISSION", "END OF INTERVAL",
}
# "12  OMITTED  12" / "12  HOSPITAL ROOFTOP - NIGHT  12": numbers typed into the text.
_NUMBERED_LINE_RE = re.compile(rf"^({SCENE_NUMBER})\.?\s+(.+?)\s+\1\.?$")
_SHOT_RE = re.compile(
    r"^(ANGLE ON|ANGLE|CLOSE ON|CLOSE UP|CLOSE-UP|CLOSEUP|EXTREME CLOSE|ECU|INSERT|POV|"
    r"BACK TO SCENE|WIDE|WIDE SHOT|ON THE|FLASHBACK|END FLASHBACK|END OF FLASHBACK|"
    r"LATER|MOMENTS LATER|SERIES OF SHOTS|END SERIES|MONTAGE|END MONTAGE|SUPER:|SUPER |"
    r"TITLE:|CONTINUOUS|AERIAL|TRACKING|REVERSE ANGLE|OVER BLACK)\b"
)


@dataclass
class VisualLine:
    page_index: int
    text: str                       # main text, margin numbers removed
    raw_text: str
    offset_in: float                # inches right of the action margin
    y_top: float | None = None
    y_bottom: float | None = None
    left_num: str | None = None
    right_num: str | None = None
    ocr_conf: float | None = None
    gap_before: bool = True         # vertical blank before this line
    dual: bool = False
    col: int = 0                    # dual-dialogue column (0 left, 1 right)


def _is_upper(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) >= 0.9


def _cue_core(t: str) -> str:
    return re.sub(r"\(.*?\)", "", t).replace("^", "").strip()


def is_transition_text(t: str) -> bool:
    s = t.strip()
    return _is_upper(s) and (s.endswith("TO:") or s.rstrip(".:").strip() + ":" in TRANSITIONS
                             or s in TRANSITIONS)


def _classify(vl: VisualLine, prev: ElementType | None, in_paren: bool) -> tuple[ElementType, float]:
    t, off = vl.text.strip(), vl.offset_in
    up = _is_upper(t)

    if vl.dual:  # side-by-side columns: indents are unreliable, type by content
        if t.startswith("(") or in_paren:
            return ElementType.PARENTHETICAL, 0.7
        if up and len(t) <= 40 and prev not in (ElementType.CHARACTER,):
            return ElementType.CHARACTER, 0.7
        return ElementType.DIALOGUE, 0.7

    if (vl.left_num or vl.right_num) and (has_heading_prefix(t) or up):
        return ElementType.SCENE_HEADING, 1.0 if has_heading_prefix(t) else 0.75

    if off < 0.5:
        if has_heading_prefix(t) and (up or " - " in t):
            return ElementType.SCENE_HEADING, 1.0
        if up and _NUMBERED_LINE_RE.match(t):
            return ElementType.SCENE_HEADING, 0.8
        if is_transition_text(t):
            return ElementType.TRANSITION, 0.95
        if up and looks_like_unprefixed_heading(t):
            return ElementType.SCENE_HEADING, 0.6
        if up and len(t) <= 50 and _SHOT_RE.match(t):
            return ElementType.SHOT, 0.9
        return ElementType.ACTION, 1.0

    if off >= 3.3:
        if is_transition_text(t) or (up and len(t) <= 30):
            return ElementType.TRANSITION, 0.9 if is_transition_text(t) else 0.6
        return ElementType.UNKNOWN, 0.3

    if (t.startswith("(") or in_paren) and 0.8 <= off < 2.4:
        return ElementType.PARENTHETICAL, 1.0 if 1.2 <= off < 2.0 else 0.8

    if 1.7 <= off < 3.3 and up and len(t) <= 50 and _cue_core(t):
        return ElementType.CHARACTER, 1.0 if off >= 1.9 else 0.7

    if 0.5 <= off < 1.9:
        dialogue_ctx = prev in (ElementType.CHARACTER, ElementType.PARENTHETICAL, ElementType.DIALOGUE)
        return ElementType.DIALOGUE, 1.0 if dialogue_ctx else 0.5

    if prev in (ElementType.CHARACTER, ElementType.PARENTHETICAL, ElementType.DIALOGUE):
        return ElementType.DIALOGUE, 0.5
    return ElementType.UNKNOWN, 0.3


def _merge_ok(cur: RawElement, vl: VisualLine, etype: ElementType, cur_page: int) -> bool:
    if vl.page_index != cur_page or etype != cur.element_type:
        return False
    if etype in (ElementType.CHARACTER, ElementType.TRANSITION, ElementType.SHOT):
        return False
    if etype == ElementType.SCENE_HEADING:
        # A wrapped heading continues on the next line; a new INT./EXT. line, a numbered
        # line, or anything after "OMITTED" is a new scene even without a blank line.
        return (not vl.gap_before and not (vl.left_num or vl.right_num)
                and not has_heading_prefix(vl.text) and not _NUMBERED_LINE_RE.match(vl.text.strip())
                and "OMITTED" not in cur.text.upper())
    if etype == ElementType.PARENTHETICAL:
        return "(" in cur.text and ")" not in cur.text
    if vl.dual != cur.is_dual_dialogue:
        return False
    return not vl.gap_before


def classify_lines(lines: list[VisualLine]) -> list[RawElement]:
    """Type each visual line and merge consecutive lines into elements."""
    out: list[RawElement] = []
    cur: RawElement | None = None
    cur_page = -1
    confs: list[float] = []
    ocrs: list[float] = []

    def close():
        if cur is not None:
            cur.classification_confidence = min(confs) if confs else 1.0
            cur.ocr_confidence = (sum(ocrs) / len(ocrs)) if ocrs else None

    for vl in lines:
        prev = cur.element_type if cur else None
        in_paren = bool(cur and cur.element_type == ElementType.PARENTHETICAL
                        and "(" in cur.text and ")" not in cur.text and vl.page_index == cur_page)
        etype, conf = _classify(vl, prev, in_paren)
        # Wrapped heading: an ALL-CAPS line at the action margin directly under a heading.
        if (prev == ElementType.SCENE_HEADING and vl.page_index == cur_page and not vl.gap_before
                and vl.offset_in < 0.5 and _is_upper(vl.text) and etype != ElementType.SCENE_HEADING):
            etype, conf = ElementType.SCENE_HEADING, 0.8
        if cur is not None and _merge_ok(cur, vl, etype, cur_page):
            cur.text = f"{cur.text} {vl.text.strip()}".strip()
            cur.raw_text += "\n" + vl.raw_text
            cur.line_count += 1
            cur.y_bottom = vl.y_bottom
            confs.append(conf)
            if vl.ocr_conf is not None:
                ocrs.append(vl.ocr_conf)
            continue
        close()
        cur = RawElement(
            element_type=etype, text=vl.text.strip(), raw_text=vl.raw_text,
            page_index=vl.page_index, y_top=vl.y_top, y_bottom=vl.y_bottom,
            scene_number_left=vl.left_num, scene_number_right=vl.right_num,
            is_dual_dialogue=vl.dual,
        )
        cur_page = vl.page_index
        confs = [conf]
        ocrs = [vl.ocr_conf] if vl.ocr_conf is not None else []
        out.append(cur)
    close()

    # Post-pass: a "cue" not followed by dialogue is centred text (e.g. INTERVAL), not a cue.
    for i, el in enumerate(out):
        if el.element_type == ElementType.CHARACTER:
            nxt = next((o for o in out[i + 1:] if o.element_type != ElementType.PAGE_HEADER), None)
            if nxt is None or nxt.element_type not in (ElementType.DIALOGUE, ElementType.PARENTHETICAL):
                el.element_type = ElementType.ACTION
                el.classification_confidence = min(el.classification_confidence, 0.5)
    return out


def dominant_left(xs: list[float], tolerance: float = 3.0) -> float | None:
    """The action margin: the left-most x position used by a meaningful share of lines."""
    if not xs:
        return None
    buckets = Counter(round(x / tolerance) for x in xs)
    need = max(2, int(0.03 * len(xs)))
    candidates = [b for b, n in buckets.items() if n >= need]
    if not candidates:
        return min(xs)
    b = min(candidates)
    near = [x for x in xs if round(x / tolerance) == b]
    return sum(near) / len(near)
