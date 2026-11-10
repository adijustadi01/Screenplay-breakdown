"""One character, different ages -> different actors.

A script may call the hero ARJUN all the way through, yet introduce him as "ARJUN (6)" in the
village and "ARJUN (28)" after "TWENTY YEARS LATER". He is ONE character (all reports count him
once) but TWO casting requirements: no single actor plays a 6-year-old and a 28-year-old.

This module reads ages from the script text in code (no API key needed), carries each one
forward to the character's later scenes until the story jumps in time, groups ages that are
too far apart for one actor, and proposes casting roles in the review queue. Nothing changes
until the AD accepts, and it can be undone.

Age evidence, strongest first:
    "ARJUN (6)", "ARJUN (early 30s)", "ARJUN (age 6)", "ARJUN, now 28,", "ARJUN, 28,"
    a dialogue cue "ARJUN (6)" or "ARJUN (YOUNG)"
    "LITTLE ARJUN", "TEENAGE ARJUN", "OLD ARJUN", "ARJUN (CHILD)"
    the age the LLM noted for that scene
Time jumps that stop an age being carried forward:
    "TWENTY YEARS LATER", "YEARS EARLIER", "PRESENT DAY", "FLASHBACK", a year in the heading "(1975)",
    "SUPER: 2004"
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..extraction.cues import parse_cue
from ..models import Appearance, CastingRole, Character, CharacterAlias, Scene, ScriptElement
from ..models.enums import APPEARANCE_PRESENCES, ElementType, EntityStatus

# Word -> (youngest, oldest) an actor for it would be. None = relative ("young" only means
# "younger than elsewhere in this script").
STAGE_WORDS: dict[str, tuple[int, int] | None] = {
    "BABY": (0, 2), "INFANT": (0, 1), "NEWBORN": (0, 0), "TODDLER": (1, 3),
    "LITTLE": (3, 12), "CHILD": (3, 12), "KID": (4, 12), "BOY": (4, 12), "GIRL": (4, 12), "SCHOOLBOY": (6, 14),
    "SCHOOLGIRL": (6, 14), "TEEN": (13, 19), "TEENAGE": (13, 19), "TEENAGER": (13, 19), "TEENAGED": (13, 19),
    "ADULT": (20, 59), "GROWN": (20, 59), "GROWN-UP": (20, 59), "MIDDLE-AGED": (40, 60),
    "OLD": (60, 90), "ELDERLY": (60, 95), "AGED": (60, 95),
    "TEENS": (13, 19), "GROWN UP": (20, 59), "GROWN-UP": (20, 59),
    # Hindi words often used in English-language Indian scripts: chhoti/chhota = little.
    "CHHOTI": (3, 12), "CHOTI": (3, 12), "CHHOTA": (3, 12), "CHOTA": (3, 12), "BACHPAN": (3, 12),
    "YOUNG": None, "YOUNGER": None, "OLDER": None,
}
_NUM_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9,
              "TEN": 10, "ELEVEN": 11, "TWELVE": 12, "FIFTEEN": 15, "TWENTY": 20, "THIRTY": 30, "FORTY": 40,
              "FIFTY": 50}
_JUMP_RE = re.compile(
    r"\b(?:\d+|" + "|".join(_NUM_WORDS) + r"|SEVERAL|MANY|A FEW|SOME)\s+(?:\w+\s+)?YEARS?\s+(?:LATER|EARLIER|AGO|BEFORE|ON|PASS)\b"
    r"|\bPRESENT DAY\b|\bBACK TO (?:THE )?PRESENT\b|\bEND (?:OF )?FLASHBACK\b|\bSUPER:?[^\n]*\b(?:19|20)\d\d\b"
    r"|\bYEARS (?:LATER|EARLIER|AGO|PASS)\b",
    re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")
_FLASHBACK_RE = re.compile(r"\b(?:FLASHBACK|FLASH BACK|DREAM|MEMORY)\b", re.IGNORECASE)


@dataclass
class Evidence:
    low: int | None           # None: relative word ("YOUNG") with no number
    high: int | None
    note: str                 # as written: "6", "30s", "young"
    quote: str


@dataclass
class SceneAge:
    appearance: Appearance
    scene: Scene
    evidence: Evidence | None = None
    carried_from: Scene | None = None
    epoch: int | None = None          # main story only: how many time jumps ("YEARS LATER") came before


@dataclass
class AgeGroup:
    low: int | None
    high: int | None
    notes: set[str] = field(default_factory=set)
    items: list[SceneAge] = field(default_factory=list)

    @property
    def mid(self) -> float:
        return ((self.low or 0) + (self.high or 0)) / 2

    jump_side: str | None = None      # "AFTER TIME JUMP": no age written, placed by a time jump

    def label(self) -> str:
        """BABY, CHILD, TEEN, YOUNG, MIDDLE-AGED or OLD; 'YOUNG / MIDDLE-AGED' when the ages span two."""
        if self.jump_side:
            return self.jump_side
        if self.low is None:
            return " / ".join(sorted(self.notes)).upper()
        mids = sorted({(i.evidence.low + i.evidence.high) / 2 for i in self.items
                       if i.evidence and i.evidence.low is not None} or {self.mid})
        bands = list(dict.fromkeys(band_of(m) for m in mids))
        return " / ".join(bands)


def band_of(age: float) -> str:
    """How an AD would describe the age: 0-2 baby, 3-12 child, 13-19 teen, 20-39 young,
    40-59 middle-aged, 60+ old."""
    return ("BABY" if age < 3 else "CHILD" if age < 13 else "TEEN" if age < 20 else "YOUNG" if age < 40
            else "MIDDLE-AGED" if age < 60 else "OLD")


def _age_from(num: str, decade: str | None, qualifier: str | None) -> tuple[int, int, str]:
    n = int(num)
    if decade:
        lo, hi = n, n + 9
        if qualifier:
            q = qualifier.lower()
            lo, hi = (n, n + 3) if q == "early" else (n + 7, n + 9) if q == "late" else (n + 3, n + 6)
        return lo, hi, f"{(qualifier + ' ') if qualifier else ''}{n}s"
    return n, n, str(n)


def _name_alt(names: list[str]) -> str:
    alts = sorted({re.escape(n) for n in names if n}, key=len, reverse=True)
    return "(?:" + "|".join(alts) + ")"


_UNITS = ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN",
          "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
_TENS = {"TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50, "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80,
         "NINETY": 90}
# "10", "ten", "twenty-two"
_NUM = (r"(\d{1,2}|(?:" + "|".join(_TENS) + r")(?:[\s-](?:" + "|".join(_UNITS[1:10]) + r"))?|"
        + "|".join(sorted(_UNITS[1:], key=len, reverse=True)) + r")")


def _to_int(num: str) -> int:
    if num.isdigit():
        return int(num)
    parts = re.split(r"[\s-]+", num.upper())
    if parts[0] in _TENS:
        return _TENS[parts[0]] + (_UNITS.index(parts[1]) if len(parts) > 1 else 0)
    return _UNITS.index(parts[0])


def _evidence_in(text: str, names: list[str]) -> Evidence | None:
    """Look for this character's age in one scene's action / cue text."""
    if not names:
        return None
    nm = _name_alt(names)
    q = r"(?:(early|mid|late)[\s-]+)?"
    yrs = r"(?:years?|yrs?\.?|saal)"
    before = r"(?<![\w'-])"
    pats = [
        # ARJUN (6) / ARJUN (age 6) / ARJUN (aged about 6) / ARJUN (CHILD - 6) / ARJUN (early 30s)
        (rf"{before}{nm}\s*\(\s*[^)\d]{{0,20}}?{q}(\d{{1,2}})(0s|s)?\b[^)]*\)", True),
        # ARJUN, 28, ... / ARJUN, now 28. / ARJUN, aged 6,
        (rf"{before}{nm},\s*(?:now\s+|age[d]?\s+|about\s+)?{q}(\d{{1,2}})(0s|s)?(?:\s+{yrs}(?:\s+old)?)?\s*[,.;)]",
         True),
        # ARJUN - 10 YRS / ARJUN: 10 years
        (rf"{before}{nm}\s*[-–—:]\s*()(\d{{1,2}})()\s*{yrs}", True),
        # 10-year-old ARJUN / a ten year old ARJUN
        (rf"{before}()\b{_NUM}[\s-]*{yrs}[\s-]*old\s+{nm}(?![\w'])()", False),
        # ARJUN, a 10-year-old girl / ARJUN, now a ten year old
        (rf"{before}{nm},?\s+(?:now\s+)?(?:an?\s+)?()\b{_NUM}[\s-]*{yrs}[\s-]*old\b()", False),
        # ARJUN, now a young woman of 19
        (rf"{before}{nm}\b[^.\n]{{0,50}}?\b(?:woman|man|girl|boy|lady|teenager|child|kid)\s+of\s+(){_NUM}\b()",
         False),
    ]
    for p, digits_only in pats:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            qual, num, dec = m.group(1) or None, m.group(2), m.group(3) or None
            if dec == "0s":
                num, dec = num + "0", "s"
            lo, hi, note = _age_from(str(_to_int(num)) if not digits_only else num, dec, qual)
            return Evidence(lo, hi, note, m.group(0))
    words = "|".join(sorted((re.escape(w).replace(r"\ ", r"[\s-]") for w in STAGE_WORDS), key=len, reverse=True))
    for p in (rf"{before}({words})\s+{nm}(?![\w'])", rf"{before}{nm}\s*\(\s*(?:early\s+|mid\s+|late\s+)?({words})\s*\)",
              rf"{before}{nm}\s+as\s+an?\s+({words})\b"):
        m = re.search(p, text, re.IGNORECASE)
        if m:
            w = re.sub(r"[\s-]+", " ", m.group(1).upper())
            w = w if w in STAGE_WORDS else w.replace(" ", "-")
            rng = STAGE_WORDS[w]
            return Evidence(rng[0] if rng else None, rng[1] if rng else None, w.lower(), m.group(0))
    return None


def _evidence_from_note(note: str | None) -> Evidence | None:
    """The LLM's per-scene age note: '10', '30s', 'young'."""
    if not note:
        return None
    m = re.search(r"(?:(early|mid|late)\s+)?(\d{1,2})(s)?", note, re.IGNORECASE)
    if m:
        lo, hi, n = _age_from(m.group(2), m.group(3), m.group(1))
        return Evidence(lo, hi, n, note)
    w = note.strip().upper()
    if w in STAGE_WORDS:
        rng = STAGE_WORDS[w]
        return Evidence(rng[0] if rng else None, rng[1] if rng else None, w.lower(), note)
    return None


