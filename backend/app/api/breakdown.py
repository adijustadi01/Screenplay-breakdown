"""Extraction, reconciliation, reports, query, review and entity endpoints."""
from __future__ import annotations

import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..extraction.service import run_extraction
from ..models import (
    Appearance,
    CastingRole,
    Character,
    ExtractionRun,
    Location,
    Scene,
    SceneLocation,
    ScriptVersion,
)
from ..models.enums import EntityStatus, ReviewStatus
from ..query.engine import QueryFilters, query_options, run_query
from ..reconciliation.service import propose
from ..reports.service import run_report, scenes_detail
from ..review import service as review
from ..review.audit import snapshot
from . import deps

router = APIRouter(prefix="/api")
JOBS: dict[int, dict[str, Any]] = {}
ACTOR_DEFAULT = "AD"


def get_session():
    yield from deps.get_session()


def _version(s: Session, version_id: int) -> ScriptVersion:
    v = s.get(ScriptVersion, version_id)
    if v is None:
        raise HTTPException(404, "script version not found")
    return v


def make_extractor(name: str):
    if name == "heuristic":
        from ..extraction.heuristic_extractor import HeuristicExtractor
        return HeuristicExtractor()
    if name == "claude":
        from ..extraction.claude_extractor import ClaudeExtractor
        return ClaudeExtractor()
    raise HTTPException(400, f"unknown extractor {name!r}")


# ----------------------------------------------------------------------------- extraction
class ExtractIn(BaseModel):
    extractor: Literal["claude", "heuristic"] = "claude"
    workers: int = 4
    scene_ids: list[int] | None = None


@router.post("/script-versions/{version_id}/extract")
def start_extraction(version_id: int, body: ExtractIn, s: Session = Depends(get_session)) -> dict:
    _version(s, version_id)
    if JOBS.get(version_id, {}).get("status") == "running":
        raise HTTPException(409, "extraction already running for this script")
    extractor = make_extractor(body.extractor)
    JOBS[version_id] = {"status": "running", "done": 0, "total": 0, "extractor": body.extractor, "error": None}
    factory = deps._factory or deps.configure()

    def work():
        with factory() as session:
            try:
                def progress(done, total):
                    JOBS[version_id].update(done=done, total=total)
                run = run_extraction(session, version_id, extractor, workers=body.workers,
                                     scene_ids=body.scene_ids, progress=progress)
                session.commit()
                JOBS[version_id].update(status="finished", run_id=run.id, run_status=run.status.value)
            except Exception as exc:
                session.commit()  # keep the FAILED run record
                JOBS[version_id].update(status="failed", error=str(exc))

    threading.Thread(target=work, daemon=True).start()
    return JOBS[version_id]


