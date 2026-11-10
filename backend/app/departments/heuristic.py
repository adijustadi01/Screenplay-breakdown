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
from ..extraction.heuristic_extractor import NUMBER_WORDS, SHOWN_AFTER_COLON, TITLES, VERSION_WORDS
from ..lexicon import is_english
from ..lexicon.grammar import NounGroup, Tok, noun_groups, read, split_sentences
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


# ---------------------------------------------------------------------------------------------
# Dictionary-driven finds. The built-in dictionary knows that a "tonga" is an animal-drawn
# vehicle, "clippers" a prop and a "singlet" sportswear, so the reader is not limited to the
# short word lists in each department module. The grammar tagger makes sure the word is used
# as a thing ("her rings") and not as an action ("the phone rings").
# ---------------------------------------------------------------------------------------------
_TAG_DEPARTMENT = {"veh": "vehicles", "animal": "animals", "cos": "costumes", "prop": "props", "inst": "sound_music"}
# People words that usually point back at the main cast ("the girls" = the two heroines), so
# they only count as background when the script gives a number or says "a few / some / other".
_ANAPHORIC = {"girl", "boy", "sister", "brother", "daughter", "son", "parent", "child", "kid", "woman", "man",
              "person", "people", "friend", "cousin", "guy", "lady", "other", "kids", "children", "men", "women",
              "couple", "wrestler", "player", "fighter", "opponent", "rival", "lover", "sibling", "twin",
              "youngster", "adult", "elder", "teenager", "baby", "husband", "wife", "mother", "father", "family"}
# People words that are not someone to hire.
_NOT_EXTRAS = {"one", "self", "life", "soul", "being", "individual", "party", "character", "figure", "body",
               "winner", "loser", "champion", "hero", "fool", "idiot", "coward", "liar", "stranger", "nobody",
               "somebody", "anybody", "everybody", "someone", "anyone", "everyone", "killer", "victim", "witness",
               "double", "match", "equal", "master", "subject", "head", "hand", "mate", "second", "minor",
               "major", "native", "relative", "ancestor", "god", "goddess", "lord", "devil", "angel", "ghost",
               "human", "mortal", "creature", "giant", "star", "light", "rest", "lot", "number", "group", "pair",
               "world", "public", "enemy", "ally", "leader", "follower", "owner", "user", "maker", "author",
               "reader", "viewer", "lead", "support", "help", "aid", "cut", "boss", "chief", "general", "private",
               "fan", "nut", "pill", "pain", "joke", "sport", "ace", "pro", "expert", "novice", "beginner",
               "senior", "junior", "younger", "elder", "best", "worst", "half", "counterpart", "peer", "pal"}
_INDEFINITE = {"a", "an", "another", "some", "several", "few", "many", "other", "couple", "dozens", "hundreds",
               "thousands", "dozen", "hundred", "thousand", "group", "bunch", "crowd", "handful", "number",
               "line", "row", "queue", "stream", "sea", "team", "gang", "batch", "party"}
_PREDICATE_BEFORE = {"is", "was", "are", "were", "be", "been", "being", "become", "becomes", "became", "like",
                     "as", "than", "into", "called", "named"}