def _era(scene: Scene, text: str) -> tuple[str, bool]:
    """(era key, starts with a time jump). Ages carry forward only inside one era."""
    heading = scene.heading_raw or ""
    year = _YEAR_RE.search(heading)
    if year:
        return f"year:{year.group(1)}", False
    if _FLASHBACK_RE.search(heading):
        return "flashback", False
    return "main", bool(_JUMP_RE.search(text))


def _too_far_apart(a: AgeGroup, b: AgeGroup) -> bool:
    """Would one actor struggle to play both? Children change fast; adults slowly."""
    young = min(a.mid, b.mid)
    return abs(a.mid - b.mid) >= (4 if young < 18 else 15)


def character_age_groups(session: Session, version_id: int, character: Character,
                         scenes: list[Scene], text_by_scene: dict[int, str], cue_by_scene: dict[int, str],
                         apps: list[Appearance]) -> tuple[list[AgeGroup], list[SceneAge]]:
    """Return (age groups, scenes whose age is unknown) for the character's unassigned appearances."""
    names = {character.canonical_name} | {a.alias for a in session.scalars(
        select(CharacterAlias).where(CharacterAlias.character_id == character.id))}
    names = [n for n in names if n and len(n) >= 2]
    own_word = character.canonical_name.split()[0]
    by_scene = {a.scene_id: a for a in apps}
    current: dict[str, tuple[Evidence, Scene]] = {}
    prev_era = None
    results: list[SceneAge] = []
    epoch = 0
    for sc in scenes:
        text = text_by_scene.get(sc.id, "")
        era, jump = _era(sc, text)
        if jump:
            current.pop("main", None)
            epoch += 1
        if prev_era == "flashback" and era != "flashback":
            current.pop("flashback", None)
        prev_era = era
        a = by_scene.get(sc.id)
        if a is None or a.presence not in APPEARANCE_PRESENCES:
            continue        # voice-overs and mentions are not on-screen: an adult narrator is not the child
        ev = (_evidence_in(text, names) or _evidence_in(cue_by_scene.get(sc.id, ""), names)
              or _evidence_from_note(a.version_note))
        if ev is None and own_word in STAGE_WORDS:          # the name itself: "YOUNG ARJUN"
            rng = STAGE_WORDS[own_word]
            ev = Evidence(rng[0] if rng else None, rng[1] if rng else None, own_word.lower(),
                          character.canonical_name)
        ep = epoch if era == "main" else None
        if ev is not None:
            current[era] = (ev, sc)
            results.append(SceneAge(a, sc, ev, epoch=ep))
        elif era in current:
            ev0, src = current[era]
            results.append(SceneAge(a, sc, ev0, carried_from=src, epoch=ep))
        else:
            results.append(SceneAge(a, sc, epoch=ep))

    known = [r for r in results if r.evidence is not None]
    unknown = [r for r in results if r.evidence is None]
    numeric = sorted((r for r in known if r.evidence.low is not None),
                     key=lambda r: (r.evidence.low + r.evidence.high) / 2)
    groups: list[AgeGroup] = []
    for r in numeric:
        g = AgeGroup(r.evidence.low, r.evidence.high, {r.evidence.note}, [r])
        if groups and not _too_far_apart(groups[-1], g):
            last = groups[-1]
            last.low, last.high = min(last.low, g.low), max(last.high, g.high)
            last.notes |= g.notes
            last.items.append(r)
        else:
            groups.append(g)
    # Relative words: "YOUNG ARJUN" joins the youngest group, "OLDER ARJUN" the oldest; with no
    # numbers anywhere, each word forms its own group.
    word_groups: dict[str, AgeGroup] = {}
    for r in (r for r in known if r.evidence.low is None):
        word = r.evidence.note.upper()
        if groups:
            target = groups[0] if word in ("YOUNG", "YOUNGER") else groups[-1]
        else:
            target = word_groups.setdefault(word, AgeGroup(None, None, {r.evidence.note}))
        target.items.append(r)
        target.notes.add(r.evidence.note)
    groups += list(word_groups.values())

    # No age written, but the scenes sit on the other side of a time jump ("FIFTEEN YEARS LATER") from
    # the scenes that do give an age: probably a different age too. Offered as its own group — only
    # when some age is written somewhere, so a parent who simply lives through the jump is left alone.
    if groups:
        known_epochs = {r.epoch for r in known if r.epoch is not None}
        by_epoch: dict[int, list[SceneAge]] = defaultdict(list)
        for r in unknown:
            if r.epoch is not None and known_epochs and r.epoch not in known_epochs:
                by_epoch[r.epoch].append(r)
        def youngest_mid(rs) -> float | None:
            mids = [(r.evidence.low + r.evidence.high) / 2 for r in rs if r.evidence.low is not None]
            if any(r.evidence.low is None and r.evidence.note.upper() in ("YOUNG", "YOUNGER") for r in rs):
                mids.append(10)
            return min(mids) if mids else None

        for ep, items in sorted(by_epoch.items()):
            if ep > max(known_epochs):
                side, ref = "AFTER TIME JUMP", youngest_mid([r for r in known if r.epoch is not None and r.epoch < ep])
                limit = 20      # a child or teen before the jump has grown up after it
            elif ep < min(known_epochs):
                side, ref = "BEFORE TIME JUMP", youngest_mid([r for r in known if r.epoch is not None and r.epoch > ep])
                limit = 30      # a young adult after the jump was a child before it
            else:
                continue
            if ref is None or ref >= limit:
                continue        # adults barely change over a jump: same actor, nothing to suggest
            g = AgeGroup(None, None, {side.lower()}, items)
            g.jump_side = side
            groups.append(g)
            unknown = [r for r in unknown if r not in items]
    return groups, unknown


