"""Offline, deterministic extractor (no API key needed).

A baseline, not a substitute for the LLM: it finds silent characters by name
matching in action lines and mentions by name matching in dialogue. It cannot
tell a photograph from a person, or resolve "Papa" to a character, so its
non-cue guesses carry low confidence and are flagged for review.
"""
from __future__ import annotations

import re

from ..lexicon import entry, is_english, is_people
from ..models.enums import ElementType
from .cues import normalize_name
from .schema import CharacterOut, LocationOut, SceneExtraction

NUMBER_WORDS = {"TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8,
                "NINE": 9, "TEN": 10, "TWELVE": 12, "TWENTY": 20}
MODIFIERS = {"MOVING", "FLASHBACK", "ESTABLISHING", "CONTINUOUS", "PRESENT DAY", "DREAM", "MONTAGE",
             "STOCK", "INTERCUT", "B&W", "BLACK AND WHITE", "SLOW MOTION"}
TITLES = {"DR.", "DR", "MR.", "MR", "MRS.", "MRS", "MS.", "MS", "INSPECTOR", "NURSE", "OFFICER",
          "CONSTABLE", "SERGEANT", "CAPTAIN", "PROFESSOR", "UNCLE", "AUNTY", "AUNT", "COACH", "MASTER", "DOCTOR",
          "COLONEL", "MAJOR", "JUDGE", "SARDAR", "SETH", "PANDIT", "THAKUR", "SWAMI", "USTAD", "PRINCIPAL",
          "COMMISSIONER", "MINISTER", "SUB-INSPECTOR", "HAVILDAR", "MISS", "LALA"}
# "a photograph of X", "thinks of X": X is talked about or shown, not standing in the scene.
_DEPICTED_RE = r"(?:photo(?:graph)?s?|pictures?|portraits?|posters?|statues?|paintings?|images?|idols?|sketch(?:es)?|" \
               r"memor(?:y|ies)|thinks?|dreams?|news|letters?|voice|name|talk(?:s|ing)?|hears?|stor(?:y|ies))" \
               r"\s+(?:of|about|from)\s+(?:the\s+|a\s+|young\s+|old\s+|late\s+)?$"
VERSION_WORDS = {"YOUNG", "OLD", "OLDER", "YOUNGER", "LITTLE", "ADULT", "TEENAGE", "TEEN", "BABY", "CHILD",
                 "KID", "ELDERLY", "AGED", "GROWN", "GROWN-UP", "MIDDLE-AGED", "INFANT", "TODDLER"}
NOT_NAMES = {"INT", "EXT", "CONTINUED", "CUT", "FADE", "THE", "END", "DAY", "NIGHT", "POV", "V.O.", "O.S."}

_INTRO_RE = re.compile(r"\b((?:[A-Z][A-Z.'’\-]+)(?:\s+[A-Z][A-Z.'’\-0-9]+){0,3})\s*\((\d{1,3}s?|[A-Z]?\d+[A-Za-z]*)\)")
_GROUP_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r"|\d+)\s+([A-Z]{3,}(?:\s+[A-Z]{3,})?)\b")
_ARTICLE_CAPS_RE = re.compile(r"\b(?:A|An|The)\s+([A-Z]{3,}(?:\s+[A-Z]{2,}){0,2})\b")
# Screenwriters put props and sounds in CAPS too ("a MIRROR", "The GUNSHOT"), so "A/The + CAPS" only
# counts as a new character when the built-in dictionary says it names people ("A CONSTABLE",
# "A WARD BOY", "THE GOONS"). See app/lexicon.


def intro_names(action_texts: list[str]) -> set[str]:
    """Names introduced in CAPS in action lines: 'DADI (80s)', 'A CONSTABLE'."""
    names = set()
    for t in action_texts:
        for m in _INTRO_RE.finditer(t):
            names.add(normalize_name(m.group(1)))
        for m in _ARTICLE_CAPS_RE.finditer(t):
            phrase = m.group(1)
            if phrase not in NOT_NAMES and is_people(phrase):
                names.add(normalize_name(phrase))
    return {n for n in names if n not in NOT_NAMES}