_BACKGROUND_KINDS = {
    "police": {"policeman", "constable", "cop", "officer", "inspector", "policewoman", "guard", "watchman"},
    "medical_staff": {"nurse", "doctor", "surgeon", "paramedic", "orderly", "compounder"},
    "students": {"student", "pupil", "schoolgirl", "schoolboy", "schoolchild", "classmate", "trainee", "cadet"},
    "vendors": {"vendor", "hawker", "shopkeeper", "seller", "shopper", "customer", "merchant", "trader"},
    "passengers": {"passenger", "commuter", "traveller", "traveler"},
    "passersby": {"pedestrian", "passerby", "bystander", "onlooker"},
    "guests": {"guest", "invitee", "relative", "baraati"},
    "dancers": {"dancer"},
    "musicians": {"musician", "drummer", "singer", "player", "band"},
    "villagers": {"villager", "farmer", "peasant", "elder", "local", "resident", "neighbour", "neighbor"},
    "military": {"soldier", "jawan", "commando", "trooper", "sentry"},
    "henchmen": {"goon", "henchman", "thug", "gangster", "bodyguard", "bouncer"},
    "staff": {"waiter", "servant", "worker", "labourer", "laborer", "clerk", "peon", "porter", "coolie", "maid",
              "attendant", "official", "referee", "umpire", "judge", "coach", "trainer", "reporter", "journalist",
              "photographer", "cameraman", "volunteer", "employee", "staff", "driver", "conductor", "mechanic"},
    "crowd": {"crowd", "mob", "spectator", "audience", "throng", "gathering", "public", "procession", "fan",
              "supporter", "devotee", "pilgrim", "protester", "mourner", "viewer"},
}
_BACKGROUND_OF = {w: k for k, ws in _BACKGROUND_KINDS.items() for w in ws}
# Collective words that really mean a group of people to hire (not "a row of trucks").
_CROWD_WORDS = {"crowd", "mob", "band", "gang", "army", "troupe", "team", "audience", "procession", "congregation",
                "squad", "platoon", "gathering", "throng", "staff", "crew", "police", "public", "baraat", "choir",
                "orchestra", "delegation", "jury", "class", "batch", "queue"}
# "a ball of fire", "a sea of faces": the first noun is not an object.
_FIGURE_OF = {"ball", "sheet", "blanket", "curtain", "cloud", "wall", "river", "sea", "rain", "shower", "stream",
              "chain", "string", "pool", "bed", "carpet", "mountain", "pile", "heap", "bunch", "pair", "piece",
              "bit", "lot", "kind", "sort", "line", "row", "ring", "circle", "plate", "cup", "glass", "bottle",
              "bowl", "bag", "box", "bucket", "sack", "packet", "jar", "tumbler", "mug", "basket", "tray"}
_CONTAINERS = {"plate", "cup", "glass", "bottle", "bowl", "bag", "box", "bucket", "sack", "packet", "jar",
               "tumbler", "mug", "basket", "tray"}
_NOT_INSTRUMENT_WITH = {"fuel", "oil", "water", "steel", "plastic", "storage", "kerosene", "diesel", "petrol"}
_MATERIALS = {"gold", "silver", "steel", "brass", "iron", "wooden", "plastic", "leather", "cotton", "silk", "glass",
              "paper", "school", "police", "army", "wedding", "milk", "tea", "water", "hair", "cricket", "hockey",
              "wrestling", "boxing", "sports", "medical", "kitchen", "oil", "fuel", "rubber", "cloth", "stone",
              "clay", "copper", "bronze", "tin", "bamboo", "cane", "jute", "rope", "mud", "cow", "bullock",
              "horse", "tractor", "bus", "truck", "train", "railway", "hospital", "temple", "prayer"}


