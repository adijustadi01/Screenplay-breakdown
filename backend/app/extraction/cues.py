"""Deterministic facts from dialogue cues — computed in code, never by the LLM.

    "MEERA (V.O.)"       -> MEERA, voice_only
    "KABIR (O.S.)"       -> KABIR, off_screen
    "VIKRAM (CONT'D)"    -> VIKRAM (CONT'D is not an extension that matters)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.enums import ElementType, Presence

_EXT_RE = re.compile(r"\(([^)]*)\)")
VOICE_EXT = {"V.O.", "VO", "V/O", "V.O", "VOICE OVER", "VOICE", "ON PHONE", "ON THE PHONE", "OVER PHONE",
             "FILTERED", "ON RADIO", "ON TV", "PRE-LAP", "PRELAP"}
OFFSCREEN_EXT = {"O.S.", "OS", "O.S", "O.C.", "OC", "O.C", "OFF", "OFF SCREEN", "OFFSCREEN"}
IGNORED_EXT = {"CONT'D", "CONT’D", "CONTD", "CONT.", "CONT", "CONTINUING", "MORE"}


def normalize_name(name: str) -> str:
    """Canonical key: upper case, single spaces, curly quotes straightened, no trailing punctuation."""
    s = name.replace("’", "'").replace("‘", "'").upper()
    s = re.sub(r"\s+", " ", s).strip(" .,:;-")
    return s


def parse_cue(cue_text: str) -> tuple[str, set[str]]:
    exts = {e.strip().upper() for e in _EXT_RE.findall(cue_text)}
    name = _EXT_RE.sub("", cue_text).replace("^", "")
    return normalize_name(name), exts


_DUAL_RE = re.compile(r"\s*(?:/|&|\+)\s*|\s+AND\s+")


def split_cue(name: str) -> list[str]:
    """A shared cue names every speaker: "SAVITRI / KAMLA", "RAJ & SIMRAN", "A AND B" -> both.
    Each of them says the line, so each gets the dialogue block."""
    parts = [p.strip(" .,") for p in _DUAL_RE.split(name)]
    if len(parts) >= 2 and all(len(p) >= 2 and p[0].isalpha() for p in parts):
        return [normalize_name(p) for p in parts]
    return [name]


def cue_presence(exts: set[str]) -> Presence:
    if exts & VOICE_EXT:
        return Presence.VOICE_ONLY
    if exts & OFFSCREEN_EXT:
        return Presence.OFF_SCREEN
    return Presence.PRESENT


@dataclass
class CueFacts:
    name: str
    dialogue_blocks: int = 0
    presences: set[Presence] = field(default_factory=set)
    first_element_id: int | None = None
    cue_texts: list[str] = field(default_factory=list)

    @property
    def presence(self) -> Presence:
        # Seen speaking on screen at any point beats O.S. beats V.O.
        for p in (Presence.PRESENT, Presence.OFF_SCREEN, Presence.VOICE_ONLY):
            if p in self.presences:
                return p
        return Presence.PRESENT


def scene_cues(elements) -> dict[str, CueFacts]:
    """elements: ScriptElement rows (or anything with element_type/text/id) of ONE scene."""
    out: dict[str, CueFacts] = {}
    for e in elements:
        if e.element_type != ElementType.CHARACTER:
            continue
        whole, exts = parse_cue(e.text)
        for name in split_cue(whole):
            if not name:
                continue
            f = out.setdefault(name, CueFacts(name, first_element_id=e.id))
            f.dialogue_blocks += 1
            f.presences.add(cue_presence(exts))
            f.cue_texts.append(e.text)
    return out
