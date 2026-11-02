"""Schedule reports, computed in code: one-liner (stripboard), Day-Out-of-Days, story vs shooting.

DOOD rows are CASTING REQUIREMENTS (who is hired), not characters:
  * an appearance with a casting role counts for that role (YOUNG ARJUN and ADULT ARJUN are
    separate rows even though they are one character);
  * a character with exactly one actor role counts for that role;
  * a character with no casting role yet appears as an IMPLICIT requirement, clearly marked,
    until the AD creates the role;
  * a character with several roles whose appearance is not assigned is flagged UNASSIGNED;
  * voice-only appearances are a voice requirement, not on the shooting DOOD;
  * group characters (GOONS) are background, listed separately.
Columns are SHOOTING days only. Story days are shown alongside, never mixed in.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CastingRole, Schedule, SceneStoryDay, ShootingDay
from ..models.enums import CastingRoleKind, CharacterKind, DoodCode
from ..reports.facts import Facts, load_facts


def _story_days(session: Session, f: Facts) -> dict[int, SceneStoryDay]:
    ids = [s.id for s in f.scenes]
    return {sd.scene_id: sd for sd in session.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id.in_(ids)))} \
        if ids else {}


def _requirements(session: Session, f: Facts):
    """appearance -> (requirement key, label, character, kind, implicit, problem)."""
    roles_by_char: dict[int, list[CastingRole]] = defaultdict(list)
    for r in f.casting_roles.values():
        roles_by_char[r.character_id].append(r)

    def req(a):
        c = f.characters.get(a.character_id)
        if c is None:
            return None
        if c.kind == CharacterKind.GROUP:
            return ("group", c.id), c.canonical_name, c.canonical_name, "group", False, None
        if a.presence == "voice_only":
            voice = [r for r in roles_by_char[c.id] if r.kind == CastingRoleKind.VOICE]
            if voice:
                return ("role", voice[0].id), voice[0].name, c.canonical_name, "voice", False, None
            return ("voice", c.id), f"VOICE FOR {c.canonical_name}", c.canonical_name, "voice", True, None
        if a.casting_role_id in f.casting_roles:
            r = f.casting_roles[a.casting_role_id]
            return ("role", r.id), r.name, c.canonical_name, r.kind.value, False, None
        actors = [r for r in roles_by_char[c.id] if r.kind == CastingRoleKind.ACTOR]
        if len(actors) == 1:
            return ("role", actors[0].id), actors[0].name, c.canonical_name, "actor", False, None
        if len(actors) > 1:
            return (("unassigned", c.id), f"{c.canonical_name} — UNASSIGNED ROLE", c.canonical_name, "actor", False,
                    f"{c.canonical_name} has {len(actors)} casting roles; this appearance is not assigned to one")
        return ("implicit", c.id), c.canonical_name, c.canonical_name, "actor", True, None
    return req


def schedule_report(session: Session, schedule_id: int) -> dict[str, Any]:
    sched = session.get(Schedule, schedule_id)
    f = load_facts(session, sched.script_version_id)
    sdays = _story_days(session, f)
    req = _requirements(session, f)
    days = session.scalars(select(ShootingDay).where(ShootingDay.schedule_id == sched.id)
                           .order_by(ShootingDay.day_number)).all()
    apps_by_scene = defaultdict(list)
    for a in f.appearances:
        apps_by_scene[a.scene_id].append(a)

    # ---- requirements and the shooting days they work
    rows: dict[tuple, dict] = {}
    for d in days:
        for ss in d.scenes:
            for a in apps_by_scene.get(ss.scene_id, []):
                if not (a.is_appearance or a.presence == "voice_only"):
                    continue
                r = req(a)
                if r is None:
                    continue
                key, label, char, kind, implicit, problem = r
                row = rows.setdefault(key, {"key": f"{key[0]}:{key[1]}", "requirement": label, "character": char,
                                            "kind": kind, "implicit": implicit, "problems": set(),
                                            "days": set(), "scenes": set()})
                if problem:
                    row["problems"].add(problem)
                row["days"].add(d.day_number)
                row["scenes"].add(ss.scene_id)
    total_days = len(days)
    cast_rows, voice_rows, group_rows = [], [], []
    for key, row in rows.items():
        out = dict(row, problems=sorted(row["problems"]), scenes=sorted(row["scenes"]), days=sorted(row["days"]))
        if row["kind"] == "group":
            group_rows.append(out)
        elif row["kind"] == "voice":
            voice_rows.append(out)
        else:
            out.update(_dood_line(sorted(row["days"]), total_days, sched.drop_after_days))
            cast_rows.append(out)
    cast_rows.sort(key=lambda r: (-len(r["days"]), -len(r["scenes"]), r["requirement"]))
    for i, r in enumerate(cast_rows, start=1):
        r["cast_id"] = i                      # stripboard cast numbers: busiest first
    cast_id = {r["key"]: r["cast_id"] for r in cast_rows}

    # ---- one-liner: per shooting day, with story days shown separately
    oneliner, warnings = [], []
    seen_scenes = defaultdict(list)
    for d in days:
        entries, eighths, lights = [], 0, set()
        for ss in d.scenes:
            s = f.scene_by_id.get(ss.scene_id)
            if s is None:
                continue
            seen_scenes[s.id].append(d.day_number)
            portion = ss.portion_eighths if ss.portion_eighths is not None else s.eighths
            eighths += portion
            lights.add("night" if s.time_effective in ("NIGHT", "EVENING") else "day")
            keys = [req(a) for a in apps_by_scene.get(s.id, []) if a.is_appearance]
            ids = sorted({cast_id.get(f"{k[0][0]}:{k[0][1]}") for k in keys if k} - {None})
            sd = sdays.get(s.id)
            entries.append({"scene_id": s.id, "scene": s.number or f"#{s.seq}", "heading": s.heading,
                            "int_ext": s.int_ext, "time": s.time_written, "time_effective": s.time_effective,
                            "story_day": sd.label if sd else None, "story_day_inferred": bool(sd and sd.needs_review),
                            "location": f.loc_label(s.location_id), "eighths": portion,
                            "split": ss.portion_eighths is not None, "cast_ids": ids})
        story = sorted({e["story_day"] for e in entries if e["story_day"]})
        if len(lights) > 1:
            warnings.append(f"Shooting day {d.day_number} mixes day and night scenes.")
        oneliner.append({"day_id": d.id, "day_number": d.day_number,
                         "date": d.shoot_date.isoformat() if d.shoot_date else None, "label": d.label,
                         "eighths": eighths, "story_days": story, "scenes": entries})
    for sid, ds in seen_scenes.items():
        if len(ds) > 1:
            s = f.scene_by_id[sid]
            warnings.append(f"Scene {s.number or f'#{s.seq}'} is scheduled on shooting days {ds} "
                            "(fine for a split scene — set the portion shot each day).")
    for r in cast_rows:
        for p in r["problems"]:
            warnings.append(p)
    unscheduled = [s for s in f.scenes if not s.omitted and s.id not in seen_scenes]
    implicit = [r["requirement"] for r in cast_rows if r["implicit"]]

    # ---- story time vs shooting time, side by side
    live = [s for s in f.scenes if not s.omitted]
    night_scenes = [s for s in live if s.time_effective in ("NIGHT", "EVENING")]
    night_story = {(sdays[s.id].story_day, sdays[s.id].label) for s in night_scenes if s.id in sdays}
    night_shoot = {d["day_number"] for d in oneliner for e in d["scenes"] if e["time_effective"] in ("NIGHT", "EVENING")}
    story_count = len({sd.story_day for sd in sdays.values() if sd.story_day})
    return {
        "schedule": {"id": sched.id, "name": sched.name, "script_version_id": sched.script_version_id,
                     "drop_after_days": sched.drop_after_days},
        "story_vs_shooting": {
            "story_days": story_count,
            "flashback_scenes": sum(1 for sd in sdays.values() if sd.is_flashback),
            "story_days_inferred": any(sd.needs_review for sd in sdays.values()),
            "shooting_days": total_days,
            "night_scenes": len(night_scenes),
            "story_nights": len(night_story),
            "shooting_nights": len(night_shoot),
            "explanation": (f"{len(night_scenes)} night scene(s) happen on {len(night_story)} story night(s) and "
                            f"are scheduled on {len(night_shoot)} shooting day(s). Story days describe the plot; "
                            f"shooting days describe the calendar — the DOOD uses shooting days only."),
        },
        "scheduled_scenes": len(seen_scenes),
        "unscheduled": [{"scene_id": s.id, "scene": s.number or f"#{s.seq}", "heading": s.heading,
                         "eighths": s.eighths, "time": s.time_written,
                         "story_day": sdays[s.id].label if s.id in sdays else None,
                         "location": f.loc_label(s.location_id)} for s in unscheduled],
        "dood": {"days": [{"day_number": d["day_number"], "date": d["date"]} for d in oneliner],
                 "cast": cast_rows, "voice": voice_rows, "background_groups": group_rows,
                 "implicit_requirements": implicit},
        "oneliner": oneliner,
        "warnings": warnings,
    }


def _dood_line(work: list[int], total: int, drop_after: int) -> dict[str, Any]:
    """Codes per shooting day: SW, W, WF, SWF, H, and WD/PW around gaps longer than drop_after."""
    codes = {n: "" for n in range(1, total + 1)}
    if not work:
        return {"codes": codes, "work_days": 0, "hold_days": 0, "drop_days": 0, "start": None, "finish": None}
    first, last = work[0], work[-1]
    ws = set(work)
    holds = drops = 0
    for n in range(first, last + 1):
        codes[n] = DoodCode.WORK.value if n in ws else DoodCode.HOLD.value
    # long gaps -> drop / pickup (the gap is unpaid, not held)
    for a, b in zip(work, work[1:]):
        gap = b - a - 1
        if gap > drop_after:
            codes[a] = DoodCode.WORK_DROP.value
            codes[b] = DoodCode.PICKUP_WORK.value
            for n in range(a + 1, b):
                codes[n] = ""
            drops += gap
    if first == last:
        codes[first] = DoodCode.START_WORK_FINISH.value
    else:
        codes[first] = DoodCode.START_WORK.value if codes[first] == DoodCode.WORK.value else codes[first]
        codes[last] = DoodCode.WORK_FINISH.value if codes[last] == DoodCode.WORK.value else codes[last]
    holds = sum(1 for c in codes.values() if c == DoodCode.HOLD.value)
    return {"codes": codes, "work_days": len(ws), "hold_days": holds, "drop_days": drops,
            "start": first, "finish": last, "total_days": len(ws) + holds}


def materialize_roles(session: Session, version_id: int, actor: str) -> list[str]:
    """Create an ACTOR casting role for every on-set character that has none (the AD's choice)."""
    from ..models.enums import ExtractorKind, ReviewStatus
    from ..review.audit import log_event
    f = load_facts(session, version_id)
    have = {r.character_id for r in f.casting_roles.values()}
    made = []
    for cid in sorted({a.character_id for a in f.appearances if a.is_appearance}):
        c = f.characters.get(cid)
        if c is None or cid in have or c.kind == CharacterKind.GROUP:
            continue
        r = CastingRole(project_id=c.project_id, character_id=cid, name=c.canonical_name, kind=CastingRoleKind.ACTOR,
                        extracted_by=ExtractorKind.HUMAN, review_status=ReviewStatus.CONFIRMED)
        session.add(r)
        session.flush()
        log_event(session, c.project_id, r, "create", actor, note="casting role created from scheduling")
        made.append(c.canonical_name)
    return made