def _variants(name: str) -> list[str]:
    toks = name.split()
    out = [name]
    if len(toks) > 1 and toks[0] in TITLES:
        out.append(" ".join(toks[1:]))
    return out


def split_location(location_raw: str | None) -> tuple[str | None, str | None, list[str]]:
    if not location_raw:
        return None, None, []
    parts = [p.strip() for p in re.split(r"\s+[-–—]+\s+", location_raw) if p.strip()]
    mods = [p for p in parts if p.upper() in MODIFIERS]
    places = [p for p in parts if p.upper() not in MODIFIERS]
    if not places:
        return None, None, mods
    return places[0], (" - ".join(places[1:]) or None), mods


class HeuristicExtractor:
    name = "heuristic"
    model = None
    prompt_version = "heuristic-v1"

    def __init__(self):
        self.known: list[str] = []
        self.speakers: set[str] = set()
        self.short: dict[str, list[str]] = {}

    def set_script_context(self, character_names, location_names, introduced_names=()) -> None:
        # Longest first, so "YOUNG ARJUN" wins over "ARJUN" on the same words.
        self.known = sorted(set(character_names) | set(introduced_names), key=len, reverse=True)
        self.speakers = set(character_names)
        # A first name or surname alone stands for the person when only one person has it and it
        # is not an English word: "Mehra" = COACH MEHRA, "Savitri" = SAVITRI KUMARI.
        claims: dict[str, set[str]] = {}
        for name in self.known:
            toks = name.split()
            if len(toks) > 1:
                for t in (toks[0], toks[-1]):
                    if len(t) > 2 and t not in TITLES and t not in VERSION_WORDS and not is_english(t):
                        claims.setdefault(t, set()).add(name)
        self.short = {}
        for t, owners in claims.items():
            if len(owners) == 1 and t not in self.known:
                self.short.setdefault(next(iter(owners)), []).append(t)

    def _names_for(self, name: str) -> list[str]:
        return [*_variants(name), *self.short.get(name, [])]

    def extract(self, scene_text: str, ctx: dict) -> SceneExtraction:
        elements = ctx["elements"]
        action = " \n".join(e.text for e in elements if e.element_type == ElementType.ACTION)
        dialogue = " \n".join(e.text for e in elements
                              if e.element_type in (ElementType.DIALOGUE, ElementType.PARENTHETICAL))
        cue_names = set(ctx["cue_names"])
        chars: dict[str, CharacterOut] = {}

        def add(name, presence, speaking, evidence, conf, group=False, qtext=None, qn=None, note=None):
            if name in chars or name in cue_names:
                return
            chars[name] = CharacterOut(
                name_as_written=name, canonical_name=name, presence=presence, speaking=speaking,
                is_group=group, quantity_text=qtext, quantity_min=qn, quantity_max=qn,
                age_or_version=None, evidence=evidence, confidence=conf, uncertainty_note=note)

        for m in _GROUP_RE.finditer(action):
            if not is_people(m.group(2)):
                continue        # "TWO JEEPS", "THREE GUNSHOTS" are things, not a group of people
            n = m.group(1)
            qn = int(n) if n.isdigit() else NUMBER_WORDS[n]
            add(normalize_name(m.group(2)), "present", "silent", m.group(0), 0.7, True, m.group(0), qn)

        def spots(masked: str, name: str) -> list[tuple[int, int]]:
            out = []
            for v in self._names_for(name):
                out += [(m.start(), m.end()) for m in
                        re.finditer(rf"(?<![\w']){re.escape(v)}(?![\w])", masked, re.IGNORECASE)]
            return sorted(out)

        def mask_all(text: str, name: str) -> str:
            for v in self._names_for(name):
                text = _mask(text, v)
            return text

        masked_action, masked_dialogue = action, dialogue
        for name in self.known:
            if name in cue_names:
                masked_action, masked_dialogue = mask_all(masked_action, name), mask_all(masked_dialogue, name)
                continue
            found = spots(masked_action, name)
            if found:
                kinds = [_mention_kind(action, a, b) for a, b in found]
                a, b = found[kinds.index("there")] if "there" in kinds else found[0]
                ev = action[a:b]
                if "there" in kinds:
                    # A speaking character of the script, named here as the one doing something
                    # ("Savitri ducks"): sure enough not to need checking. Anything less is flagged.
                    acts = name in self.speakers and any(
                        k == "there" and _is_action_word(_ACTS_RE.match(action[y:y + 40])) for (x, y), k in zip(found, kinds))
                    add(name, "present", "silent", ev, 0.75 if acts else 0.6)
                elif "depicted" in kinds:
                    add(name, "mentioned", "silent", action[max(0, a - 30):b].strip(), 0.5,
                        note="Only shown or talked about here (a photo, a memory): counted as mentioned.")
                else:
                    add(name, "uncertain", "silent", action[a:min(len(action), b + 25)].strip(), 0.4,
                        note="Only named as the owner of something here: check whether they are in the scene.")
                masked_action, masked_dialogue = mask_all(masked_action, name), mask_all(masked_dialogue, name)
                continue
            found = spots(masked_dialogue, name)
            if found:
                add(name, "mentioned", "silent", dialogue[found[0][0]:found[0][1]], 0.6)
                masked_dialogue = mask_all(masked_dialogue, name)

        parent, sub, mods = split_location(ctx.get("location_raw"))
        loc = LocationOut(story_location=parent, sub_location=sub, modifiers=mods,
                          evidence=ctx.get("location_raw") or "", confidence=0.8) if parent else None
        first_action = next((e.text for e in elements if e.element_type == ElementType.ACTION), "")
        synopsis = first_action[:200] + ("…" if len(first_action) > 200 else "")
        return SceneExtraction(characters=list(chars.values()), location=loc,
                               synopsis=f"(auto, first action line) {synopsis}".strip(),
                               int_ext_suggestion="UNKNOWN", notes=None)


