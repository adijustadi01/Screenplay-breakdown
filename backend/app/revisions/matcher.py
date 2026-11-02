"""Match scenes between two drafts. Deterministic; doubtful matches are flagged.

1. Same preserved scene number, and the content agrees (or the heading is the same) -> NUMBER.
   A number whose content changed completely is NOT trusted (number reused for a new scene).
2. Remaining scenes: text similarity, best pairs first, order-preserving -> CONTENT
   (catches renumbered and unnumbered scenes). Below 0.85 similarity it is flagged.
3. Leftovers: new scenes -> ADDED, old scenes -> REMOVED. An old number that the new draft
   marks OMITTED -> OMITTED.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Scene, ScriptElement
from ..models.enums import ElementType, LineageStatus, MatchMethod

NUMBER_MIN_SIMILARITY = 0.5
CONTENT_MIN_SIMILARITY = 0.6
CONFIDENT_SIMILARITY = 0.85
SKIP = {ElementType.PAGE_HEADER, ElementType.NON_PRINTING, ElementType.TITLE_PAGE, ElementType.SCENE_HEADING}


@dataclass
class SceneText:
    scene: Scene
    body: str          # normalised body text (no heading, no page furniture)
    heading_key: str


@dataclass
class Match:
    old: Scene | None
    new: Scene | None
    status: LineageStatus
    method: MatchMethod
    similarity: float | None
    renumbered: bool = False
    heading_changed: bool = False
    reasons: tuple[str, ...] = ()


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def load_texts(session: Session, version_id: int) -> list[SceneText]:
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)).all()
    bodies: dict[int, list[str]] = {}
    for e in session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                             .order_by(ScriptElement.seq)):
        if e.scene_id and e.element_type not in SKIP:
            bodies.setdefault(e.scene_id, []).append(e.text)
    out = []
    for s in scenes:
        hk = "|".join([s.int_ext.value, _norm(s.location_raw or ""), s.time_of_day.value]) if not s.is_omitted else "omitted"
        out.append(SceneText(s, _norm(" ".join(bodies.get(s.id, []))), hk))
    return out


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.3 or sm.quick_ratio() < 0.3:
        return sm.quick_ratio() * 0.5
    return sm.ratio()


def match_versions(session: Session, old_version_id: int, new_version_id: int) -> list[Match]:
    old = load_texts(session, old_version_id)
    new = load_texts(session, new_version_id)
    old_by_num: dict[str, list[SceneText]] = {}
    for o in old:
        if o.scene.scene_number:
            old_by_num.setdefault(o.scene.scene_number, []).append(o)
    used_old: set[int] = set()
    matches: dict[int, Match] = {}          # new scene id -> match

    def classify(o: SceneText, n: SceneText, method: MatchMethod, sim: float, reasons=()) -> Match:
        if n.scene.is_omitted and not o.scene.is_omitted:
            return Match(o.scene, n.scene, LineageStatus.OMITTED, method, sim, reasons=tuple(reasons))
        same = o.body == n.body and o.heading_key == n.heading_key
        renum = bool(o.scene.scene_number and n.scene.scene_number and o.scene.scene_number != n.scene.scene_number)
        r = list(reasons)
        if renum:
            r.append("renumbered")
        return Match(o.scene, n.scene, LineageStatus.UNCHANGED if same else LineageStatus.CHANGED, method, sim,
                     renumbered=renum, heading_changed=o.heading_key != n.heading_key, reasons=tuple(r))

    # 1. by preserved scene number
    for n in new:
        num = n.scene.scene_number
        cands = old_by_num.get(num or "", [])
        if len(cands) != 1 or cands[0].scene.id in used_old:
            continue
        o = cands[0]
        if n.scene.is_omitted or o.scene.is_omitted:
            matches[n.scene.id] = classify(o, n, MatchMethod.NUMBER, 1.0 if n.scene.is_omitted == o.scene.is_omitted else 0.0)
            used_old.add(o.scene.id)
            continue
        sim = similarity(o.body, n.body)
        if sim >= NUMBER_MIN_SIMILARITY or o.heading_key == n.heading_key:
            reasons = ["low_similarity"] if sim < NUMBER_MIN_SIMILARITY else []
            matches[n.scene.id] = classify(o, n, MatchMethod.NUMBER, sim, reasons)
            used_old.add(o.scene.id)

    # 2. by content, best pairs first
    rest_new = [n for n in new if n.scene.id not in matches and not n.scene.is_omitted]
    rest_old = [o for o in old if o.scene.id not in used_old and not o.scene.is_omitted]
    pairs = []
    for n in rest_new:
        for o in rest_old:
            sim = similarity(o.body, n.body)
            if sim >= CONTENT_MIN_SIMILARITY:
                pairs.append((sim, n, o))
    pairs.sort(key=lambda p: -p[0])
    for sim, n, o in pairs:
        if n.scene.id in matches or o.scene.id in used_old:
            continue
        reasons = [] if sim >= CONFIDENT_SIMILARITY else ["content_match_uncertain"]
        if n.scene.scene_number and n.scene.scene_number in old_by_num and \
                all(x.scene.id != o.scene.id for x in old_by_num[n.scene.scene_number]):
            reasons.append("number_reused")
        matches[n.scene.id] = classify(o, n, MatchMethod.CONTENT, sim, reasons)
        used_old.add(o.scene.id)

    out: list[Match] = []
    for n in new:
        if n.scene.id in matches:
            out.append(matches[n.scene.id])
        elif n.scene.is_omitted:
            # newly listed OMITTED placeholder with no old counterpart: nothing to compare
            out.append(Match(None, n.scene, LineageStatus.OMITTED, MatchMethod.NONE, None))
        else:
            reasons = ("number_reused",) if n.scene.scene_number in old_by_num else ()
            out.append(Match(None, n.scene, LineageStatus.ADDED, MatchMethod.NONE, None, reasons=reasons))
    for o in old:
        if o.scene.id not in used_old and not o.scene.is_omitted:
            out.append(Match(o.scene, None, LineageStatus.REMOVED, MatchMethod.NONE, None))
    return out