def _scene_label(sc: Scene) -> str:
    return sc.scene_number or f"#{sc.seq}"


def _scene_list(items: list[SceneAge], limit: int = 12) -> str:
    labels = [_scene_label(i.scene) + (" (flashback)" if _FLASHBACK_RE.search(i.scene.heading_raw or "") else "")
              for i in sorted(items, key=lambda i: i.scene.seq)]
    return ", ".join(labels[:limit]) + (f" and {len(labels) - limit} more" if len(labels) > limit else "")


def _ages_text(notes) -> str:
    ages = sorted((n for n in notes if any(ch.isdigit() for ch in n)),
                  key=lambda n: int(re.sub(r"\D", "", n) or 0))
    return ", ".join(ages)


def compose_role_name(character_name: str, bands: list[str], notes) -> str:
    """'ARJUN — CHILD (10)', 'ARJUN — YOUNG + MIDDLE-AGED (28, 50)'."""
    detail = _ages_text(notes)
    return f"{character_name} — {' + '.join(dict.fromkeys(bands))}" + (f" ({detail})" if detail else "")


def role_name(character: Character, g: AgeGroup) -> str:
    return compose_role_name(character.canonical_name, [g.label()], g.notes)


def propose_age_roles(session: Session, version_id: int, add) -> None:
    """Add an 'age_roles' proposal for every character who appears at clearly different ages."""
    v_scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id)
                               .order_by(Scene.seq)).all()
    if not v_scenes:
        return
    text_by_scene: dict[int, list[str]] = defaultdict(list)
    cue_by_scene: dict[int, list[str]] = defaultdict(list)
    for e in session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                             .order_by(ScriptElement.seq)):
        if not e.scene_id:
            continue
        if e.element_type in (ElementType.ACTION, ElementType.SCENE_HEADING, ElementType.TRANSITION,
                              ElementType.SHOT):
            text_by_scene[e.scene_id].append(e.text)
        elif e.element_type == ElementType.CHARACTER:
            name, exts = parse_cue(e.text)
            for x in exts:
                cue_by_scene[e.scene_id].append(f"{name} ({x})")
    texts = {k: "\n".join(v) for k, v in text_by_scene.items()}
    cues = {k: "\n".join(v) for k, v in cue_by_scene.items()}
    scene_ids = [s.id for s in v_scenes]
    apps_by_char: dict[int, list[Appearance]] = defaultdict(list)
    for a in session.scalars(select(Appearance).where(Appearance.scene_id.in_(scene_ids),
                                                      Appearance.casting_role_id.is_(None))):
        apps_by_char[a.character_id].append(a)

    for cid, apps in apps_by_char.items():
        ch = session.get(Character, cid)
        if ch is None or ch.status != EntityStatus.ACTIVE:
            continue
        groups, unknown = character_age_groups(session, version_id, ch, v_scenes, texts, cues, apps)
        if len(groups) < 2:
            continue
        lines, plan = [], []
        for g in groups:
            name = role_name(ch, g)
            carried = [i for i in g.items if i.carried_from is not None]
            stated = sorted({i.evidence.quote for i in g.items if i.evidence and i.carried_from is None})[:3]
            line = f"• {name}: scene(s) {_scene_list(g.items)}"
            if g.jump_side:
                line += (" — no age is written for these, but they come "
                         f"{g.jump_side.lower().replace(' time jump', '')} a time jump (e.g. 'YEARS LATER'). Please check")
            if carried:
                line += f" ({len(carried)} of them carry the age from an earlier scene — please check)"
            if stated:
                line += f". Script says: {'; '.join(repr(s) for s in stated)}"
            lines.append(line)
            plan.append({"name": name, "band": g.label(), "notes": sorted(g.notes),
                         "age_note": ", ".join(sorted(g.notes)),
                         "scene_count": len({i.scene.id for i in g.items}),
                         "appearance_ids": sorted(i.appearance.id for i in g.items)})
        if unknown:
            lines.append(f"• Age not clear: scene(s) {_scene_list(unknown)} — choose the role for these yourself.")
        msg = (f"{ch.canonical_name} appears at quite different ages, which usually means different actors.\n"
               + "\n".join(lines)
               + f"\nThis is only a suggestion — the director decides. Choose below which ages one actor "
               f"plays (e.g. the same actor de-aged with VFX), then accept. {ch.canonical_name} stays ONE "
               f"character in every report; the Schedule & DOOD list each actor separately.")
        sig = "|".join(f"{p['name']}:{','.join(map(str, p['appearance_ids']))}" for p in plan)
        add(f"age_roles:{ch.id}:{hashlib.sha1(sig.encode()).hexdigest()[:12]}", "age_roles", msg,
            {"action": "age_roles", "character_id": ch.id, "roles": plan}, 0.75, ("characters", ch.id))


