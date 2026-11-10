"""Find the people of a script from its action lines — no API key, no cue needed.

Dialogue cues give the speaking cast. This module finds everybody else, and the people a
script introduces without a cue of their own:

- CAPITALS in action: "His daughters SAVITRI and KAMLA jog", "The REFEREE raises his hand",
  "a RUSSIAN WRESTLER". Capitals that the dictionary knows as things ("A MIRROR", "THE GUNSHOT")
  are not people.
- Names that are never in capitals: "Dadi sits on a charpai", "Masterji hurries over". A word
  that is not English, always starts with a capital, never follows a/the, and does what people do
  (it is followed by a verb, a comma or 's) is a name.

Words that only follow in/at/to/from are places; words inside quotes are text on a sign; words of
the scene headings are locations.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..lexicon import EXTRA_PEOPLE, entry, is_english, kind_of
from .cues import normalize_name

# Titles that go in front of a name. Kept with the name: "COACH MEHRA", "MRS. D'SOUZA".
NAME_TITLES = {"DR", "MR", "MRS", "MS", "MISS", "INSPECTOR", "NURSE", "OFFICER", "CONSTABLE", "SERGEANT", "CAPTAIN",
               "PROFESSOR", "UNCLE", "AUNTY", "AUNT", "COACH", "MASTER", "DOCTOR", "COLONEL", "MAJOR", "GENERAL",
               "JUDGE", "FATHER", "SISTER", "BROTHER", "SIR", "MADAM", "SARDAR", "SETH", "PANDIT", "THAKUR", "BABA",
               "SWAMI", "MAULANA", "USTAD", "GURU", "PRINCIPAL", "COMMISSIONER", "MINISTER", "CHIEF", "LALA", "CHACHA",
               "MAMA", "DADA", "BHAI", "DIDI", "SUB-INSPECTOR", "HAVILDAR", "SUBEDAR", "LIEUTENANT", "KING", "QUEEN",
               "PRINCE", "PRINCESS", "LORD", "LADY", "YOUNG", "OLD", "LITTLE"}
# Family words Indian scripts use as names ("Dadi sits", "Bauji enters").
KIN_NAMES = {"DADI", "NANI", "DADA", "NANA", "BAUJI", "BABUJI", "PITAJI", "MATAJI", "MAA", "AMMA", "AMMI", "ABBU",
             "ABBA", "APPA", "PAPA", "MUMMY", "DADDY", "BHABHI", "CHACHI", "MAMI", "BUA", "MAUSI", "TAU", "TAI",
             "DIDI", "BHAIYA", "MASTERJI", "GURUJI", "PANDITJI", "MUNSHIJI", "SETHJI", "BABA", "AJJI", "THATHA"}
_NOT_NAMES = {"INT", "EXT", "CUT", "FADE", "DISSOLVE", "SMASH", "SUPER", "TITLE", "CONTINUED", "CONT'D", "THE", "END",
              "DAY", "NIGHT", "MORNING", "EVENING", "LATER", "POV", "V.O", "O.S", "VO", "OS", "CU", "ECU", "MONTAGE",
              "FLASHBACK", "INTERCUT", "BACK", "ANGLE", "CLOSE", "WIDE", "INSERT", "SERIES", "SHOTS", "BEGIN",
              "INTERMISSION", "INTERVAL", "OMITTED", "SFX", "VFX", "MOS", "BG", "FG", "OK", "TV", "AM", "PM", "DJ",
              "SONG", "MUSIC", "SOUND", "NOTE", "SCENE", "TIME", "YEARS", "MONTHS", "DAYS", "WEEKS", "HOURS",
              "PRESENT", "PAST", "TODAY", "TOMORROW", "YESTERDAY", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY",
              "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER", "MONDAY", "TUESDAY",
              "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY", "GOD", "INDIA", "INDIAN", "HINDI", "ENGLISH",
              "URDU", "PUNJABI", "DELHI", "MUMBAI", "BOMBAY", "KOLKATA", "CALCUTTA", "CHENNAI", "MADRAS", "PUNJAB",
              "HARYANA", "LAHORE", "PAKISTAN", "LONDON", "AMERICA", "BHARAT", "HINDUSTAN", "DIWALI", "HOLI", "EID",
              "RAMZAN", "DUSSEHRA", "NAVRATRI", "OLYMPICS", "COMMONWEALTH"}
_PLACE_PREPS = {"in", "at", "to", "from", "near", "outside", "inside", "towards", "toward", "into", "across",
                "through", "of"}
_PERSON_FOLLOWERS = {"is", "was", "has", "does", "and", "who", "'s", "’s"}
_COUNT_WORDS = {"TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "TWELVE", "TWENTY", "FIFTY",
                "HUNDRED", "HUNDREDS", "THOUSANDS", "DOZENS", "SEVERAL", "MANY", "SOME", "FEW"}
_COUNT_WORDS_LOW = {w.lower() for w in _COUNT_WORDS} | {"of"}
_QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’\-][A-Za-zÀ-ɏ]+)*\.?|[,;:()]|['’]s\b")


def _clean(word: str) -> str:
    w = word.strip(".").replace("’", "'")
    w = re.sub(r"(?i)'s$", "", w)
    return re.sub(r"(?i)-(?:ji|saab|sahib|bhai|didi)$", "", w)


def _name_like(word: str) -> bool:
    """Not an English word (so probably a name), or a family word used as a name."""
    up = word.upper()
    if up in _NOT_NAMES or len(up) < 3 or not up[0].isalpha():
        return False
    return up in KIN_NAMES or not is_english(word)


def _verbish(word: str) -> bool:
    if word.lower() in _PERSON_FOLLOWERS or word in (",", "("):
        return True
    e = entry(word)
    return bool(e) and ("z" in e[3] and word.lower().endswith("s") or "w" in e[3] and word.lower().endswith("ed"))


def discover_names(action_texts: list[str], known: set[str] = frozenset(),
                   location_names: list[str] | tuple = ()) -> set[str]:
    """Names of people found in action lines, UPPER CASE, beyond those already `known` (the cues)."""
    known_words = {t for k in known for t in k.replace("'", " ").split()}
    place_words = {t for loc in location_names for t in re.findall(r"[A-Za-z']+", loc.upper())}
    stats: dict[str, Counter] = defaultdict(Counter)
    phrases: Counter = Counter()
    caps_people: Counter = Counter()

    for text in action_texts:
        if text.isupper() or re.match(r"\s*(?:SUPER|TITLE|SONG|CUT TO|FADE)\b", text):
            continue                                 # "SUPER: MUMBAI, 1995" — a caption, not people
        text = _QUOTED.sub(" ", text)
        for sent in _SENT_SPLIT.split(text):
            words = [m.group(0) for m in _WORD.finditer(sent)]
            i = 0
            while i < len(words):
                w = words[i]
                if not w[0].isalpha() or not w[0].isupper():
                    if w[0].isalpha() and _name_like(w):
                        stats[_clean(w).upper()]["lower"] += 1
                    i += 1
                    continue
                # a run of capitalised words: "Coach Mehra", "RUSSIAN WRESTLER", "Savitri"
                j = i
                while j + 1 < len(words) and words[j + 1][0].isalpha() and words[j + 1][0].isupper() \
                        and (words[j + 1].isupper() == w.isupper() or len(words[j + 1]) == 1):
                    j += 1
                run = [_clean(x) for x in words[i:j + 1]]
                prev = words[i - 1].lower() if i else ""
                nxt = words[j + 1] if j + 1 < len(words) else ""
                all_caps = all(x.isupper() and len(x) > 1 for x in run)
                if all_caps and len(run) <= 4:
                    ups = [x.upper() for x in run]
                    counted = bool(ups) and (ups[0] in _COUNT_WORDS or prev in _COUNT_WORDS_LOW)
                    while ups and ups[0] in ("A", "AN", "THE", "HIS", "HER"):
                        ups = ups[1:]
                    if ups and not counted and ups[-1] not in _NOT_NAMES and not _name_like(ups[-1]) \
                            and kind_of(" ".join(ups)) in ("person", "group") \
                            and (ups[-1] in EXTRA_PEOPLE or (entry(ups[-1]) or ("",))[0] in ("P", "G")
                                 or kind_of(ups[-1]) == "person"):
                        caps_people[" ".join(ups)] += 1          # "The REFEREE", "DHABA OWNER"
                for k, x in enumerate(run):
                    if not _name_like(x):
                        continue
                    st = stats[x.upper()]
                    st["caps" if words[i + k].isupper() and len(x) > 1 else
                       ("start" if i + k == 0 else "title")] += 1
                    before = (words[i + k - 1].lower() if i + k else "")
                    if k == 0 and prev in ("a", "an", "the"):
                        st["after_det"] += 1
                    if k == 0 and prev in _PLACE_PREPS:
                        st["after_place"] += 1
                    if k == len(run) - 1 and nxt and _verbish(nxt):
                        st["subject"] += 1
                    if k > 0 and (before.rstrip(".").upper() in NAME_TITLES or _name_like(run[k - 1])):
                        phrases[(run[k - 1].upper(), x.upper())] += 1
                i = j + 1

    names: set[str] = set()
    accepted: dict[str, Counter] = {}
    for word, st in stats.items():
        if word in known_words or word in place_words or st["lower"] or st["after_det"]:
            continue
        seen = st["caps"] + st["title"] + st["start"]
        if st["after_place"] * 2 > seen:
            continue                                 # mostly "in Sonpur", "to Lahore": a place
        strong = st["caps"] >= 1 and (st["subject"] >= 1 or seen >= 2 or st["caps"] == seen)
        repeated = seen >= 2 and st["subject"] >= 1
        once = seen == 1 and st["start"] == 1 and st["subject"] == 1
        if strong or repeated or once:
            accepted[word] = st
    for word in accepted:
        total = sum(accepted[word][k] for k in ("caps", "title", "start"))
        best = max(((n, a) for (a, b), n in phrases.items() if b == word), default=(0, ""))
        if best[0] * 2 >= total and (best[1] in NAME_TITLES or best[1] in accepted):
            if best[1] in NAME_TITLES:
                names.add(f"{best[1]} {word}")       # "COACH MEHRA"
            # FIRST LAST: the first name stands for the person; the surname is not a second character
            continue
        names.add(word)
    # a surname-only word that always follows an accepted first name is part of that name
    for (a, b), _n in phrases.items():
        if a in accepted and b in names and b in accepted:
            total = sum(accepted[b][k] for k in ("caps", "title", "start"))
            if sum(n for (x, y), n in phrases.items() if y == b) * 2 >= total:
                names.discard(b)
    names |= {normalize_name(p) for p in caps_people}
    return {n for n in names if n not in known and n not in _NOT_NAMES}
