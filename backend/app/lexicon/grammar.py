"""Read one sentence of action text: which words are the things, people and animals in it?

Built on the offline grammar tagger (tagger.py) and the dictionary (words.tsv.gz), with fixes
for how screenplays are written:

- Present tense. The tagger learnt from newspapers and often takes a present-tense verb for a
  plural noun ("The phone RINGS.", "the crowd CHEERS"). When a clause has no verb at all, the
  last word that can be a verb is one.
- CAPITALS. "A MIRROR" is a mirror, "RAGHAV" is a name: the dictionary decides.
- Noun groups. In "the milk bottle" the thing is the BOTTLE; in "two police jeeps" it is the JEEP.

Everything here is plain code: the same sentence always gives the same answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import entry, is_english
from .tagger import tag_sentence

NOUN_TAGS = {"NN", "NNS"}
VERB_TAGS = {"VBZ", "VBP", "VBD", "MD", "VB"}
_CLAUSE_BREAK = {",", ";", ":", "--", "...", "and", "but", "as", "while", "when", "then", "until", "before", "after",
                 "because", "who", "which", "that", "where", "so", "though", "although", "if", "–", "—"}
_BEFORE_NOUN = {"DT", "PRP$", "CD", "JJ", "JJR", "JJS", "IN", "POS", "TO", "VBN", "VBG", "PDT", "WP$"}
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'“(]?[A-Z0-9])")


@dataclass
class Tok:
    text: str
    tag: str
    start: int
    end: int
    code: str = ""        # P person, G group, A animal, O other noun, "" not a noun in the dictionary
    kind: str = ""        # department tag: "veh:car", "prop:weapon", "body"...
    lemma: str = ""       # singular form, lower case
    pos: str = ""         # dictionary parts of speech (n v z w a r), "" = not an English word

    @property
    def low(self) -> str:
        return self.text.lower()

    @property
    def is_noun(self) -> bool:
        return self.tag in NOUN_TAGS

    @property
    def plural(self) -> bool:
        """Plural by the dictionary ("neighbours" -> neighbour), or by the tagger for unknown words."""
        if self.lemma and "n" in self.pos:
            return self.lemma != self.low
        return self.tag == "NNS"


@dataclass
class NounGroup:
    """One thing mentioned in a sentence: 'two police jeeps', 'her gold medal'."""
    head: Tok
    modifiers: list[Tok] = field(default_factory=list)   # noun/adjective words directly before the head
    first: int = 0            # index of the first token of the whole group (determiner included)
    head_index: int = 0
    determiner: str = ""      # "a", "the", "his", "two", "geeta's"... lower case, "" if none
    number: str = ""          # the number word or digits, if stated ("two", "200", "a dozen")

    @property
    def start(self) -> int:
        return self.modifiers[0].start if self.modifiers else self.head.start


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.split(text) if s.strip()]


def read(sentence: str, names: frozenset[str] = frozenset()) -> list[Tok]:
    """Tag a sentence and attach dictionary facts. `names` (UPPER CASE words of cast names) are
    always names, never things: a character called ROSE is not a flower."""
    def is_word(low: str) -> bool:
        return low.upper() not in names and is_english(low)

    toks: list[Tok] = []
    for text, tag, s, e in tag_sentence(sentence, is_word):
        t = Tok(text, tag, s, e)
        if text.upper() in names and text[:1].isupper():
            t.tag = "NNP"
        elif text[:1].isalpha():
            en = entry(text)
            if en:
                t.code, t.kind, t.lemma, t.pos = en[0], en[1], (en[2] or text.lower()), en[3]
        toks.append(t)
    _fix_tags(toks)
    return toks


_NOUN_MUST_FOLLOW = {"DT", "PRP$", "JJ", "JJR", "JJS", "CD", "POS", "IN", "TO", "PDT", "WP$"}
_CLAUSE_WORDS = {";", "and", "but", "as", "while", "when", "then", "until", "before", "after", "because", "so",
                 "though", "although", "if", "–", "—", "--"}


_DIRECTION_WORDS = {"past", "back", "around", "down", "up", "out", "over", "inside", "outside", "forward", "ahead",
                    "away", "apart", "aside", "along", "behind", "below", "above", "across", "by", "off", "through",
                    "round", "close", "right", "left", "straight", "still", "first", "last", "next", "flat", "hard",
                    "fast", "high", "low", "late", "early", "alone", "together", "upstairs", "downstairs", "nearby",
                    "home", "aloud", "open", "shut", "free", "loose", "tight"}


_PLURAL_MODIFIERS = {"sports", "arms", "customs", "sales", "goods", "news", "clothes", "games", "savings", "awards",
                     "arts", "communications", "systems", "operations", "narcotics", "weapons", "drinks", "ladies",
                     "mens", "womens", "childrens", "girls", "boys"}


def _noun_tag(t: Tok) -> str:
    return "NNS" if (t.lemma != t.low and t.low.endswith("s")) else "NN"


def _fix_tags(toks: list[Tok]) -> None:
    n = len(toks)
    for i, t in enumerate(toks):
        # A dictionary word the tagger called a name mid-sentence ("the Jeep") is that word.
        if t.tag in ("NNP", "NNPS") and "n" in t.pos and (t.text.islower() or i == 0 or toks[i - 1].tag == "."):
            t.tag = _noun_tag(t)
        # An adjective-tagged noun at the end of its group ("Geeta's medal glints") is the noun.
        if t.tag == "JJ" and "n" in t.pos and "a" not in t.pos:
            nxt = toks[i + 1] if i + 1 < n else None
            if nxt is None or not (nxt.tag in NOUN_TAGS and "z" not in nxt.pos):
                t.tag = _noun_tag(t)
        # A verb-tagged noun right after a/the/his ("the watch") is the noun.
        if t.tag.startswith("VB") and "n" in t.pos and i and toks[i - 1].tag in ("DT", "PRP$", "CD", "POS"):
            if not (t.tag in ("VBG", "VBN") or (i + 1 < n and toks[i + 1].tag in NOUN_TAGS)):
                t.tag = _noun_tag(t)

    # "wheels a stretcher PAST", "walks BACK": direction words after a noun are not things.
    for i, t in enumerate(toks):
        if t.tag in NOUN_TAGS and t.low in _DIRECTION_WORDS and i and toks[i - 1].tag in NOUN_TAGS | {"NNP", "PRP"}:
            t.tag = "RB"

    # Present-tense verbs the tagger took for nouns ("The phone RINGS", "Savitri DUCKS, GRABS his leg").
    def clause_has_verb(i: int) -> bool:
        a = i
        while a > 0 and toks[a - 1].low not in _CLAUSE_WORDS and toks[a - 1].tag != ".":
            a -= 1
        b = i
        while b + 1 < n and toks[b + 1].low not in _CLAUSE_WORDS and toks[b + 1].tag != ".":
            b += 1
        return any(toks[k].tag in VERB_TAGS for k in range(a, b + 1) if k != i)

    for i, t in enumerate(toks):
        if t.tag not in ("NN", "NNS", "JJ") or i == 0:
            continue
        third = "z" in t.pos and t.low.endswith("s")
        base = "v" in t.pos and not third
        if not (third or base):
            continue
        k = i - 1
        while k > 0 and toks[k].tag == "," :
            k -= 1
        prev, direct = toks[k], toks[i - 1]
        if direct.tag in _NOUN_MUST_FOLLOW or direct.tag.startswith("VB"):
            continue                                        # "her rings", "eats ducks": a noun
        if third and prev.tag in ("NNP", "NNPS", "PRP"):
            t.tag = "VBZ"                                   # "Savitri ducks", "Pappu, whistles"
        elif base and direct.tag == "PRP" and direct.low in ("they", "we", "you", "i"):
            t.tag = "VBP"
        elif direct.tag in (",", "CC") and prev.tag in ("VBZ", "VBP") and third:
            t.tag = "VBZ"                                   # "ducks, grabs his leg and throws him"
        elif prev is direct and prev.low in _NUMBER_WORDS:
            continue                                        # "a dozen neighbours": still people
        elif prev is direct and prev.tag in ("NN", "NNS", "RB") and not clause_has_verb(i):
            # "a crowd of villagers cheers": the -s verb goes with "crowd"; "sports cars" stays a thing
            if third and (not prev.plural or prev.low not in _PLURAL_MODIFIERS) or base and prev.plural:
                t.tag = "VBZ" if third else "VBP"           # "the crowd cheers", "spectators crowd around"


_NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "fifteen", "twenty", "thirty", "forty", "fifty", "sixty", "hundred", "thousand", "dozen", "dozens",
                 "hundreds", "thousands", "several", "few", "many", "some", "couple"}


def noun_groups(toks: list[Tok]) -> list[NounGroup]:
    """Every noun group in the sentence, in order. The head is the last noun of a run of nouns."""
    out: list[NounGroup] = []
    i, n = 0, len(toks)
    while i < n:
        if not toks[i].is_noun:
            i += 1
            continue
        j = i
        while j + 1 < n and toks[j + 1].is_noun:
            j += 1
        head = toks[j]
        mods = list(toks[i:j])
        k = i - 1
        while k >= 0 and toks[k].tag in ("JJ", "JJR", "JJS", "VBN", "VBG") :
            mods.insert(0, toks[k])
            k -= 1
        det, number = "", ""
        # "a dozen neighbours": the number word is not part of the name
        while mods and mods[0].low in _NUMBER_WORDS and mods[0] is not head:
            number = mods.pop(0).low
            det = number
        first = k + 1
        if number:
            first = k + 1 if not (k >= 0 and toks[k].low in ("a", "an")) else k
        elif k >= 0 and toks[k].tag == "CD" or (k >= 0 and toks[k].low in _NUMBER_WORDS):
            number = toks[k].low
            det = number
            first = k
            if k >= 1 and toks[k - 1].low in ("a", "an") :          # "a dozen", "a few"
                number = f"{toks[k - 1].low} {number}"
                first = k - 1
        elif k >= 0 and toks[k].tag in ("DT", "PRP$", "PDT"):
            det, first = toks[k].low, k
        elif k >= 0 and toks[k].tag == "POS" or (k >= 0 and toks[k].low in ("'s", "’s")):
            det, first = (toks[k - 1].low + "'s" if k >= 1 else "'s"), max(k - 1, 0)
        elif k >= 1 and toks[k].low == "of" and toks[k - 1].low in _NUMBER_WORDS:
            number = toks[k - 1].low                                 # "dozens of", "hundreds of"
            det, first = number, k - 1
        out.append(NounGroup(head, mods, first, j, det, number))
        i = j + 1
    return out
