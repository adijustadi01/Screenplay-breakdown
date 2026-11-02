"""Revision report: which scenes changed, and what each change means for production.

Per scene: status, heading changes, page-length change, cast added / removed / changed
presence (CHARACTERS) and casting requirements added / removed (CASTING ROLES — kept
separate), location change, department items added / removed / re-quantified, text diff.
Script-wide: characters, casting roles and department items new to / dropped from the script,
and scheduled scenes affected by the revision. Computed in code from both drafts' facts.
"""
from __future__ import annotations

import difflib
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Appearance,
    BreakdownOccurrence,
    Schedule,
    ScheduledScene,
    SceneLineage,
    ScriptElement,
    ScriptRevision,
    ShootingDay,
)
from ..models.enums import ElementType, LineageStatus
from ..reports.facts import Facts, load_facts

SKIP = {ElementType.PAGE_HEADER, ElementType.NON_PRINTING, ElementType.TITLE_PAGE}


def _cast(f: Facts, scene_id: int | None) -> dict[int, str]:
    if scene_id is None:
        return {}
    return {a.character_id: a.presence for a in f.appearances if a.scene_id == scene_id and a.character_id in f.characters}


def _roles(f: Facts, scene_id: int | None) -> set[str]:
    if scene_id is None:
        return set()
    return {f.casting_roles[a.casting_role_id].name for a in f.appearances
            if a.scene_id == scene_id and a.is_appearance and a.casting_role_id in f.casting_roles}


def _items(f: Facts, scene_id: int | None) -> dict[int, Any]:
    if scene_id is None:
        return {}
    return {o.item_id: o for o in f.occurrences if o.scene_id == scene_id}


def _text(session: Session, scene_id: int | None) -> list[str]:
    if scene_id is None:
        return []
    return [e.text for e in session.scalars(select(ScriptElement).where(ScriptElement.scene_id == scene_id)
                                            .order_by(ScriptElement.seq)) if e.element_type not in SKIP]


class Coverage:
    """Which scenes each kind of extraction has actually been run on (from the run records)."""

    def __init__(self, session: Session, version_id: int):
        from ..models import ExtractionRun
        from ..models.enums import RunKind, RunStatus
        self.cast_all = False
        self.cast: set[int] = set()
        self.dept_all: set[str] = set()
        self.dept: dict[str, set[int]] = defaultdict(set)
        for r in session.scalars(select(ExtractionRun).where(ExtractionRun.script_version_id == version_id,
                                                             ExtractionRun.status.in_([RunStatus.SUCCEEDED,
                                                                                       RunStatus.PARTIAL]))):
            sids = (r.params or {}).get("scene_ids")
            if r.kind == RunKind.SCENE_EXTRACTION:
                if sids is None:
                    self.cast_all = True
                else:
                    self.cast |= set(sids)
            elif r.kind == RunKind.DEPARTMENT_EXTRACTION:
                for d in (r.params or {}).get("departments", []):
                    if sids is None:
                        self.dept_all.add(d)
                    else:
                        self.dept[d] |= set(sids)

    def has_cast(self, sid: int) -> bool:
        return self.cast_all or sid in self.cast

    def has_dept(self, sid: int, d: str) -> bool:
        return d in self.dept_all or sid in self.dept.get(d, set())


