"""Scene heading parsing. Pure functions, no LLM.

    "23A  INT. ARJUN'S HOUSE - KITCHEN - NIGHT  23A"
      -> number 23A/23A, INT, location "ARJUN'S HOUSE - KITCHEN", NIGHT

Whatever cannot be parsed cleanly is reported in `flags` — never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.enums import RELATIVE_TIMES, HeadingStatus, IntExt, TimeOfDay

# Scene numbers: 23, 23A, A23, AA23, 23AB, 23.1 is NOT supported on purpose (ambiguous with prices etc.)
SCENE_NUMBER = r"[A-Z]{0,2}\d{1,4}[A-Z]{0,3}"
# OCR often adds stray brackets/pipes around margin numbers ("47]", "|12", "(23)").
_NUM_TOKEN_RE = re.compile(rf"^[\[(|]?({SCENE_NUMBER})[\])|.,:;]*$")
_LEADING_NUM_RE = re.compile(rf"^\s*[\[(|]?({SCENE_NUMBER})[\])|.]*(?=\s)\s*")
_TRAILING_NUM_RE = re.compile(rf"(\s+)[\[(|]?({SCENE_NUMBER})[\])|.]*\s*$")
_FOUNTAIN_NUM_RE = re.compile(r"\s*#([\w.\-]+)#\s*$")
_REVISION_MARK_RE = re.compile(r"\s*\*+\s*$")

# Order matters: compound forms first. Lookahead stops INTERCUT / EXTREME matching.
_PREFIX_RE = re.compile(
    r"""^(
        INT(?:ERIOR)?\.?\s*[/\-–]\s*EXT(?:ERIOR)?\.? |
        EXT(?:ERIOR)?\.?\s*[/\-–]\s*INT(?:ERIOR)?\.? |
        I\s*/\s*E\.? | E\s*/\s*I\.? |
        INT(?:ERIOR)?\.? |
        EXT(?:ERIOR)?\.? |
        EST\.?
    )(?=[\s.\-–:]|$)[\s.\-–:]*""",
    re.IGNORECASE | re.VERBOSE,
)

# Separators between heading parts: " - ", " -- ", " – ", " — ".
_SEP_RE = re.compile(r"\s+[-–—]{1,2}\s+|\s*--\s*")

_TIME_WORDS: dict[str, TimeOfDay] = {
    "DAY": TimeOfDay.DAY,
    "NIGHT": TimeOfDay.NIGHT,
    "DAWN": TimeOfDay.DAWN,
    "DUSK": TimeOfDay.DUSK,
    "SUNRISE": TimeOfDay.SUNRISE,
    "SUNSET": TimeOfDay.SUNSET,
    "MORNING": TimeOfDay.MORNING,
    "AFTERNOON": TimeOfDay.AFTERNOON,
    "EVENING": TimeOfDay.EVENING,
    "CONTINUOUS": TimeOfDay.CONTINUOUS,
    "CONTINUOUS ACTION": TimeOfDay.CONTINUOUS,
    "CONT": TimeOfDay.CONTINUOUS,
    "CONT.": TimeOfDay.CONTINUOUS,
    "CONTD": TimeOfDay.CONTINUOUS,
    "SAME": TimeOfDay.SAME,
    "SAME TIME": TimeOfDay.SAME,
    "LATER": TimeOfDay.LATER,
    "MOMENTS LATER": TimeOfDay.MOMENTS_LATER,
    "MOMENT LATER": TimeOfDay.MOMENTS_LATER,
    "A MOMENT LATER": TimeOfDay.MOMENTS_LATER,
    "MINUTES LATER": TimeOfDay.MOMENTS_LATER,
}
# Words that make a segment *look like* a time even if we don't map it exactly
# ("LATE NIGHT", "NEXT MORNING", "PRE-DAWN") -> OTHER + flag, raw kept.
_TIME_HINTS = re.compile(
    r"\b(DAY|NIGHT|DAWN|DUSK|SUNRISE|SUNSET|MORNING|AFTERNOON|EVENING|NOON|MIDNIGHT|"
    r"LATER|CONTINUOUS|SAME|TWILIGHT|MAGIC HOUR)\b"
)

INT_EXT_MAP = {"INT": IntExt.INT, "EXT": IntExt.EXT, "EST": IntExt.EXT}


@dataclass
class ParsedHeading:
    text: str                                  # heading without numbers / marks
    scene_number_left: str | None = None
    scene_number_right: str | None = None
    int_ext: IntExt = IntExt.UNKNOWN
    int_ext_raw: str | None = None
    location_raw: str | None = None
    time_of_day: TimeOfDay = TimeOfDay.UNSPECIFIED
    time_of_day_raw: str | None = None
    is_omitted: bool = False
    status: HeadingStatus = HeadingStatus.OK
    confidence: float = 1.0
    flags: list[str] = field(default_factory=list)

    @property
    def scene_number(self) -> str | None:
        return self.scene_number_left or self.scene_number_right


def is_scene_number_token(token: str) -> bool:
    return bool(_NUM_TOKEN_RE.match(token.strip().upper()))


def clean_scene_number(token: str) -> str:
    m = _NUM_TOKEN_RE.match(token.strip().upper())
    return m.group(1) if m else token.strip()


def has_heading_prefix(text: str) -> bool:
    t = _LEADING_NUM_RE.sub("", text.strip(), count=1)
    return bool(_PREFIX_RE.match(t))


def looks_like_unprefixed_heading(text: str) -> bool:
    """ALL-CAPS line like "HOSPITAL ROOFTOP - NIGHT": no INT/EXT, but ends in a time."""
    t = text.strip()
    if not t or t != t.upper() or len(t) > 90:
        return False
    parts = _SEP_RE.split(t)
    if len(parts) < 2 or not parts[0].strip():
        return False
    return _classify_time(parts[-1])[0] is not None


def _classify_time(segment: str) -> tuple[TimeOfDay | None, bool]:
    """-> (time, exact). exact=False means OTHER via a hint word."""
    seg = segment.strip().upper()
    base = re.sub(r"\s*\(.*?\)\s*", " ", seg).strip()   # "DAY (FLASHBACK)" -> "DAY"
    base = base.rstrip(".") if base not in ("CONT.",) else base
    if base in _TIME_WORDS:
        return _TIME_WORDS[base], True
    if base and len(base.split()) <= 3 and _TIME_HINTS.search(base):
        return TimeOfDay.OTHER, False
    return None, False


def parse_heading(
    raw: str,
    margin_left: str | None = None,
    margin_right: str | None = None,
) -> ParsedHeading:
    """Parse a heading line. `margin_*` are numbers already found by layout (PDF)."""
    text = " ".join(raw.replace("\t", "   ").split()) if raw else ""
    spaced = raw.replace("\t", "   ").strip() if raw else ""
    if spaced.startswith(".") and not spaced.startswith(".."):  # Fountain forced heading
        spaced = spaced[1:]
        text = text[1:]
    p = ParsedHeading(text=text)
    flags = p.flags

    # Fountain "#12A#" and revision asterisks.
    m = _FOUNTAIN_NUM_RE.search(spaced)
    if m:
        p.scene_number_left = p.scene_number_right = m.group(1).upper()
        spaced = spaced[: m.start()]
    spaced = _REVISION_MARK_RE.sub("", spaced)

    # Numbers typed into the text itself ("12  INT. HOUSE - DAY  12").
    m = _LEADING_NUM_RE.match(spaced)
    leading = None
    if m and not _PREFIX_RE.match(spaced):
        leading = m.group(1).upper()
        spaced = spaced[m.end():]
    m = _TRAILING_NUM_RE.search(spaced)
    if m:
        # Accept a trailing number only if the line also had a leading one, or it is
        # set apart by a wide gap — otherwise "INT. APARTMENT 4B" would lose "4B".
        if leading is not None or len(m.group(1)) >= 3:
            p.scene_number_right = m.group(2).upper()
            spaced = spaced[: m.start()]
    if leading:
        p.scene_number_left = leading
    if margin_left:
        p.scene_number_left = clean_scene_number(margin_left)
    if margin_right:
        p.scene_number_right = clean_scene_number(margin_right)

    body = " ".join(spaced.split())
    p.text = body
    upper = body.upper()

    if upper.startswith("OMITTED") or upper in ("OMIT", "OMITTED", "SCENE OMITTED"):
        p.is_omitted = True
        p.time_of_day = TimeOfDay.UNSPECIFIED
        return p

    m = _PREFIX_RE.match(body)
    if m:
        prefix = m.group(1)
        p.int_ext_raw = prefix.strip()
        letters = re.sub(r"[^A-Z/]", "", prefix.upper())
        if "/" in letters or re.search(r"INT.*EXT|EXT.*INT", letters):
            p.int_ext = IntExt.INT_EXT
        else:
            p.int_ext = INT_EXT_MAP.get(letters[:3], IntExt.UNKNOWN)
        rest = body[m.end():]
    else:
        p.int_ext = IntExt.UNKNOWN
        p.status = HeadingStatus.AMBIGUOUS
        flags.append("no_int_ext")
        rest = body

    parts = [s.strip() for s in _SEP_RE.split(rest) if s.strip()]
    time_idx = None
    for i in range(len(parts) - 1, 0, -1):
        if _classify_time(parts[i])[0] is not None:
            time_idx = i
            break
    if time_idx is not None and time_idx >= 2:
        # "NIGHT - CONTINUOUS": keep the absolute time, note both.
        last, prev = _classify_time(parts[time_idx])[0], _classify_time(parts[time_idx - 1])[0]
        if last in RELATIVE_TIMES and prev is not None and prev not in RELATIVE_TIMES:
            time_idx -= 1
            flags.append("multiple_times")
    if time_idx is not None:
        tod, exact = _classify_time(parts[time_idx])
        p.time_of_day = tod
        p.time_of_day_raw = " - ".join(parts[time_idx:])
        if not exact:
            flags.append("unrecognised_time")
        if time_idx < len(parts) - 1 and "multiple_times" not in flags:
            flags.append("text_after_time")
        loc_parts = parts[:time_idx]
    else:
        # "INT. HOUSE DAY" — time not dash-separated. Accept the last word, flagged.
        words = rest.split()
        tod = _TIME_WORDS.get(words[-1].upper().strip(".")) if len(words) >= 2 else None
        if tod is not None:
            p.time_of_day = tod
            p.time_of_day_raw = words[-1]
            flags.append("time_not_separated")
            loc_parts = [" ".join(words[:-1])]
        else:
            p.time_of_day = TimeOfDay.UNSPECIFIED
            flags.append("no_time_of_day")
            loc_parts = parts

    p.location_raw = " - ".join(loc_parts).strip(" .-") or None
    if not p.location_raw:
        flags.append("no_location")
        p.status = HeadingStatus.AMBIGUOUS

    if p.scene_number_left and p.scene_number_right and p.scene_number_left != p.scene_number_right:
        flags.append("number_mismatch")

    penalties = {
        "no_int_ext": 0.4, "no_time_of_day": 0.2, "unrecognised_time": 0.2,
        "time_not_separated": 0.15, "no_location": 0.4, "text_after_time": 0.05,
        "number_mismatch": 0.1, "multiple_times": 0.05,
    }
    p.confidence = max(0.05, 1.0 - sum(penalties.get(f, 0.1) for f in flags))
    return p


_NUM_KEY_RE = re.compile(r"^([A-Z]*)(\d+)([A-Z]*)$")


def scene_number_sort_key(num: str) -> tuple[int, int, str] | None:
    """'23' -> (23, 0, ''), '23A' -> (23, 1, 'A'), 'A23' -> (22, 9, 'A') (inserted before 23)."""
    m = _NUM_KEY_RE.match(num.upper())
    if not m:
        return None
    pre, n, suf = m.group(1), int(m.group(2)), m.group(3)
    if pre:
        return (n - 1, 9, pre)
    return (n, 1 if suf else 0, suf)
