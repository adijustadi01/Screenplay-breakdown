"""Built-in English dictionary: is this word a person, a group of people, an animal or a thing?

Works offline with no API key. The data is Princeton WordNet 3.0 (about 235,000 noun forms,
plurals included), pre-built into nouns.tsv.gz by tools/build_lexicon.py. A short list of
words WordNet lacks or gets wrong for Indian and film scripts (PANDIT, DHOBI, AUNTY, GANG...) is kept
below and always wins.

    kind_of("A KNIFE")       -> "thing"
    kind_of("WARD BOY")      -> "person"       (the last word decides when the phrase is unknown)
    kind_of("GOONS")         -> "person"
    kind_of("MOB")           -> "group"
    kind_of("GOLDEN RETRIEVER") -> "animal"
    kind_of("STAR")          -> "maybe_person" (usually not a person; a person only sometimes)
    kind_of("RAGHAV")        -> None           (not an English word: probably a name)
"""
from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

_CODES = {"P": "person", "p": "maybe_person", "G": "group", "A": "animal", "O": "thing"}

# Words the dictionary lacks, or whose main meaning in a script is a person even though
# WordNet's main meaning is something else. These always count as people.
EXTRA_PEOPLE = {
    "PANDIT", "POOJARI", "PUJARI", "SADHU", "BABA", "DHOBI", "COOLIE", "PEON", "AUNTY", "AUNTIE", "DADI", "NANI",
    "DADA", "NANA", "BHAI", "DIDI", "BHABHI", "CHACHA", "CHACHI", "MAMA", "MAMI", "BEGUM", "SAHIB", "MEMSAHIB",
    "SETH", "SETHJI", "MUNSHI", "HAVILDAR", "JAWAN", "JAWANS", "CHOWKIDAR", "DARWAN", "AYAH", "MALI", "HAWALDAR",
    "SARPANCH", "ZAMINDAR", "THAKUR", "MAHANT", "FAKIR", "MAULVI", "GOON", "GOONS", "MLA", "MP", "DON",
    "FIGURE", "SHADOW", "STAR", "ANCHOR", "DOUBLE", "STUNTMAN", "COMPOUNDER", "WARDEN", "GUARD", "GUARDS",
    "SECURITY", "GENERAL", "MAJOR", "CHIEF", "CAPTAIN", "SON", "COOK", "BOSS", "OWNER", "AGENT", "SPY",
    "MANAGER", "SECRETARY", "ASSISTANT", "PLAYER", "PLAYERS", "COACH", "FAN", "FANS", "ELDER", "ELDERS",
}
EXTRA_GROUPS = {"GANG", "GANGS", "CROWD", "CROWDS", "MOB", "MOBS", "CREW", "CREWS", "TEAM", "TEAMS", "BARAAT",
                "AUDIENCE", "CONGREGATION", "PROCESSION", "FAMILY", "FAMILIES", "TROUPE", "BAND", "ARMY",
                "POLICE", "PLATOON", "SQUAD", "VILLAGERS"}


@lru_cache(maxsize=1)
def _words() -> dict[str, str]:
    path = Path(__file__).with_name("nouns.tsv.gz")
    out: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            word, _, code = line.rstrip("\n").partition("\t")
            out[word] = code
    return out


def _one(word: str) -> str | None:
    w = word.strip(" .,'’").upper()
    if not w:
        return None
    if w in EXTRA_GROUPS:
        return "group"
    if w in EXTRA_PEOPLE:
        return "person"
    code = _words().get(w.lower())
    return _CODES[code] if code else None


def kind_of(phrase: str) -> str | None:
    """'person', 'group', 'maybe_person', 'animal', 'thing', or None when the word is not in the dictionary.

    The whole phrase is tried first ("POLICE OFFICER"), then its last word ("WARD BOY" -> BOY).
    """
    p = " ".join(phrase.replace("’", "'").split())
    if not p:
        return None
    if p.upper().startswith(("A ", "AN ", "THE ")):
        p = p.split(" ", 1)[1]
    whole = _one(p)
    if whole is not None or " " not in p:
        return whole
    return _one(p.rsplit(" ", 1)[1])


def is_people(phrase: str) -> bool:
    """True when the phrase names a person or a group of people."""
    return kind_of(phrase) in ("person", "group")


def is_not_people(phrase: str) -> bool:
    """True when the dictionary is sure this is an object, sound, place or animal — not a person."""
    return kind_of(phrase) in ("thing", "animal")