def revision_report(session: Session, revision_id: int, include_text: bool = True) -> dict[str, Any]:
    rev = session.get(ScriptRevision, revision_id)
    fo, fn = load_facts(session, rev.from_version_id), load_facts(session, rev.to_version_id)
    rows = session.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id)).all()
    seq = lambda r: (fn.scene_by_id[r.new_scene_id].seq if r.new_scene_id else 10_000 + fo.scene_by_id[r.old_scene_id].seq)  # noqa: E731
    rows = sorted(rows, key=seq)
    cname = lambda f, cid: f.characters[cid].canonical_name if cid in f.characters else f"#{cid}"  # noqa: E731
    on_set = ("present", "off_screen")
    # Only compare what BOTH drafts have broken down; otherwise "added" would just mean "not extracted before".
    depts_old = {o.department for o in fo.occurrences}
    depts_new = {o.department for o in fn.occurrences}
    compared_depts = depts_old & depts_new
    not_compared = sorted((depts_old | depts_new) - compared_depts)
    cast_compared = bool(fo.appearances) and bool(fn.appearances)
    cov_o, cov_n = Coverage(session, rev.from_version_id), Coverage(session, rev.to_version_id)
    scenes_out = []
    totals = defaultdict(int)
    comparable_old_items: set = set()     # (dept, name) seen in an old scene that was really compared
    for r in rows:
        so = fo.scene_by_id.get(r.old_scene_id) if r.old_scene_id else None
        sn = fn.scene_by_id.get(r.new_scene_id) if r.new_scene_id else None
        totals[r.status.value] += 1
        # A scene is covered if an extraction ran on it, or (unchanged) its facts were carried from a covered scene.
        def cov_cast(sid_n):
            return cov_n.has_cast(sid_n) or (r.carried_forward and r.old_scene_id and cov_o.has_cast(r.old_scene_id))

        def cov_dept(sid_n, d):
            return cov_n.has_dept(sid_n, d) or (r.carried_forward and r.old_scene_id and cov_o.has_dept(r.old_scene_id, d))
        scene_cast_ok = cast_compared and (r.new_scene_id is None or (sn and sn.omitted) or cov_cast(r.new_scene_id)) \
            and (r.old_scene_id is None or cov_o.has_cast(r.old_scene_id))
        co, cn = (_cast(fo, r.old_scene_id), _cast(fn, r.new_scene_id)) if scene_cast_ok else ({}, {})
        scene_depts = {d for d in compared_depts
                       if (r.new_scene_id is None or (sn and sn.omitted) or cov_dept(r.new_scene_id, d))
                       and (r.old_scene_id is None or cov_o.has_dept(r.old_scene_id, d))}
        depts_pending = sorted(compared_depts - scene_depts) if sn and not sn.omitted else []
        added = sorted(cname(fn, c) for c, p in cn.items() if p in on_set and co.get(c) not in on_set)
        removed = sorted(cname(fo, c) for c, p in co.items() if p in on_set and cn.get(c) not in on_set)
        presence_changed = sorted(f"{cname(fn, c)}: {co[c]} → {cn[c]}" for c in set(co) & set(cn)
                                  if co[c] != cn[c] and not (co[c] in on_set and cn[c] in on_set)
                                  and cname(fn, c) not in added and cname(fo, c) not in removed)
        ro, rn = (_roles(fo, r.old_scene_id), _roles(fn, r.new_scene_id)) if scene_cast_ok else (set(), set())
        io = {k: v for k, v in _items(fo, r.old_scene_id).items() if v.department in scene_depts}
        comparable_old_items |= {(v.department, v.name) for v in io.values()}
        inn = {k: v for k, v in _items(fn, r.new_scene_id).items() if v.department in scene_depts}
        dept = defaultdict(lambda: {"added": [], "removed": [], "quantity_changed": []})
        for iid, o in inn.items():
            if iid not in io:
                dept[o.department]["added"].append(o.name)
            elif (io[iid].quantity_max, io[iid].quantity_raw) != (o.quantity_max, o.quantity_raw):
                dept[o.department]["quantity_changed"].append(
                    f"{o.name}: {io[iid].quantity_max if io[iid].quantity_max is not None else '?'} → "
                    f"{o.quantity_max if o.quantity_max is not None else '?'}")
        for iid, o in io.items():
            if iid not in inn:
                dept[o.department]["removed"].append(o.name)
        loc_o = fo.loc_label(so.location_id) if so else None
        loc_n = fn.loc_label(sn.location_id) if (sn and not sn.omitted) else None
        heading = []
        if so and sn and not sn.omitted:
            if so.int_ext != sn.int_ext:
                heading.append(f"INT/EXT {so.int_ext} → {sn.int_ext}")
            if so.time_written != sn.time_written:
                heading.append(f"time {so.time_written} → {sn.time_written}")
        entry = {
            "lineage_id": r.id, "status": r.status.value, "method": r.method.value, "similarity": r.similarity,
            "needs_review": r.needs_review, "review_reason": r.review_reason, "carried_forward": r.carried_forward,
            "old": {"scene_id": so.id, "number": so.number, "seq": so.seq, "heading": so.heading} if so else None,
            "new": {"scene_id": sn.id, "number": sn.number, "seq": sn.seq, "heading": sn.heading} if sn else None,
            "renumbered": r.renumbered, "heading_changed": r.heading_changed, "heading_changes": heading,
            "length_delta_eighths": r.length_delta_eighths,
            "location": {"from": loc_o, "to": loc_n} if (so and sn and loc_o != loc_n) else None,
            "cast_added": added, "cast_removed": removed, "presence_changed": presence_changed,
            "casting_roles_added": sorted(rn - ro), "casting_roles_removed": sorted(ro - rn),
            "departments": {k: v for k, v in sorted(dept.items())},
            "not_extracted": bool(sn and not sn.omitted and fo.appearances and not cov_cast(sn.id)),
            "departments_not_extracted": depts_pending,
        }
        if include_text and r.status in (LineageStatus.CHANGED,):
            entry["text_diff"] = list(difflib.unified_diff(_text(session, r.old_scene_id), _text(session, r.new_scene_id),
                                                           "previous", "revised", lineterm="", n=1))[2:60]
        scenes_out.append(entry)

    def on_set_chars(f):
        return {a.character_id for a in f.appearances if a.is_appearance and a.character_id in f.characters
                and not f.scene_by_id[a.scene_id].omitted}
    old_chars, new_chars = on_set_chars(fo), on_set_chars(fn)
    old_roles = {fo.casting_roles[a.casting_role_id].name for a in fo.appearances
                 if a.is_appearance and a.casting_role_id in fo.casting_roles}
    new_roles = {fn.casting_roles[a.casting_role_id].name for a in fn.appearances
                 if a.is_appearance and a.casting_role_id in fn.casting_roles}
    old_items = {(o.department, o.name) for o in fo.occurrences if o.department in compared_depts}
    new_items = {(o.department, o.name) for o in fn.occurrences if o.department in compared_depts}
    script_dept = defaultdict(lambda: {"new_to_script": [], "dropped_from_script": []})
    for d, n in sorted(new_items - old_items):
        script_dept[d]["new_to_script"].append(n)
    for d, n in sorted((old_items - new_items) & comparable_old_items):
        script_dept[d]["dropped_from_script"].append(n)

    affected = []
    changed_old = {r.old_scene_id: r for r in rows if r.old_scene_id and r.status != LineageStatus.UNCHANGED}
    for sch in session.scalars(select(Schedule).where(Schedule.script_version_id == rev.from_version_id)):
        for ss, day in session.execute(select(ScheduledScene, ShootingDay).join(ShootingDay).where(
                ShootingDay.schedule_id == sch.id)).all():
            if ss.scene_id in changed_old:
                r = changed_old[ss.scene_id]
                s = fo.scene_by_id[ss.scene_id]
                affected.append({"schedule": sch.name, "shooting_day": day.day_number,
                                 "date": day.shoot_date.isoformat() if day.shoot_date else None,
                                 "scene": s.number or f"#{s.seq}", "status": r.status.value})

    return {
        "revision_id": rev.id, "from_version_id": rev.from_version_id, "to_version_id": rev.to_version_id,
        "summary": dict(totals),
        "pages_delta_eighths": sum(s.eighths for s in fn.scenes if not s.omitted)
                               - sum(s.eighths for s in fo.scenes if not s.omitted),
        "cast_compared": cast_compared,
        "departments_compared": sorted(compared_depts),
        "departments_not_compared": not_compared,
        "characters_new_to_script": sorted(cname(fn, c) for c in new_chars - old_chars) if cast_compared else [],
        "characters_dropped_from_script": sorted(cname(fo, c) for c in old_chars - new_chars) if cast_compared else [],
        "casting_roles_new": sorted(new_roles - old_roles),
        "casting_roles_dropped": sorted(old_roles - new_roles),
        "departments": {k: v for k, v in sorted(script_dept.items())},
        "needs_review": sum(1 for r in rows if r.needs_review),
        "not_extracted": sum(1 for s in scenes_out if s["not_extracted"]),
        "departments_not_extracted": sum(1 for s in scenes_out if s["departments_not_extracted"]),
        "affected_schedule": affected,
        "scenes": scenes_out,
    }
