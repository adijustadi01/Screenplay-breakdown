"""Offline grammar tagger: is each word a noun, a verb, an adjective...?

"The phone RINGS" (verb) and "her RINGS" (noun) look the same to a word list. This tagger reads
the whole sentence and labels every word with its part of speech (Penn Treebank tags: NN noun,
NNS plural noun, NNP name, VBZ verb, JJ adjective, CD number, DT a/the, PRP$ his/her...).

It is the averaged perceptron tagger of Matthew Honnibal (MIT licence), with the NLTK English
model, re-implemented here in ~60 lines so the app needs no extra library and no internet.
About 97% accurate on ordinary English. Pure code: the same sentence always gets the same tags.
"""
from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

_START = ["-START-", "-START2-"]
_END = ["-END-", "-END2-"]
_TOKEN_RE = re.compile(
    r"n['’]t\b"                               # does|n't
    r"|['’](?:s|S|re|ve|ll|d|m)\b"            # Geeta|'s, they|'re
    r"|\d+(?:[.,:/]\d+)*(?:st|nd|rd|th|s)?"   # 10, 1990s, 3rd
    r"|[A-Za-zÀ-ɏ]+(?:[-'’][A-Za-zÀ-ɏ]+)*\.?(?=\s|$|[,;:!?)\"])"   # words, Mr.
    r"|[A-Za-zÀ-ɏ]+(?:[-'’][A-Za-zÀ-ɏ]+)*"
    r"|\.\.\.|--|[^\sA-Za-z0-9]")
_SPLIT_RE = re.compile(r"(?i)(.+?)(n['’]t|['’]s)")
_ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "prof.", "insp.", "col.", "capt.", "lt.", "sgt."}


@lru_cache(maxsize=1)
def _model() -> tuple[dict, dict, list]:
    with gzip.open(Path(__file__).with_name("tagger.json.gz"), "rt", encoding="utf-8") as f:
        data = json.load(f)
    return data["weights"], data["tagdict"], sorted(data["classes"])


def tokenize(sentence: str) -> list[tuple[str, int, int]]:
    """[(token, start, end)] — positions point back into the sentence. Penn Treebank style:
    "Geeta's" -> Geeta + 's, "doesn't" -> does + n't, a final full stop is its own token."""
    out = []
    for m in _TOKEN_RE.finditer(sentence):
        tok, s, e = m.group(0), m.start(), m.end()
        if tok.endswith(".") and len(tok) > 1 and tok.lower() not in _ABBREV:
            pieces = [(tok[:-1], s, e - 1), (".", e - 1, e)]
        else:
            pieces = [(tok, s, e)]
        for t, a, b in pieces:
            sp = _SPLIT_RE.fullmatch(t)
            if sp and len(sp.group(1)) > 1:
                cut = a + len(sp.group(1))
                out.append((sp.group(1), a, cut))
                out.append(("n't" if sp.group(2).lower().startswith("n") else "'s", cut, b))
            else:
                out.append((t, a, b))
    return out


def _normalize(word: str) -> str:
    if "-" in word and word[0] != "-":
        return "!HYPHEN"
    if word.isdigit() and len(word) == 4:
        return "!YEAR"
    if word and word[0].isdigit():
        return "!DIGITS"
    return word.lower()


def tag(tokens: list[str]) -> list[str]:
    """One Penn Treebank tag per token."""
    weights, tagdict, classes = _model()
    context = _START + [_normalize(w) for w in tokens] + _END
    prev, prev2 = _START
    out = []
    for i, word in enumerate(tokens):
        t = tagdict.get(word)
        if not t:
            j = i + 2
            feats = ("bias", "i suffix " + word[-3:], "i pref1 " + (word[0] if word else ""), "i-1 tag " + prev,
                     "i-2 tag " + prev2, f"i tag+i-2 tag {prev} {prev2}", "i word " + context[j],
                     f"i-1 tag+i word {prev} {context[j]}", "i-1 word " + context[j - 1],
                     "i-1 suffix " + context[j - 1][-3:], "i-2 word " + context[j - 2],
                     "i+1 word " + context[j + 1], "i+1 suffix " + context[j + 1][-3:],
                     "i+2 word " + context[j + 2])
            scores: dict[str, float] = {}
            for f in feats:
                w = weights.get(f)
                if w:
                    for label, v in w.items():
                        scores[label] = scores.get(label, 0.0) + v
            t = max(classes, key=lambda label: (scores.get(label, 0.0), label))
        out.append(t)
        prev2, prev = prev, t
    return out


def tag_sentence(sentence: str, is_word=None) -> list[tuple[str, str, int, int]]:
    """[(token, tag, start, end)]. Screenplays put names, props and sounds in CAPITALS, which the
    tagger would read as names; `is_word(lowercase)` says which capitals are ordinary English words
    ("a MIRROR") so they are tagged as such, and the rest are treated as names ("RAGHAV")."""
    toks = tokenize(sentence)
    words = []
    start = True
    for tok, _, _ in toks:
        if len(tok) > 1 and tok.isupper():
            tok = tok.lower() if (is_word is None or is_word(tok.lower())) else tok.capitalize()
        elif start and tok[:1].isupper() and is_word is not None and is_word(tok.lower()):
            tok = tok.lower()          # "Children play": the capital only marks the sentence start
        start = tok in (".", "!", "?")
        words.append(tok)
    return [(t[0], g, t[1], t[2]) for t, g in zip(toks, tag(words))]
