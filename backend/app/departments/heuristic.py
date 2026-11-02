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
from ..extraction.heuristic_extractor import NUMBER_WORDS, VERSION_WORDS
from ..models.enums import ElementType
from .base import DepartmentSpec, ItemOut, SceneContext

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
TENS = {"FIFTEEN": 15, "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50, "SIXTY": 60, "HUNDRED": 100,
        "ELEVEN": 11, "TWELVE": 12}
_WORDS = {**NUMBER_WORDS, **TENS, "ONE": 1, "A": 1}
_NUMWORD = "|".join(sorted((w for w in _WORDS if w != "A"), key=len, reverse=True))
_NUM_RE = re.compile(rf"\b((?:{_NUMWORD}|\d+)(?:\s+(?:HUNDRED|THOUSAND))?|A DOZEN|A HUNDRED|DOZENS OF)\s+"
                     r"((?:[\w'-]+\s+){0,2})$", re.I)
_SINGULAR_RE = re.compile(r"\b(a|an|his|her|their|its|the|one)\s+((?:[\w'-]+\s+){0,2})$", re.I)
_POSSESSIVE_RE = re.compile(r"\b([A-Z][\w-]*)['’][sS]\s+((?:[\w-]+\s+){0,2})$")
SINGULAR_S = {"bus", "glass", "dress", "class", "chess", "grass", "boss", "cross", "series",
              # always-plural nouns that mean one object
              "scissors", "handcuffs", "trousers", "pants", "jeans", "glasses", "spectacles", "pliers",
              "goggles", "binoculars", "bell-bottoms", "shorts"}


def _number(word: str) -> int | None:
    parts = word.upper().split()
    if parts == ["A", "DOZEN"]:
        return 12
    if parts[0] == "DOZENS":
        return None                       # "dozens of" is not a number
    base = int(parts[0]) if parts[0].isdigit() else _WORDS.get(parts[0])
    if base is None:
        return None
    for mult in parts[1:]:
        base *= {"HUNDRED": 100, "THOUSAND": 1000}.get(mult, 1)
    return base


IRREGULAR_PLURALS = {"men", "women", "children", "people", "police", "staff", "cattle", "sheep", "geese", "mice",
                     "folk", "crew", "personnel"}
COLLECTIVES = {"crowd", "mob", "group", "band", "gang", "army", "troupe", "team", "family", "audience", "flock",
               "herd", "pack", "procession", "congregation"}


def _plural(noun: str) -> bool:
    last = noun.split()[-1].lower()
    if last in IRREGULAR_PLURALS or last in COLLECTIVES:
        return True
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
        # A bare token maps to the SHORTEST name containing it: "RAGHAV" -> RAGHAV, not OLD RAGHAV.
        for c in sorted(cast, key=len):
            for tok in c.split():
                if len(tok) > 2 and tok not in VERSION_WORDS:
                    self.cast_tokens.setdefault(tok, c)

    def _matches(self, spec: DepartmentSpec, sentence: str) -> list[tuple]:
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
        # join adjacent matches of the same category ("Royal Enfield" "motorcycle"), and
        # "X of Y" pairs ("a crowd of villagers") into one item named after Y.
        joined: list[list] = []   # [start, end, category, name_start, name_end]
        for s, e, c in chosen:
            gap = sentence[joined[-1][1]:s].strip().lower() if joined else None
            if joined and joined[-1][2] == c and gap == "":
                joined[-1][1] = e
                joined[-1][4] = e
            elif joined and gap == "of":
                joined[-1][1], joined[-1][2], joined[-1][3], joined[-1][4] = e, c, s, e
            else:
                joined.append([s, e, c, s, e])
        return [tuple(j) for j in joined]

    def extract(self, scene_text: str, ctx: SceneContext) -> dict[str, list[ItemOut]]:
        # SONG: lines are read exactly by code (sound_music.derive), not by these rules.
        action = " ".join(e.text for e in ctx.elements
                          if e.element_type in (ElementType.ACTION, ElementType.SCENE_HEADING)
                          and not re.match(r"\s*SONG\s*[:\-]", e.text, re.I))
        sentences = [s for s in _SENT_RE.split(action) if s.strip()]
        out: dict[str, list[ItemOut]] = {}
        for spec in self.specs:
            found: dict[str, ItemOut] = {}
            for sent in sentences:
                for s, e, cat, ns, ne in self._matches(spec, sent):
                    noun = normalize_name(cat.replace("_", " ") if cat in spec.offline_category_names
                                          else sent[ns:ne])
                    before = sent[:s]
                    qty, qtext = None, None
                    m = _NUM_RE.search(before)
                    if m and _number(m.group(1)) is not None:
                        qty, qtext = _number(m.group(1)), (m.group(0) + sent[s:e]).strip()
                    elif spec.quantity_matters:
                        # Headcounts are never implied: only a stated number counts.
                        art = _SINGULAR_RE.search(before)
                        qtext = ((art.group(0) if art else "") + sent[s:e]).strip()
                    elif not _plural(noun):
                        qty = 1
                    by_category = cat in spec.offline_category_names
                    if by_category:
                        qty, qtext = None, None          # events/effects are not counted units
                    owner = None
                    pm = None if by_category else _POSSESSIVE_RE.search(before)
                    who = [c for c in self.cast if re.search(rf"\b{re.escape(c)}\b", sent, re.IGNORECASE)
                           or any(re.search(rf"\b{re.escape(t)}\b", sent, re.IGNORECASE)
                                  for t in c.split() if len(t) > 2 and self.cast_tokens.get(t) == c)]
                    if pm and pm.group(1).upper() in self.cast_tokens:
                        owner = self.cast_tokens[pm.group(1).upper()]
                    if spec.name_with_holder and (owner or who):
                        holder = owner or who[0]
                        owner = holder
                        name = f"{holder} — {noun}"
                    elif owner:
                        name = f"{pm.group(1).upper()}'S {noun}"
                    else:
                        name = noun
                    if name in found:
                        continue
                    chars = []
                    if owner and spec.owner_roles:
                        chars.append({"name": owner, "role": spec.owner_roles[0]})
                    if spec.default_role:
                        chars += [{"name": c, "role": spec.default_role} for c in who[:2]
                                  if not (c == owner and spec.owner_roles)]
                    found[name] = ItemOut(
                        name=name, category=cat, description=sent.strip()[:200], quantity_text=qtext,
                        quantity_min=qty, quantity_max=qty, characters=chars,
                        details=spec.offline_details(sent) if spec.offline_details else {},
                        evidence=sent[s:e] if not qtext else qtext, confidence=0.5)
            out[spec.key.value] = list(found.values())
        return out
