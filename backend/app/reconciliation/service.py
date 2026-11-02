"""Whole-script reconciliation: find aliases, casting variants and location variants.

Produces PROPOSALS in the review queue. Nothing is merged until the AD accepts;
every accepted merge is recorded so it can be undone.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..extraction.heuristic_extractor import TITLES, VERSION_WORDS
from ..models import Appearance, Character, Location, ReviewItem, SceneLocation, ScriptVersion
from ..models.enums import EntityStatus, ReviewCategory, Severity

FUZZY_THRESHOLD = 0.88


def loose_key(name: str) -> str:
    """'ARJUN'S HOUSE' == 'ARJUNS HOUSE' == 'Arjun’s  House'; 'DR. RAO' == 'DR RAO'."""
    return re.sub(r"[^A-Z0-9]", "", name.upper().replace("’", "'"))


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def propose(session: Session, version_id: int) -> dict[str, int]:
    v = session.get(ScriptVersion, version_id)
    pid = v.project_id
    existing = {
        (i.proposal or {}).get("key")
        for i in session.scalars(select(ReviewItem).where(
            ReviewItem.project_id == pid, ReviewItem.category == ReviewCategory.RECONCILIATION))
    }
    made = Counter()

    def add(key: str, reason: str, message: str, proposal: dict, conf: float, entity: tuple[str, int]):
        if key in existing:
            return
        existing.add(key)
        proposal["key"] = key
        session.add(ReviewItem(
            project_id=pid, script_version_id=version_id, category=ReviewCategory.RECONCILIATION,
            reason_code=reason, severity=Severity.WARNING, message=message, proposal=proposal,
            confidence=round(conf, 3), entity_table=entity[0], entity_id=entity[1]))
        made[reason] += 1

    # ---------------------------------------------------------------- characters
    chars = session.scalars(select(Character).where(
        Character.project_id == pid, Character.status == EntityStatus.ACTIVE)).all()
    counts = Counter(a.character_id for a in session.scalars(select(Appearance)))
    by_name = {c.canonical_name: c for c in chars}

    def scenes_of(c: Character) -> str:
        return f"{counts.get(c.id, 0)} scene(s)"

    for c in chars:
        toks = c.canonical_name.split()
        # YOUNG ARJUN -> casting variant of ARJUN
        if len(toks) > 1 and toks[0] in VERSION_WORDS:
            base = by_name.get(" ".join(toks[1:]))
            if base is not None and base.id != c.id:
                add(f"variant:{c.id}:{base.id}", "casting_variant",
                    f"{c.canonical_name} ({scenes_of(c)}) looks like a different version of {base.canonical_name} "
                    f"({scenes_of(base)}). Accept to make them ONE character with a separate casting role "
                    f"'{c.canonical_name}'.",
                    {"action": "casting_variant", "variant_id": c.id, "character_id": base.id}, 0.85,
                    ("characters", c.id))
                continue
        # INSPECTOR SHARMA <- SHARMA
        if len(toks) > 1 and toks[0] in TITLES:
            short = by_name.get(" ".join(toks[1:]))
            if short is not None and short.id != c.id:
                add(f"merge_char:{short.id}:{c.id}", "character_alias",
                    f"'{short.canonical_name}' ({scenes_of(short)}) is probably the same person as "
                    f"'{c.canonical_name}' ({scenes_of(c)}). Accept to merge into {c.canonical_name}.",
                    {"action": "merge_character", "from_id": short.id, "into_id": c.id}, 0.85,
                    ("characters", short.id))

    seen_pairs = set()
    for i, a in enumerate(chars):
        for b in chars[i + 1:]:
            ka, kb = loose_key(a.canonical_name), loose_key(b.canonical_name)
            if not ka or not kb or _digits(ka) != _digits(kb):
                continue
            if ka == kb:
                ratio = 1.0
            elif min(len(ka), len(kb)) >= 4:
                ratio = difflib.SequenceMatcher(None, ka, kb).ratio()
            else:
                continue
            if ratio < FUZZY_THRESHOLD:
                continue
            small, big = sorted((a, b), key=lambda c: counts.get(c.id, 0))
            pair = frozenset((a.id, b.id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            add(f"merge_char:{small.id}:{big.id}", "character_spelling",
                f"'{small.canonical_name}' ({scenes_of(small)}) and '{big.canonical_name}' ({scenes_of(big)}) "
                f"look like spellings of the same name (similarity {ratio:.0%}). Accept to merge.",
                {"action": "merge_character", "from_id": small.id, "into_id": big.id}, ratio,
                ("characters", small.id))

    # ---------------------------------------------------------------- locations
    locs = session.scalars(select(Location).where(
        Location.project_id == pid, Location.status == EntityStatus.ACTIVE)).all()
    lcount = Counter(sl.location_id for sl in session.scalars(select(SceneLocation)))
    children: dict[int | None, list[Location]] = {}
    for loc in locs:
        children.setdefault(loc.parent_id, []).append(loc)

    def subtree_scenes(loc: Location) -> int:
        return lcount.get(loc.id, 0) + sum(subtree_scenes(ch) for ch in children.get(loc.id, []))

    for parent_id, group in children.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ka, kb = loose_key(a.name), loose_key(b.name)
                if _digits(ka) != _digits(kb):
                    continue
                ratio = 1.0 if ka == kb else (
                    difflib.SequenceMatcher(None, ka, kb).ratio() if min(len(ka), len(kb)) >= 4 else 0)
                if ratio < FUZZY_THRESHOLD:
                    continue
                # Prefer the spelling with punctuation (ARJUN'S over ARJUNS), then the busier one.
                small, big = sorted((a, b), key=lambda x: ("'" in x.name, subtree_scenes(x)))
                add(f"merge_loc:{small.id}:{big.id}", "location_variant",
                    f"Location '{small.name}' ({subtree_scenes(small)} scene(s)) looks like a variant of "
                    f"'{big.name}' ({subtree_scenes(big)} scene(s)). Accept to merge (sub-locations move too).",
                    {"action": "merge_location", "from_id": small.id, "into_id": big.id}, ratio,
                    ("locations", small.id))

    # HOSPITAL ROOFTOP -> CITY HOSPITAL > ROOFTOP
    tops = children.get(None, [])
    for loc in tops:
        if children.get(loc.id):
            continue
        toks = loc.name.split()
        for p in tops:
            if p.id == loc.id:
                continue
            head = p.name.split()[-1]
            if head in toks and toks.index(head) < len(toks) - 1 and loose_key(p.name) != loose_key(loc.name):
                rest = " ".join(toks[toks.index(head) + 1:])
                add(f"reparent:{loc.id}:{p.id}", "location_parent",
                    f"'{loc.name}' may be part of '{p.name}'. Accept to file it as {p.name} > {rest}.",
                    {"action": "reparent_location", "location_id": loc.id, "parent_id": p.id, "new_name": rest},
                    0.6, ("locations", loc.id))
                break

    # ------------------------------------------------------------ department items (Stage 2)
    from ..models import BreakdownItem, BreakdownOccurrence
    items = session.scalars(select(BreakdownItem).where(
        BreakdownItem.project_id == pid, BreakdownItem.status == EntityStatus.ACTIVE)).all()
    icount = Counter(o.item_id for o in session.scalars(select(BreakdownOccurrence)))
    by_dept: dict = {}
    for it in items:
        by_dept.setdefault(it.department, []).append(it)
    for dept, group in by_dept.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ka, kb = loose_key(a.name), loose_key(b.name)
                if not ka or not kb or _digits(ka) != _digits(kb):
                    continue
                if a.character_id and b.character_id and a.character_id != b.character_id:
                    continue        # two different people's things
                ta, tb = set(a.name.split()), set(b.name.split())
                ratio = 1.0 if ka == kb else difflib.SequenceMatcher(None, ka, kb).ratio()
                subset = (ta < tb or tb < ta) and min(len(ta), len(tb)) >= 1 and a.category == b.category
                if ratio < FUZZY_THRESHOLD and not subset:
                    continue
                small, big = sorted((a, b), key=lambda x: (icount.get(x.id, 0), len(x.name)))
                why = "spelling" if ratio >= FUZZY_THRESHOLD else "shorter name"
                add(f"merge_item:{small.id}:{big.id}", "item_variant",
                    f"{dept.value.replace('_', ' ').title()}: '{small.name}' ({icount.get(small.id, 0)} scene(s)) may be "
                    f"the same as '{big.name}' ({icount.get(big.id, 0)} scene(s)) ({why}). Accept to merge — "
                    f"only if it is the SAME object, not just the same kind.",
                    {"action": "merge_item", "from_id": small.id, "into_id": big.id},
                    ratio if ratio >= FUZZY_THRESHOLD else 0.55, ("breakdown_items", small.id))

    session.flush()
    return dict(made)

