"""Offline, deterministic extractor (no API key needed).

A baseline, not a substitute for the LLM: it finds silent characters by name
matching in action lines and mentions by name matching in dialogue. It cannot
tell a photograph from a person, or resolve "Papa" to a character, so its
non-cue guesses carry low confidence and are flagged for review.
"""
from __future__ import annotations

import re

from ..models.enums import ElementType
from .cues import normalize_name
from .schema import CharacterOut, LocationOut, SceneExtraction

NUMBER_WORDS = {"TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8,
                "NINE": 9, "TEN": 10, "TWELVE": 12, "TWENTY": 20}
MODIFIERS = {"MOVING", "FLASHBACK", "ESTABLISHING", "CONTINUOUS", "PRESENT DAY", "DREAM", "MONTAGE",
             "STOCK", "INTERCUT", "B&W", "BLACK AND WHITE", "SLOW MOTION"}
TITLES = {"DR.", "DR", "MR.", "MR", "MRS.", "MRS", "MS.", "MS", "INSPECTOR", "NURSE", "OFFICER",
          "CONSTABLE", "SERGEANT", "CAPTAIN", "PROFESSOR", "UNCLE", "AUNTY", "AUNT"}
VERSION_WORDS = {"YOUNG", "OLD", "OLDER", "YOUNGER", "LITTLE", "ADULT", "TEENAGE", "BABY"}
NOT_NAMES = {"INT", "EXT", "CONTINUED", "CUT", "FADE", "THE", "END", "DAY", "NIGHT", "POV", "V.O.", "O.S."}

_INTRO_RE = re.compile(r"\b((?:[A-Z][A-Z.'’\-]+)(?:\s+[A-Z][A-Z.'’\-0-9]+){0,3})\s*\((\d{1,3}s?|[A-Z]?\d+[A-Za-z]*)\)")
_GROUP_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r"|\d+)\s+([A-Z]{3,}(?:\s+[A-Z]{3,})?)\b")
_ARTICLE_CAPS_RE = re.compile(r"\b(?:A|An|The)\s+([A-Z]{3,}(?:\s+[A-Z]{2,}){0,2})\b")
# Screenwriters put props and sounds in CAPS too ("a MIRROR", "The GUNSHOT"), so "A/The + CAPS" only
# counts as a new character when its last word names a person ("A CONSTABLE", "A WARD BOY").
PERSON_WORDS = {
    "MAN", "MEN", "WOMAN", "WOMEN", "BOY", "BOYS", "GIRL", "GIRLS", "CHILD", "KID", "KIDS", "BABY", "LADY",
    "GENTLEMAN", "GUY", "GUYS", "TEEN", "TEENAGER", "PERSON", "PEOPLE", "STRANGER", "FIGURE",
    "CONSTABLE", "COP", "COPS", "OFFICER", "INSPECTOR", "SERGEANT", "SOLDIER", "SOLDIERS", "GUARD", "GUARDS",
    "WATCHMAN", "SECURITY", "COMMISSIONER", "CAPTAIN", "COMMANDER", "MAJOR", "COLONEL", "GENERAL",
    "DOCTOR", "NURSE", "SURGEON", "PATIENT", "COMPOUNDER", "PRIEST", "PANDIT", "POOJARI", "SADHU", "MONK",
    "IMAM", "NUN", "FATHER", "MOTHER", "BROTHER", "SISTER", "SON", "DAUGHTER", "WIFE", "HUSBAND", "UNCLE",
    "AUNTY", "AUNT", "GRANDFATHER", "GRANDMOTHER", "GRANDMA", "GRANDPA", "COUSIN", "NEPHEW", "NIECE",
    "FRIEND", "NEIGHBOUR", "NEIGHBOR", "LANDLORD", "LANDLADY", "TENANT", "SERVANT", "MAID", "COOK", "BUTLER",
    "DRIVER", "CHAUFFEUR", "PILOT", "CONDUCTOR", "PORTER", "COOLIE", "WAITER", "WAITRESS", "BARTENDER",
    "MANAGER", "CLERK", "CASHIER", "ACCOUNTANT", "BOSS", "OWNER", "SHOPKEEPER", "VENDOR", "SELLER", "HAWKER",
    "MERCHANT", "BROKER", "BANKER", "LAWYER", "ADVOCATE", "JUDGE", "MINISTER", "POLITICIAN", "MLA", "MP",
    "TEACHER", "PRINCIPAL", "STUDENT", "STUDENTS", "PROFESSOR", "REPORTER", "JOURNALIST", "PHOTOGRAPHER",
    "ANCHOR", "FARMER", "VILLAGER", "VILLAGERS", "FISHERMAN", "LABOURER", "LABORER", "WORKER", "WORKERS",
    "MECHANIC", "TAILOR", "BARBER", "DHOBI", "PEON", "POSTMAN", "MILKMAN", "BEGGAR", "THIEF", "ROBBER",
    "GOON", "GOONS", "HENCHMAN", "HENCHMEN", "THUG", "THUGS", "GANGSTER", "KILLER", "ASSASSIN", "SMUGGLER",
    "DON", "BODYGUARD", "HITMAN", "RECEPTIONIST", "SECRETARY", "ASSISTANT", "ATTENDANT", "AGENT", "SPY",
    "DANCER", "DANCERS", "SINGER", "MUSICIAN", "DRUMMER", "ACTOR", "ACTRESS", "HERO", "HEROINE", "VILLAIN",
    "KING", "QUEEN", "PRINCE", "PRINCESS", "MINISTERS", "PRISONER", "CONVICT", "WARDEN", "JAILER",
    "MOURNER", "MOURNERS", "CROWD", "MOB", "GANG", "DOUBLE", "STUNTMAN", "CUSTOMER", "PASSENGER",
    "PASSENGERS", "TOURIST", "VISITOR", "GUEST", "GUESTS", "BRIDE", "GROOM", "ELDER", "ELDERS", "CHIEF",
    "HEADMAN", "LEADER", "PLAYER", "PLAYERS", "COACH", "UMPIRE", "FAN", "FANS", "SAILOR", "CREW",
}


