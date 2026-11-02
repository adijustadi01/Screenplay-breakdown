"""Stage 3 endpoints: revisions (compare drafts) and scheduling (story days, shooting days, DOOD)."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Schedule, Scene, SceneStoryDay, ScriptRevision, ScriptVersion, ShootingDay
from ..revisions.diff import revision_report
from ..revisions.service import carry_forward, compare, rematch, scenes_needing_extraction
from ..scheduling import service as sched
from ..scheduling.reports import materialize_roles, schedule_report
from ..scheduling.story_days import infer_story_days
from . import deps

router = APIRouter(prefix="/api")


def get_session():
    yield from deps.get_session()


# ------------------------------------------------------------------------------ revisions
class CompareIn(BaseModel):
    from_version_id: int
    to_version_id: int
    carry_forward: bool = True


@router.post("/revisions")
def create_revision(body: CompareIn, s: Session = Depends(get_session)) -> dict:
    try:
        rev = compare(s, body.from_version_id, body.to_version_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    copied = carry_forward(s, rev.id) if body.carry_forward else {}
    s.commit()
    return {"revision_id": rev.id, "carried_forward": copied,
            "scenes_needing_extraction": scenes_needing_extraction(s, rev.id)}


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: int, s: Session = Depends(get_session)) -> list[dict]:
    return [{"id": r.id, "from_version_id": r.from_version_id, "to_version_id": r.to_version_id,
             "created_at": r.created_at.isoformat(), "stats": r.stats}
            for r in s.scalars(select(ScriptRevision).where(ScriptRevision.project_id == project_id)
                               .order_by(ScriptRevision.id.desc()))]


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: int, s: Session = Depends(get_session)) -> dict:
    if s.get(ScriptRevision, revision_id) is None:
        raise HTTPException(404, "revision not found")
    r = revision_report(s, revision_id)
    r["scenes_needing_extraction"] = scenes_needing_extraction(s, revision_id)
    return r


class RematchIn(BaseModel):
    old_scene_id: int | None
    actor: str = "AD"


@router.post("/lineage/{lineage_id}/rematch")
def rematch_scene(lineage_id: int, body: RematchIn, s: Session = Depends(get_session)) -> dict:
    row = rematch(s, lineage_id, body.old_scene_id, body.actor)
    s.commit()
    return {"lineage_id": row.id, "status": row.status.value, "similarity": row.similarity}


# ------------------------------------------------------------------------------ story days
@router.post("/script-versions/{version_id}/story-days/infer")
def infer(version_id: int, s: Session = Depends(get_session)) -> dict:
    if s.get(ScriptVersion, version_id) is None:
        raise HTTPException(404, "script version not found")
    out = infer_story_days(s, version_id)
    s.commit()
    return out


@router.get("/script-versions/{version_id}/story-days")
def story_days(version_id: int, s: Session = Depends(get_session)) -> list[dict]:
    rows = []
    for sc in s.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)):
        sd = s.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == sc.id)).first()
        rows.append({"scene_id": sc.id, "scene": sc.scene_number or f"#{sc.seq}", "time": sc.time_of_day.value,
                     "omitted": sc.is_omitted, "story_day_id": sd.id if sd else None,
                     "story_day": sd.story_day if sd else None, "label": sd.label if sd else None,
                     "is_flashback": sd.is_flashback if sd else None, "basis": sd.basis if sd else None,
                     "review_status": sd.review_status.value if sd else None,
                     "inferred": bool(sd and sd.extracted_by.value == "rules")})
    return rows


# ------------------------------------------------------------------------------ schedules
class ScheduleIn(BaseModel):
    name: str = "Main unit"
    drop_after_days: int = 10


@router.get("/script-versions/{version_id}/schedules")
def list_schedules(version_id: int, s: Session = Depends(get_session)) -> list[dict]:
    return [{"id": x.id, "name": x.name, "days": len(x.days)}
            for x in s.scalars(select(Schedule).where(Schedule.script_version_id == version_id))]


@router.post("/script-versions/{version_id}/schedules")
def new_schedule(version_id: int, body: ScheduleIn, s: Session = Depends(get_session)) -> dict:
    x = sched.create_schedule(s, version_id, body.name, body.drop_after_days)
    s.commit()
    return {"id": x.id}


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    if s.get(Schedule, schedule_id) is None:
        raise HTTPException(404, "schedule not found")
    return schedule_report(s, schedule_id)


class DayIn(BaseModel):
    shoot_date: date | None = None
    label: str | None = None


@router.post("/schedules/{schedule_id}/days")
def add_day(schedule_id: int, body: DayIn, s: Session = Depends(get_session)) -> dict:
    d = sched.add_day(s, schedule_id, body.shoot_date, body.label)
    s.commit()
    return {"id": d.id, "day_number": d.day_number}


@router.patch("/days/{day_id}")
def edit_day(day_id: int, body: DayIn, s: Session = Depends(get_session)) -> dict:
    d = s.get(ShootingDay, day_id)
    d.shoot_date, d.label = body.shoot_date, body.label
    s.commit()
    return {"id": d.id}


@router.delete("/days/{day_id}")
def remove_day(day_id: int, s: Session = Depends(get_session)) -> dict:
    sched.delete_day(s, day_id)
    s.commit()
    return {"ok": True}


class MoveIn(BaseModel):
    day_number: int


@router.post("/days/{day_id}/move")
def move(day_id: int, body: MoveIn, s: Session = Depends(get_session)) -> dict:
    sched.move_day(s, day_id, body.day_number)
    s.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    scene_id: int
    portion_eighths: int | None = None


@router.post("/days/{day_id}/scenes")
def assign(day_id: int, body: AssignIn, s: Session = Depends(get_session)) -> dict:
    try:
        sched.assign(s, day_id, body.scene_id, body.portion_eighths)
    except sched.ScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc
    s.commit()
    return {"ok": True}


@router.delete("/days/{day_id}/scenes/{scene_id}")
def unassign(day_id: int, scene_id: int, s: Session = Depends(get_session)) -> dict:
    sched.unassign(s, day_id, scene_id)
    s.commit()
    return {"ok": True}


class AutofillIn(BaseModel):
    max_eighths: int = 40


@router.post("/schedules/{schedule_id}/autofill")
def autofill(schedule_id: int, body: AutofillIn, s: Session = Depends(get_session)) -> dict:
    created = sched.autofill(s, schedule_id, body.max_eighths)
    s.commit()
    return {"days_created": len(created)}


class MigrateIn(BaseModel):
    revision_id: int
    name: str | None = None


@router.post("/schedules/{schedule_id}/migrate")
def migrate(schedule_id: int, body: MigrateIn, s: Session = Depends(get_session)) -> dict:
    try:
        out = sched.migrate_schedule(s, schedule_id, body.revision_id, body.name)
    except sched.ScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc
    s.commit()
    return out


@router.post("/script-versions/{version_id}/casting-roles/materialize")
def make_roles(version_id: int, s: Session = Depends(get_session)) -> dict:
    made = materialize_roles(s, version_id, "AD")
    s.commit()
    return {"created": made}
