"""Built-in English dictionary: is this word a person, a group of people, an animal or a thing?

Works offline with no API key. The data is Princeton WordNet 3.0 (about 286,000 word forms,
plurals and verb endings included), pre-built into words.tsv.gz by tools/build_lexicon.py. Each noun
also carries what a production department would call it ("veh:truck", "prop:weapon", "cos:accessory",
"animal:dog"), taken from the dictionary's "is a kind of" tree: see entry(). A short list of
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
    """form -> "code<TAB>tag<TAB>lemma<TAB>pos" (see tools/build_lexicon.py)."""
    path = Path(__file__).with_name("words.tsv.gz")
    out: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            word, _, rest = line.rstrip("\n").partition("\t")
            out[word] = rest
    return out


def entry(word: str) -> tuple[str, str, str, str] | None:
    """(code, tag, lemma, pos) for one word form, or None when it is not an English word.

    code: P person, p maybe person, G group, A animal, O other noun, "" not a noun
    tag: "veh:car", "animal:dog", "cos:accessory", "prop:weapon", "inst", "body" or ""
    lemma: the singular when the form is a plural ("knives" -> "knife"), else ""
    pos: letters among n (noun) v (verb) z (verb with -s) w (other verb form) a (adjective) r (adverb)
    """
    rest = _words().get(word.strip(" .,").replace("’", "'").lower())
    if rest is None:
        return None
    code, tag, lemma, pos = rest.split("\t")
    return code, tag, lemma, pos


# The dictionary has no grammar words (pronouns, prepositions...): they are English too.
FUNCTION_WORDS = frozenset("""
a an the this that these those i me my mine myself we us our ours ourselves you your yours yourself yourselves
he him his himself she her hers herself it its itself they them their theirs themselves who whom whose which what
whatever whoever where when why how whenever wherever and or but nor so yet for if then than because although though
while whereas unless until since once as of in on at by to from with without within into onto upon over under above
below between among through during before after about against around across along behind beside besides beyond
toward towards near off out up down away is am are was were be been being have has had having do does did doing
done will would shall should can could may might must ought not no yes none all any both each every either neither
few many much more most several some such only own same other another others one ones two three four five six
seven eight nine ten eleven twelve hundred thousand first second third next last very too also just even still
again ever never always often sometimes here there now today tonight tomorrow yesterday ok okay oh ah hey hi hello
mr mrs ms dr sir madam everyone everybody someone somebody anyone anybody nobody nothing something anything
everything else per via etc
""".split())


def is_english(word: str) -> bool:
    """True when the word is English at all (any part of speech, grammar words included)."""
    w = word.strip(" .,").replace("’", "'")
    return (w.lower() in FUNCTION_WORDS or entry(w) is not None or w.upper() in EXTRA_PEOPLE
            or w.upper() in EXTRA_GROUPS)


def _one(word: str) -> str | None:
    w = word.strip(" .,'’").upper()
    if not w:
        return None
    if w in EXTRA_GROUPS:
        return "group"
    if w in EXTRA_PEOPLE:
        return "person"
    e = entry(w)
    return _CODES[e[0]] if e and e[0] else None


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