def merge_plans(character_name: str, roles: list[dict], actors: list | None) -> list[dict]:
    """Combine the age groups the AD says one actor plays. `actors[i]` labels group i's actor;
    groups with the same label become one casting role. None = one actor per group."""
    if not actors:
        return roles
    if len(actors) != len(roles):
        raise ValueError("choose an actor for every age")
    if len(set(map(str, actors))) == 1:          # one actor for every age
        ids = sorted(i for r in roles for i in r["appearance_ids"])
        notes = [n for r in roles for n in r.get("notes", [])]
        return [{"name": character_name, "age_note": ", ".join(notes) or None, "appearance_ids": ids}]
    merged: dict[str, dict] = {}
    for r, who in zip(roles, actors):
        m = merged.setdefault(str(who), {"bands": [], "notes": [], "appearance_ids": []})
        m["bands"].append(r.get("band") or r["name"].split(" — ", 1)[-1].split(" (")[0])
        m["notes"] += r.get("notes") or [r.get("age_note") or ""]
        m["appearance_ids"] += r["appearance_ids"]
    out = []
    for m in merged.values():
        out.append({"name": compose_role_name(character_name, m["bands"], m["notes"]),
                    "age_note": ", ".join(n for n in m["notes"] if n) or None,
                    "appearance_ids": sorted(m["appearance_ids"])})
    return out