def dictionary_finds(spec: DepartmentSpec, toks: list[Tok], groups: list[NounGroup],
                     cast_words: frozenset[str]) -> list[dict]:
    """Things in one sentence that belong to this department, found through the dictionary.

    Each find: {start, end, category, name, confidence, details} with positions in the sentence.
    """
    key = spec.key.value
    out: list[dict] = []
    quoted = _quoted_spans(toks)
    for g in groups:
        h = g.head
        if "n" not in h.pos or h.text.upper() in cast_words:
            continue
        if any(a <= h.start < b for a, b in quoted):
            continue                                    # words written on a poster or a sign
        before = toks[g.first - 1].low if g.first > 0 else ""
        after = toks[g.head_index + 1].low if g.head_index + 1 < len(toks) else ""
        if before in ("like", "as") or after in ("'s", "’s"):
            continue                                    # "strong as a bull"; "a wrestler's turban"
        if g.head_index + 1 < len(toks) and toks[g.head_index + 1].tag in ("NNP", "NNPS"):
            continue                                    # "a vintage Ambassador": describes the name after it
        lemma = h.lemma or h.low
        if key == "background":
            if h.code not in ("P", "G") or lemma in _NOT_EXTRAS or h.low in _NOT_EXTRAS:
                continue
            if h.code == "G" and lemma not in _CROWD_WORDS:
                continue
            counted = bool(g.number) or g.determiner in _INDEFINITE or before in _INDEFINITE
            is_group = h.plural or h.code == "G"
            if lemma in _ANAPHORIC and not counted:
                continue
            if not is_group and before == "," and g.first >= 2:
                continue                                # "BALDEV, a former wrestler, ..." describes Baldev
            if not is_group and not (g.determiner in ("a", "an", "another") and before not in _PREDICATE_BEFORE):
                continue                                # "the doctor", "her coach": a known person, not an extra
            if before in _PREDICATE_BEFORE and not g.number:
                continue                                # "they are students", "like soldiers"
            cat = _BACKGROUND_OF.get(lemma, "crowd" if h.code == "G" else "other")
            mods = [m for m in g.modifiers if m.tag in ("NN", "NNS") and m.text.upper() not in cast_words][-1:]
            name = normalize_name(" ".join([*(m.low for m in mods), h.low if is_group else lemma]))
            out.append({"start": (mods[0].start if mods else h.start), "end": h.end, "category": cat, "name": name,
                        "confidence": 0.6, "details": {} if is_group else {"featured": True},
                        "single": not is_group})
            continue
        prefix, _, sub = h.kind.partition(":")
        if _TAG_DEPARTMENT.get(prefix) != key:
            continue
        if h.low == "glasses" and after == "of":
            continue                                    # "glasses of milk" are not spectacles
        if after == "of" and lemma in _FIGURE_OF and lemma not in _CONTAINERS:
            continue                                    # "a ball of fire"
        mod_words = {m.low for m in g.modifiers}
        if prefix == "inst" and mod_words & _NOT_INSTRUMENT_WITH:
            continue                                    # a fuel drum is not a drum
        cat = "instrument" if prefix == "inst" else (sub if sub in spec.categories else "other")
        if key == "props" and lemma == "note" and any("rupee" in w or w.isdigit() for w in mod_words):
            cat = "money"
        if key == "props" and lemma == "chain" and mod_words & {"gold", "silver", "neck"}:
            cat = "jewellery"
        mods = [m for m in g.modifiers if m.text.upper() not in cast_words
                and (m.tag in ("NN", "NNS") and m.code not in ("P", "G") or m.low in _MATERIALS)][-1:]
        name = normalize_name(" ".join([*(m.low for m in mods), lemma]))
        # Sure of it when the word can only be a noun, or stands after a/the/his/two.
        sure = h.pos == "n" or bool(g.determiner)
        details = {"consumable": True} if (key == "props" and sub in ("food_drink", "consumable")) else {}
        out.append({"start": (mods[0].start if mods else h.start), "end": h.end, "category": cat, "name": name,
                    "confidence": 0.7 if sure else 0.55, "details": details, "single": not h.plural})
    return out


def _quoted_spans(toks: list[Tok]) -> list[tuple[int, int]]:
    """Character ranges inside double quotes: text on a sign, a poster, a screen."""
    spans, open_at = [], None
    for t in toks:
        if t.text in ('"', "“", "”", "``", "''"):
            if open_at is None:
                open_at = t.end
            else:
                spans.append((open_at, t.start))
                open_at = None
    return spans


_NOUN_DEPARTMENTS = {"vehicles", "props", "costumes", "animals", "background"}
_PRONOUN_START = re.compile(r"^(?:he|she|his|her)\b", re.I)


