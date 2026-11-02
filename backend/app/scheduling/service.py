"""Shooting schedule: shooting days and the scenes filmed on each (set by the AD)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Schedule, ScheduledScene, Scene, SceneLineage, ShootingDay
from ..models.enums import LineageStatus
from ..reports.facts import load_facts

# Grouping classes used ONLY by the auto-fill suggestion (stored times are never changed).
NIGHT = {"NIGHT", "EVENING"}
MAGIC = {"DAWN", "DUSK", "SUNRISE", "SUNSET"}


class ScheduleError(ValueError):
    pass


def create_schedule(session: Session, version_id: int, name: str, drop_after_days: int = 10) -> Schedule:
    s = Schedule(script_version_id=version_id, name=name, drop_after_days=drop_after_days)
    session.add(s)
    session.flush()
    return s


def add_day(session: Session, schedule_id: int, shoot_date: date | None = None, label: str | None = None) -> ShootingDay:
    n = session.scalar(select(func.max(ShootingDay.day_number)).where(ShootingDay.schedule_id == schedule_id)) or 0
    d = ShootingDay(schedule_id=schedule_id, day_number=n + 1, shoot_date=shoot_date, label=label)
    session.add(d)
    session.flush()
    return d


def delete_day(session: Session, day_id: int) -> None:
    d = session.get(ShootingDay, day_id)
    sid, num = d.schedule_id, d.day_number
    session.delete(d)
    session.flush()
    for later in session.scalars(select(ShootingDay).where(ShootingDay.schedule_id == sid,
                                                           ShootingDay.day_number > num)
                                 .order_by(ShootingDay.day_number)):
        later.day_number -= 1
        session.flush()


def move_day(session: Session, day_id: int, new_number: int) -> None:
    """Change shooting order: day becomes number `new_number`, others shift."""
    d = session.get(ShootingDay, day_id)
    days = session.scalars(select(ShootingDay).where(ShootingDay.schedule_id == d.schedule_id)
                           .order_by(ShootingDay.day_number)).all()
    order = [x for x in days if x.id != d.id]
    order.insert(max(0, min(len(order), new_number - 1)), d)
    for i, x in enumerate(order):          # two passes avoid unique clashes
        x.day_number = 10_000 + i
    session.flush()
    for i, x in enumerate(order, start=1):
        x.day_number = i
    session.flush()


def assign(session: Session, day_id: int, scene_id: int, portion_eighths: int | None = None,
           position: int | None = None) -> ScheduledScene:
    day = session.get(ShootingDay, day_id)
    sc = session.get(Scene, scene_id)
    sched = session.get(Schedule, day.schedule_id)
    if sc is None or sc.script_version_id != sched.script_version_id:
        raise ScheduleError("scene does not belong to this schedule's script version")
    if sc.is_omitted:
        raise ScheduleError("omitted scenes cannot be scheduled")
    existing = session.scalars(select(ScheduledScene).where(ScheduledScene.shooting_day_id == day_id,
                                                            ScheduledScene.scene_id == scene_id)).first()
    if existing:
        existing.portion_eighths = portion_eighths
        return existing
    if position is None:
        position = (session.scalar(select(func.max(ScheduledScene.position))
                                   .where(ScheduledScene.shooting_day_id == day_id)) or 0) + 1
    ss = ScheduledScene(shooting_day_id=day_id, scene_id=scene_id, portion_eighths=portion_eighths, position=position)
    session.add(ss)
    session.flush()
    return ss


def unassign(session: Session, day_id: int, scene_id: int) -> None:
    for ss in session.scalars(select(ScheduledScene).where(ScheduledScene.shooting_day_id == day_id,
                                                           ScheduledScene.scene_id == scene_id)):
        session.delete(ss)
    session.flush()


def scheduled_scene_ids(session: Session, schedule_id: int) -> set[int]:
    return {sid for (sid,) in session.execute(select(ScheduledScene.scene_id).join(ShootingDay)
                                              .where(ShootingDay.schedule_id == schedule_id))}


def autofill(session: Session, schedule_id: int, max_eighths: int = 40) -> list[int]:
    """A STARTING POINT, not a schedule: group unscheduled scenes by story location and
    day / night / magic hour, then pack them into new shooting days of at most `max_eighths`."""
    sched = session.get(Schedule, schedule_id)
    f = load_facts(session, sched.script_version_id)
    done = scheduled_scene_ids(session, schedule_id)
    pending = [s for s in f.scenes if not s.omitted and s.id not in done]

    def light(s):
        t = s.time_effective
        return "night" if t in NIGHT else ("magic" if t in MAGIC else ("day" if t not in ("UNSPECIFIED", "OTHER") else "unknown"))

    groups: dict[tuple, list] = {}
    for s in pending:
        root = f.loc_label(s.location_path[0]) if s.location_path else "(no location)"
        groups.setdefault((light(s), root), []).append(s)
    created = []
    for (lt, root), scenes in sorted(groups.items(), key=lambda kv: (kv[0][0] != "day", kv[0][1])):
        day, used = None, 0
        for s in sorted(scenes, key=lambda x: x.seq):
            if day is None or (used + s.eighths > max_eighths and used > 0):
                day = add_day(session, schedule_id, label=f"Suggested — {lt} · {root}")
                created.append(day.id)
                used = 0
            assign(session, day.id, s.id)
            used += s.eighths
    return created


def migrate_schedule(session: Session, schedule_id: int, revision_id: int, name: str | None = None) -> dict:
    """Copy a schedule onto the revised draft through the scene lineage."""
    from ..models import ScriptRevision
    old = session.get(Schedule, schedule_id)
    rev = session.get(ScriptRevision, revision_id)
    if old.script_version_id != rev.from_version_id:
        raise ScheduleError("schedule is not on this revision's previous draft")
    links = {r.old_scene_id: r for r in session.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id))
             if r.old_scene_id}
    new = create_schedule(session, rev.to_version_id, name or f"{old.name} (revised)", old.drop_after_days)
    dropped, kept = [], 0
    for d in old.days:
        nd = ShootingDay(schedule_id=new.id, day_number=d.day_number, shoot_date=d.shoot_date, label=d.label,
                         notes=d.notes)
        session.add(nd)
        session.flush()
        for ss in d.scenes:
            r = links.get(ss.scene_id)
            if r is None or r.new_scene_id is None or r.status in (LineageStatus.REMOVED, LineageStatus.OMITTED):
                sc = session.get(Scene, ss.scene_id)
                dropped.append({"shooting_day": d.day_number, "scene": sc.scene_number or f"#{sc.seq}",
                                "reason": r.status.value if r else "no match"})
                continue
            session.add(ScheduledScene(shooting_day_id=nd.id, scene_id=r.new_scene_id, position=ss.position,
                                       portion_eighths=ss.portion_eighths))
            kept += 1
    session.flush()
    added = [r.new_scene_id for r in session.scalars(select(SceneLineage).where(
        SceneLineage.revision_id == rev.id, SceneLineage.status == LineageStatus.ADDED))]
    return {"schedule_id": new.id, "kept": kept, "dropped": dropped, "new_scenes_unscheduled": added}