def apply_age_roles(session: Session, character_id: int, roles: list[dict], actor: str,
                    actors: list | None = None) -> dict:
    from ..models.enums import CastingRoleKind, ExtractorKind
    from ..review.audit import log_event
    from .apply import ProposalError, _resolve_char
    ch = _resolve_char(session, character_id)
    try:
        roles = merge_plans(ch.canonical_name, roles, actors)
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc
    created, assigned = [], []
    existing = {r.name: r for r in session.scalars(select(CastingRole).where(CastingRole.character_id == ch.id))}
    for plan in roles:
        role = existing.get(plan["name"])
        if role is None:
            role = CastingRole(project_id=ch.project_id, character_id=ch.id, name=plan["name"],
                               kind=CastingRoleKind.ACTOR, age_note=(plan.get("age_note") or None),
                               extracted_by=ExtractorKind.RECONCILIATION)
            session.add(role)
            session.flush()
            created.append(role.id)
            log_event(session, ch.project_id, role, "create", actor, note=f"age-based casting role for {ch.canonical_name}")
        for aid in plan["appearance_ids"]:
            a = session.get(Appearance, aid)
            if a is not None and a.character_id == ch.id and a.casting_role_id is None:
                a.casting_role_id = role.id
                assigned.append(a.id)
    session.flush()
    return {"type": "age_roles", "character_id": ch.id, "created_roles": created, "assigned": assigned,
            "actors": actors}


def undo_age_roles(session: Session, record: dict, actor: str) -> None:
    from ..review.audit import log_event
    for aid in record.get("assigned", []):
        a = session.get(Appearance, aid)
        if a is not None:
            a.casting_role_id = None
    session.flush()
    for rid in record.get("created_roles", []):
        r = session.get(CastingRole, rid)
        if r is not None and not session.scalars(select(Appearance).where(Appearance.casting_role_id == rid)).first():
            session.delete(r)
    session.flush()
    ch = session.get(Character, record["character_id"])
    if ch is not None:
        log_event(session, ch.project_id, ch, "undo_age_roles", actor)