class DepartmentHeuristicExtractor:
    name = "heuristic"
    model = None

    def __init__(self, specs: list[DepartmentSpec]):
        self.specs = specs
        self.prompt_version = "dept-heuristic-v3+" + ",".join(s.key.value for s in specs)
        self.cast: list[str] = []
        self.cast_tokens: dict[str, str] = {}

    def set_script_context(self, cast: list[str], locations: list[str]) -> None:
        self.cast = sorted(cast, key=len, reverse=True)
        # A bare token maps to the SHORTEST name containing it: "RAGHAV" -> RAGHAV, not OLD RAGHAV.
        # In a name of several words only the name-like words stand for the person: "Mehra" means
        # COACH MEHRA, but "wrestler" does not mean RUSSIAN WRESTLER. A title does when it is the
        # only one ("the Inspector" = INSPECTOR KHAN).
        titles: dict[str, set[str]] = {}
        for c in sorted(cast, key=len):
            toks = c.split()
            for tok in toks:
                if len(tok) <= 2 or tok in VERSION_WORDS:
                    continue
                if len(toks) == 1 or not is_english(tok):
                    self.cast_tokens.setdefault(tok, c)
                elif tok == toks[0] and tok in TITLES:
                    titles.setdefault(tok, set()).add(c)
        for tok, owners in titles.items():
            if len(owners) == 1:
                self.cast_tokens.setdefault(tok, next(iter(owners)))

    def _matches(self, spec: DepartmentSpec, sentence: str, toks: list[Tok] | None = None) -> list[tuple]:
        found = []
        for cat, words in spec.lexicon.items():
            for w in words:
                for m in re.finditer(rf"\b{w}\b", sentence, re.IGNORECASE):
                    if toks is None or self._word_list_match_ok(
                            spec, {"start": m.start(), "end": m.end(), "category": cat}, toks):
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

    def _people_in(self, sent: str, near: int) -> list[str]:
        """Cast named in the sentence: the one named last before position `near` first (the likely
        wearer / holder), then the others in reading order."""
        hits: list[tuple[int, str]] = []
        for c in self.cast:
            pos = [m.start() for m in re.finditer(rf"\b{re.escape(c)}\b", sent, re.IGNORECASE)]
            for t in c.split():
                if len(t) > 2 and self.cast_tokens.get(t) == c:
                    pos += [m.start() for m in re.finditer(rf"\b{re.escape(t)}\b", sent, re.IGNORECASE)]
            if pos:
                before = [p for p in pos if p <= near]
                hits.append((near - max(before) if before else 10_000 + min(pos), c))
        return [c for _, c in sorted(hits)]

    @staticmethod
    def _word_list_match_ok(spec: DepartmentSpec, m: dict, toks: list[Tok]) -> bool:
        """A word-list hit must be used as a thing: not a verb ("a rooster CROWS"), not the first
        half of another thing ("a TRUCK stop"), and a FALL must be a person falling."""
        idx = next((i for i, t in enumerate(toks) if t.start <= m["end"] - 1 < t.end), None)
        if idx is None:
            return True
        t = toks[idx]
        single = m["end"] - m["start"] == len(t.text) and t.start == m["start"]
        if spec.key.value in _NOUN_DEPARTMENTS:
            if single and t.tag.startswith("VB") and "n" in t.pos:
                return False
            nxt = toks[idx + 1] if idx + 1 < len(toks) else None
            if single and nxt is not None and nxt.is_noun and "n" in nxt.pos and nxt.code not in ("P", "G") \
                    and _TAG_DEPARTMENT.get(nxt.kind.split(":")[0]) != spec.key.value:
                return False                    # "a TRUCK stop"; but "Ambassador CAR" stays a car
        if m["category"] == "fall" and single:
            k = idx - 1
            while k >= 0 and toks[k].tag in ("RB", ","):
                k -= 1
            subj = toks[k] if k >= 0 else None
            if subj is None or not (subj.tag in ("NNP", "NNPS", "PRP") or subj.code in ("P", "G")):
                return False
        return True

    def extract(self, scene_text: str, ctx: SceneContext) -> dict[str, list[ItemOut]]:
        # SONG: lines are read exactly by code (sound_music.derive), not by these rules.
        # Sentences never run across script elements. Headings are read by the word lists only
        # ("EXT. TRUCK STOP" is a place, not a truck).
        sentences: list[tuple[str, bool]] = []
        for e in ctx.elements:
            if e.element_type not in (ElementType.ACTION, ElementType.SCENE_HEADING) \
                    or re.match(r"\s*SONG\s*[:\-]", e.text, re.I):
                continue
            heading = e.element_type == ElementType.SCENE_HEADING
            text = e.text
            shown = None if heading else SHOWN_AFTER_COLON.search(text)
            if shown:
                text = text[:shown.end()]       # what a photograph shows is not on set
            sentences += [(x, heading) for x in ([text.strip()] if heading else split_sentences(text))]
        cast_words = frozenset(self.cast_tokens) | frozenset(t for c in self.cast for t in c.split())
        parsed = {}
        for sent, heading in sentences:
            if not heading and sent not in parsed:
                toks = read(sent, cast_words)
                parsed[sent] = (toks, noun_groups(toks))
        out: dict[str, list[ItemOut]] = {}
        for spec in self.specs:
            found: dict[str, ItemOut] = {}
            last_subject: str | None = None
            for sent, heading in sentences:
                if not heading:
                    here = self._people_in(sent, 0)
                    if len(here) == 1:
                        last_subject_next = here[0]         # one person named: "she" next means them
                    else:
                        last_subject_next = last_subject    # nobody, or several: keep who we had
                toks, groups = (None, []) if heading else parsed[sent]
                matches = [{"start": s, "end": e, "category": cat, "ns": ns, "ne": ne}
                           for s, e, cat, ns, ne in self._matches(spec, sent, toks)]
                if not heading:
                    for f in dictionary_finds(spec, toks, groups, cast_words):
                        if all(f["end"] <= m["start"] or f["start"] >= m["end"] for m in matches):
                            matches.append(f)
                    matches.sort(key=lambda m: m["start"])
                for m in matches:
                    s, e, cat = m["start"], m["end"], m["category"]
                    from_dictionary = "name" in m
                    noun = m["name"] if from_dictionary else normalize_name(
                        cat.replace("_", " ") if cat in spec.offline_category_names else sent[m["ns"]:m["ne"]])
                    before = sent[:s]
                    qty, qtext = None, None
                    nm = _NUM_RE.search(before)
                    if nm and _number(nm.group(1)) is not None:
                        qty, qtext = _number(nm.group(1)), (nm.group(0) + sent[s:e]).strip()
                    elif spec.quantity_matters and not m.get("single"):
                        # Headcounts are never implied: only a stated number counts.
                        art = _SINGULAR_RE.search(before)
                        qtext = ((art.group(0) if art else "") + sent[s:e]).strip()
                    elif m.get("single") or (not from_dictionary and not _plural(noun)):
                        qty = 1
                    by_category = cat in spec.offline_category_names and not from_dictionary
                    if by_category:
                        qty, qtext = None, None          # events/effects are not counted units
                    owner = None
                    pm = None if by_category else _POSSESSIVE_RE.search(before)
                    who = self._people_in(sent, s)
                    carried = False
                    if not who and not heading and _PRONOUN_START.match(sent) and last_subject:
                        who, carried = [last_subject], True      # "He takes a poster out of his kurta"
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
                    details = spec.offline_details(sent) if spec.offline_details else {}
                    details = {**details, **{k: v for k, v in m.get("details", {}).items()
                                             if k in spec.detail_names()}}
                    conf = m.get("confidence", 0.5)
                    found[name] = ItemOut(
                        name=name, category=cat, description=sent.strip()[:200], quantity_text=qtext,
                        quantity_min=qty, quantity_max=qty, characters=chars, details=details,
                        evidence=sent[s:e] if not qtext else qtext,
                        confidence=min(conf, 0.55) if carried and chars else conf,
                        uncertainty_note=(f"'{sent.split()[0]}' taken to mean {who[0]} (the last person named)"
                                          if carried and chars else None))
                if not heading:
                    last_subject = last_subject_next
            # "HAIR CLIPPERS" and "CLIPPERS" in one scene are the same thing: keep the fuller name.
            for short in [n for n in found if any(o != n and o.endswith(" " + n.split(" — ")[-1])
                                                  and o.split(" — ")[0] == n.split(" — ")[0] or
                                                  (" — " not in n and " — " not in o and o != n
                                                   and o.endswith(" " + n)) for o in found)]:
                del found[short]
            out[spec.key.value] = list(found.values())
        return out