def intro_names(action_texts: list[str]) -> set[str]:
    """Names introduced in CAPS in action lines: 'DADI (80s)', 'A CONSTABLE'."""
    names = set()
    for t in action_texts:
        for m in _INTRO_RE.finditer(t):
            names.add(normalize_name(m.group(1)))
        for m in _ARTICLE_CAPS_RE.finditer(t):
            phrase = m.group(1)
            if phrase not in NOT_NAMES and phrase.split()[-1] in PERSON_WORDS:
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

    def set_script_context(self, character_names, location_names, introduced_names=()) -> None:
        # Longest first, so "YOUNG ARJUN" wins over "ARJUN" on the same words.
        self.known = sorted(set(character_names) | set(introduced_names), key=len, reverse=True)

    def extract(self, scene_text: str, ctx: dict) -> SceneExtraction:
        elements = ctx["elements"]
        action = " \n".join(e.text for e in elements if e.element_type == ElementType.ACTION)
        dialogue = " \n".join(e.text for e in elements
                              if e.element_type in (ElementType.DIALOGUE, ElementType.PARENTHETICAL))
        cue_names = set(ctx["cue_names"])
        chars: dict[str, CharacterOut] = {}

        def add(name, presence, speaking, evidence, conf, group=False, qtext=None, qn=None):
            if name in chars or name in cue_names:
                return
            chars[name] = CharacterOut(
                name_as_written=name, canonical_name=name, presence=presence, speaking=speaking,
                is_group=group, quantity_text=qtext, quantity_min=qn, quantity_max=qn,
                age_or_version=None, evidence=evidence, confidence=conf, uncertainty_note=None)

        for m in _GROUP_RE.finditer(action):
            n = m.group(1)
            qn = int(n) if n.isdigit() else NUMBER_WORDS[n]
            add(normalize_name(m.group(2)), "present", "silent", m.group(0), 0.7, True, m.group(0), qn)

        def find(masked: str, original: str, name: str) -> str | None:
            # Search the masked copy (same length) but quote the original text.
            for v in _variants(name):
                m = re.search(rf"(?<![\w']){re.escape(v)}(?![\w])", masked, re.IGNORECASE)
                if m:
                    return original[m.start(): m.end()]
            return None

        masked_action, masked_dialogue = action, dialogue
        for name in self.known:
            if name in cue_names:
                masked_action = _mask(masked_action, name)
                masked_dialogue = _mask(masked_dialogue, name)
                continue
            ev = find(masked_action, action, name)
            if ev:
                add(name, "present", "silent", ev, 0.6)
                masked_action = _mask(masked_action, name)
                masked_dialogue = _mask(masked_dialogue, name)
                continue
            ev = find(masked_dialogue, dialogue, name)
            if ev:
                add(name, "mentioned", "silent", ev, 0.6)
                masked_dialogue = _mask(masked_dialogue, name)

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

