"""Offline department extractor: keyword rules over action lines. A baseline only —
low confidence, everything it finds is flagged for review.

Per sentence: the longest matching phrase wins, adjacent matches of the same category
are joined ("Royal Enfield" + "motorcycle"), a possessive names the owner
("Khan's police jeep" -> KHAN'S POLICE JEEP), a singular article/possessive means 1,
and a plural without a stated number stays unknown.
"""
from __future__ import annotations

import re

from ..extraction.cues import normalize_name
from ..extraction.heuristic_extractor import NUMBER_WORDS
from ..models.enums import ElementType
from .base import DepartmentSpec, ItemOut, SceneContext

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_NUM_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r"|\d+|A DOZEN|DOZENS OF)\s+((?:[\w'-]+\s+){0,2})$", re.I)
_SINGULAR_RE = re.compile(r"\b(a|an|his|her|their|its|the|one)\s+((?:[\w'-]+\s+){0,2})$", re.I)
_POSSESSIVE_RE = re.compile(r"\b([A-Z][\w-]*)['’]s\s+((?:[\w-]+\s+){0,2})$")
SINGULAR_S = {"bus", "glass", "dress", "class", "chess", "grass", "boss", "cross", "series"}


def _number(word: str) -> int | None:
    w = word.upper()
    if w.isdigit():
        return int(w)
    if w in ("A DOZEN",):
        return 12
    return NUMBER_WORDS.get(w)


def _plural(noun: str) -> bool:
    last = noun.split()[-1].lower()
    return last.endswith("s") and last not in SINGULAR_S and not last.endswith("ss")


class DepartmentHeuristicExtractor:
    name = "heuristic"
    model = None

    def __init__(self, specs: list[DepartmentSpec]):
        self.specs = specs
        self.prompt_version = "dept-heuristic-v2+" + ",".join(s.key.value for s in specs)
        self.cast: list[str] = []
        self.cast_tokens: dict[str, str] = {}

    def set_script_context(self, cast: list[str], locations: list[str]) -> None:
        self.cast = sorted(cast, key=len, reverse=True)
        for c in self.cast:
            for tok in c.split():
                if len(tok) > 2:
                    self.cast_tokens.setdefault(tok, c)

    def _matches(self, spec: DepartmentSpec, sentence: str) -> list[tuple[int, int, str]]:
        found = []
        for cat, words in spec.lexicon.items():
            for w in words:
                for m in re.finditer(rf"\b{w}\b", sentence, re.IGNORECASE):
                    found.append((m.start(), m.end(), cat))
        # longest first, drop overlaps
        found.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
        chosen: list[tuple[int, int, str]] = []
        for s, e, c in found:
            if all(e <= cs or s >= ce for cs, ce, _ in chosen):
                chosen.append((s, e, c))
        chosen.sort()
        # join adjacent matches of the same category ("Royal Enfield" "motorcycle")
        joined: list[list] = []
        for s, e, c in chosen:
            if joined and joined[-1][2] == c and sentence[joined[-1][1]:s].strip() == "":
                joined[-1][1] = e
            else:
                joined.append([s, e, c])
        return [tuple(j) for j in joined]

    def extract(self, scene_text: str, ctx: SceneContext) -> dict[str, list[ItemOut]]:
        action = " ".join(e.text for e in ctx.elements
                          if e.element_type in (ElementType.ACTION, ElementType.SCENE_HEADING))
        sentences = [s for s in _SENT_RE.split(action) if s.strip()]
        out: dict[str, list[ItemOut]] = {}
        for spec in self.specs:
            found: dict[str, ItemOut] = {}
            for sent in sentences:
                for s, e, cat in self._matches(spec, sent):
                    noun = normalize_name(sent[s:e])
                    before = sent[:s]
                    qty, qtext = None, None
                    m = _NUM_RE.search(before)
                    if m:
                        qty, qtext = _number(m.group(1)), (m.group(0) + sent[s:e]).strip()
                    elif _SINGULAR_RE.search(before) and not _plural(noun):
                        qty = 1
                    elif not _plural(noun):
                        qty = 1
                    owner = None
                    pm = _POSSESSIVE_RE.search(before)
                    if pm and pm.group(1).upper() in self.cast_tokens:
                        owner = self.cast_tokens[pm.group(1).upper()]
                        name = f"{pm.group(1).upper()}'S {noun}"
                    else:
                        name = noun
                    if name in found:
                        continue
                    who = [c for c in self.cast if re.search(rf"\b{re.escape(c)}\b", sent, re.IGNORECASE)
                           or any(re.search(rf"\b{re.escape(t)}\b", sent, re.IGNORECASE)
                                  for t in c.split() if len(t) > 2 and self.cast_tokens.get(t) == c)]
                    chars = []
                    if owner and spec.owner_roles:
                        chars.append({"name": owner, "role": spec.owner_roles[0]})
                    if spec.default_role:
                        chars += [{"name": c, "role": spec.default_role} for c in who[:2]
                                  if not (c == owner and spec.owner_roles)]
                    found[name] = ItemOut(
                        name=name, category=cat, description=sent.strip()[:200], quantity_text=qtext,
                        quantity_min=qty, quantity_max=qty, characters=chars,
                        evidence=sent[s:e] if not qtext else qtext, confidence=0.5)
            out[spec.key.value] = list(found.values())
        return out