def _mask(text: str, name: str) -> str:
    for v in _variants(name):
        text = re.sub(rf"(?<![\w']){re.escape(v)}(?![\w])", lambda m: "#" * len(m.group(0)), text,
                      flags=re.IGNORECASE)
    return text


SHOWN_AFTER_COLON = _SHOWN_AFTER_COLON = re.compile(
    r"\b(?:photo(?:graph)?|picture|portrait|painting|poster|sketch|drawing)s?\s*:", re.IGNORECASE)
_ACTS_RE = re.compile(r"\s+(?:(?:and|,)\s+[A-Z][\w']+\s+)*([a-z]+)")


def _is_action_word(m) -> bool:
    e = entry(m.group(1)) if m else None
    return bool(e) and ("z" in e[3] or "v" in e[3])


_AFTER_POSSESSIVE = re.compile(r"^['’]s\s+((?:[\w-]+\s+){0,2}?)([\w-]+)")


def _mention_kind(text: str, start: int, end: int) -> str:
    """How one mention of a name in action text counts:
    'there'    the person is in the scene              "Savitri ducks", "Kamla's eyes fill with tears"
    'depicted' shown or talked about, not there        "a photograph of Baldev", "news of Vikram"
    'owner'    only names whose thing it is            "Arjun's house", "Baldev's father" (no evidence)
    """
    before = text[max(0, start - 40):start]
    if re.search(_DEPICTED_RE, before, re.IGNORECASE):
        return "depicted"
    paragraph = text[text.rfind("\n", 0, start) + 1:start]
    if _SHOWN_AFTER_COLON.search(paragraph):
        return "depicted"                    # "...a framed photograph: two boys. Vikram and Arjun."
    m = _AFTER_POSSESSIVE.match(text[end:end + 60])
    if not m:
        return "there"
    e = entry(m.group(2))
    kind = e[1] if e else ""
    # her eyes, his kurta, her hand: part of the person, so the person is there
    if kind == "body" or kind.startswith("cos") or m.group(2).lower() in (
            "voice", "face", "eyes", "hand", "hands", "shoulder", "shoulders", "feet", "back", "head", "hair", "arm",
            "arms", "leg", "legs", "lip", "lips", "body", "grip", "gaze", "expression", "smile", "tears", "breath"):
        return "there"
    return "owner"