@router.get("/script-versions/{version_id}/extract")
def extraction_status(version_id: int, s: Session = Depends(get_session)) -> dict:
    runs = s.scalars(select(ExtractionRun).where(ExtractionRun.script_version_id == version_id)
                     .order_by(ExtractionRun.id.desc())).all()
    return {"job": JOBS.get(version_id), "runs": [
        {"id": r.id, "kind": r.kind.value, "model": r.model, "prompt_version": r.prompt_version,
         "status": r.status.value, "stats": r.stats, "error": r.error, "started_at": r.started_at.isoformat(),
         "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs]}


@router.post("/script-versions/{version_id}/reconcile")
def reconcile(version_id: int, s: Session = Depends(get_session)) -> dict:
    _version(s, version_id)
    made = propose(s, version_id)
    s.commit()
    return {"proposals_created": made}


# -------------------------------------------------------------------------------- reports
@router.get("/script-versions/{version_id}/reports/{name}")
def report(version_id: int, name: str, time_mode: Literal["as_written", "effective"] = "as_written",
           size: int = 2, min_scenes: int = 1, s: Session = Depends(get_session)) -> dict:
    _version(s, version_id)
    kw: dict[str, Any] = {"time_mode": time_mode}
    if name == "combinations":
        kw.update(size=size, min_scenes=min_scenes)
    try:
        return run_report(s, version_id, name, **kw)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/script-versions/{version_id}/scene-list")
def scene_list(version_id: int, ids: str = Query(..., description="comma-separated scene ids"),
               s: Session = Depends(get_session)) -> list[dict]:
    _version(s, version_id)
    return scenes_detail(s, version_id, [int(x) for x in ids.split(",") if x.strip()])


# ---------------------------------------------------------------------------------- query
@router.get("/script-versions/{version_id}/query/options")
def get_query_options(version_id: int, s: Session = Depends(get_session)) -> dict:
    _version(s, version_id)
    return query_options(s, version_id)


@router.post("/script-versions/{version_id}/query")
def query(version_id: int, q: QueryFilters, s: Session = Depends(get_session)) -> dict:
    _version(s, version_id)
    return run_query(s, version_id, q)


# --------------------------------------------------------------------------------- review
class ActionIn(BaseModel):
    action: Literal["confirm", "edit", "reject", "accept", "decline", "dismiss", "undo"]
    changes: dict[str, Any] | None = None
    note: str | None = None
    actor: str = ACTOR_DEFAULT


class BulkIn(BaseModel):
    item_ids: list[int]
    action: Literal["confirm", "reject", "accept", "decline", "dismiss"]
    note: str | None = None
    actor: str = ACTOR_DEFAULT


@router.get("/projects/{project_id}/review")
def review_queue(project_id: int, version_id: int | None = None, status: str | None = "open",
                 category: str | None = None, severity: str | None = None, reason: str | None = None,
                 scene_id: int | None = None, limit: int = 500, offset: int = 0,
                 s: Session = Depends(get_session)) -> dict:
    return review.list_items(s, project_id, version_id, status or None, category, severity, reason, scene_id,
                             limit, offset)


@router.get("/review/{item_id}")
def review_item(item_id: int, s: Session = Depends(get_session)) -> dict:
    try:
        return review.item_detail(s, item_id)
    except review.ReviewError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/review/{item_id}")
def review_act(item_id: int, body: ActionIn, s: Session = Depends(get_session)) -> dict:
    try:
        item = review.act_on_item(s, item_id, body.action, body.actor, body.changes, body.note)
    except review.ReviewError as exc:
        s.rollback()
        raise HTTPException(400, str(exc)) from exc
    s.commit()
    return review.item_detail(s, item.id)


@router.post("/review-bulk")
def review_bulk(body: BulkIn, s: Session = Depends(get_session)) -> dict:
    out = review.bulk_act(s, body.item_ids, body.action, body.actor, body.note)
    s.commit()
    return out


class FactActionIn(BaseModel):
    action: Literal["confirm", "edit", "reject"]
    changes: dict[str, Any] | None = None
    note: str | None = None
    actor: str = ACTOR_DEFAULT


@router.post("/facts/{table}/{entity_id}")
def fact_act(table: str, entity_id: int, body: FactActionIn, s: Session = Depends(get_session)) -> dict:
    try:
        if body.action == "confirm":
            obj = review.confirm_fact(s, table, entity_id, body.actor, body.note)
        elif body.action == "edit":
            obj = review.edit_fact(s, table, entity_id, body.changes or {}, body.actor, body.note)
        else:
            obj = review.reject_fact(s, table, entity_id, body.actor, body.note)
    except review.ReviewError as exc:
        s.rollback()
        raise HTTPException(400, str(exc)) from exc
    s.commit()
    return snapshot(obj)


@router.get("/facts/{table}/{entity_id}/history")
def fact_history(table: str, entity_id: int, s: Session = Depends(get_session)) -> list[dict]:
    return review.audit_log(s, table, entity_id)


class AddAppearanceIn(BaseModel):
    character_id: int | None = None
    new_character_name: str | None = None
    presence: Literal["present", "off_screen", "voice_only", "mentioned", "uncertain"] = "present"
    speaking: Literal["speaking", "silent", "unknown"] = "silent"
    note: str | None = None
    actor: str = ACTOR_DEFAULT


@router.post("/scenes/{scene_id}/appearances")
def add_appearance(scene_id: int, body: AddAppearanceIn, s: Session = Depends(get_session)) -> dict:
    sc = s.get(Scene, scene_id)
    if sc is None:
        raise HTTPException(404, "scene not found")
    try:
        cid = body.character_id
        if cid is None:
            if not body.new_character_name:
                raise review.ReviewError("give character_id or new_character_name")
            cid = review.create_character(s, s.get(ScriptVersion, sc.script_version_id).project_id,
                                          body.new_character_name, body.actor).id
        a = review.add_appearance(s, scene_id, cid, body.presence, body.speaking, body.actor, body.note)
    except review.ReviewError as exc:
        s.rollback()
        raise HTTPException(400, str(exc)) from exc
    s.commit()
    return snapshot(a)


# ------------------------------------------------------------------------------- entities
@router.get("/scenes/{scene_id}/breakdown")
def scene_breakdown(scene_id: int, s: Session = Depends(get_session)) -> dict:
    sc = s.get(Scene, scene_id)
    if sc is None:
        raise HTTPException(404, "scene not found")
    apps = []
    for a in s.scalars(select(Appearance).where(Appearance.scene_id == scene_id).order_by(Appearance.id)):
        c = s.get(Character, a.character_id)
        while c.status == EntityStatus.MERGED and c.merged_into_id:
            c = s.get(Character, c.merged_into_id)
        role = s.get(CastingRole, a.casting_role_id) if a.casting_role_id else None
        apps.append(snapshot(a) | {"character_name": c.canonical_name, "character_kind": c.kind.value,
                                   "casting_role": role.name if role else None,
                                   "counts": a.review_status != ReviewStatus.REJECTED})
    locs = []
    for sl in s.scalars(select(SceneLocation).where(SceneLocation.scene_id == scene_id)):
        loc = s.get(Location, sl.location_id)
        while loc.status == EntityStatus.MERGED and loc.merged_into_id:
            loc = s.get(Location, loc.merged_into_id)
        path = [loc.name]
        p = loc
        while p.parent_id:
            p = s.get(Location, p.parent_id)
            path.insert(0, p.name)
        locs.append(snapshot(sl) | {"location_path": " > ".join(path)})
    return {"scene_id": scene_id, "synopsis": sc.synopsis, "appearances": apps, "locations": locs}


@router.get("/projects/{project_id}/characters")
def characters(project_id: int, s: Session = Depends(get_session)) -> list[dict]:
    return [{"id": c.id, "name": c.canonical_name, "kind": c.kind.value, "status": c.status.value,
             "merged_into_id": c.merged_into_id}
            for c in s.scalars(select(Character).where(Character.project_id == project_id)
                               .order_by(Character.canonical_name))]


@router.get("/projects/{project_id}/locations")
def locations(project_id: int, s: Session = Depends(get_session)) -> list[dict]:
    return [{"id": loc.id, "name": loc.name, "parent_id": loc.parent_id, "kind": loc.kind.value,
             "status": loc.status.value}
            for loc in s.scalars(select(Location).where(Location.project_id == project_id).order_by(Location.name))]
