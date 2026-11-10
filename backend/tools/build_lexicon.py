"""Build the built-in English dictionary used to tell people from objects.

Reads the raw Princeton WordNet 3.0 files (index.noun, data.noun, noun.exc) and writes
app/lexicon/nouns.tsv.gz: one line per noun form, "word<TAB>code", where code is

    P  the word's main (most common) meaning is a person       "CONSTABLE", "WIDOW", "GOONS"
    p  it can mean a person, but its main meaning is not       "STAR", "PIG", "SHADOW"
    G  its main meaning is a group of people                    "CROWD", "MOB", "CREW"
    A  its main meaning is an animal                            "DOG", "HORSES"
    O  its main meaning is something else (object, sound...)    "MIRROR", "KNIFE", "GUNSHOT"

Plurals are included ("SOLDIERS", "POLICEMEN"), so the app needs no grammar code at run time.

Run once (the output is committed; the app never downloads anything):

    python tools/build_lexicon.py /path/to/wordnet          # the folder holding index.noun

WordNet: https://wordnet.princeton.edu  (licence copied to app/lexicon/WORDNET_LICENSE.txt)
"""
from __future__ import annotations

import gzip
import shutil
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "lexicon"
PERSON, GROUP, ANIMAL = 18, 14, 5
# Group senses that mean people ("crowd", "mob") rather than collections of things ("bunch").
PEOPLE_GROUP_HINTS = ("people", "persons", "person", "men", "soldiers", "workers", "members", "individuals",
                      "followers", "players", "crowd")


def _plurals(word: str) -> set[str]:
    if " " in word:
        head, _, last = word.rpartition(" ")
        return {f"{head} {p}" for p in _plurals(last)}
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return {word + "es"}
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return {word[:-1] + "ies"}
    if word.endswith("man"):
        return {word[:-3] + "men", word + "s"}
    return {word + "s"}


def build(wn: Path) -> dict[str, str]:
    lex, gloss = {}, {}
    for line in (wn / "data.noun").read_text(encoding="latin-1").splitlines():
        if not line or line.startswith(" "):
            continue
        offset, filenum = line.split(" ", 2)[:2]
        lex[offset] = int(filenum)
        gloss[offset] = line.split("|", 1)[1].lower() if "|" in line else ""

    def code_for(offsets: list[str]) -> str:
        first = lex[offsets[0]]
        if first == PERSON:
            return "P"
        if first == GROUP and any(h in gloss[offsets[0]] for h in PEOPLE_GROUP_HINTS):
            return "G"
        if first == ANIMAL:
            return "A"
        if any(lex[o] == PERSON for o in offsets[1:]):
            return "p"
        return "O"

    words: dict[str, str] = {}
    for line in (wn / "index.noun").read_text(encoding="latin-1").splitlines():
        if not line or line.startswith(" "):
            continue
        parts = line.split()
        lemma, synset_cnt, p_cnt = parts[0], int(parts[2]), int(parts[3])
        offsets = parts[6 + p_cnt:6 + p_cnt + synset_cnt]   # ordered by how common each meaning is
        word = lemma.replace("_", " ")
        if not word.replace(" ", "").replace("-", "").replace("'", "").isalpha():
            continue
        words[word] = code_for(offsets)

    forms = dict(words)
    for line in (wn / "noun.exc").read_text(encoding="latin-1").splitlines():
        plural, *bases = line.split()
        base = bases[0].replace("_", " ") if bases else None
        plural = plural.replace("_", " ")
        if base in words and plural not in words:
            forms[plural] = words[base]
    for word, code in words.items():
        for pl in _plurals(word):
            forms.setdefault(pl, code)
    return forms


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    wn = Path(sys.argv[1])
    forms = build(wn)
    OUT.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{w}\t{c}\n" for w, c in sorted(forms.items()))
    with gzip.GzipFile(OUT / "nouns.tsv.gz", "wb", mtime=0) as f:
        f.write(body.encode("utf-8"))
    shutil.copy(wn / "LICENSE", OUT / "WORDNET_LICENSE.txt")
    counts: dict[str, int] = {}
    for c in forms.values():
        counts[c] = counts.get(c, 0) + 1
    print(f"{len(forms)} forms written to {OUT / 'nouns.tsv.gz'}: {counts}")


if __name__ == "__main__":
    main()
