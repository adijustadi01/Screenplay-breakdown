"""The review workflow: confirm / edit / reject any fact, accept / decline / undo proposals.

Reports are queries over the facts, so a correction here shows up in every
report and query on the next request — there is nothing to recompute.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..models import (
    Appearance,
    BreakdownItem,
    BreakdownOccurrence,
    SceneLineage,
    SceneStoryDay,
    Character,
    Location,
    Page,
    ReviewItem,
    Scene,
    SceneLocation,
    ScriptElement,
    ScriptVersion,
)
from ..models.enums import (
    CharacterKind,
    ElementType,
    ExtractorKind,
    IntExt,
    LocationKind,
    Presence,
    Resolution,
    ReviewCategory,
    ReviewItemStatus,
    ReviewStatus,
    SceneLocationRole,
    Severity,
    Speaking,
    TimeOfDay,
)
from ..reconciliation.apply import ProposalError, apply_proposal, undo
from .audit import log_event, snapshot

MODELS = {"scene_story_days": SceneStoryDay, "scene_lineage": SceneLineage,
          "breakdown_occurrences": BreakdownOccurrence, "breakdown_items": BreakdownItem,
          "appearances": Appearance, "scenes": Scene, "scene_locations": SceneLocation,
          "characters": Character, "locations": Location, "script_elements": ScriptElement,
          "pages": Page, "script_versions": ScriptVersion}

# Fields the AD may edit, and how to coerce incoming values.
EDITABLE: dict[str, dict[str, Any]] = {
    "appearances": {"presence": Presence, "speaking": Speaking, "character_id": int, "casting_role_id": int,
                    "quantity_min": int, "quantity_max": int, "quantity_raw": str, "dialogue_block_count": int},
    "scenes": {"int_ext": IntExt, "time_of_day": TimeOfDay, "effective_time_of_day": TimeOfDay,
               "scene_number": str, "synopsis": str, "is_omitted": bool, "length_eighths": int},
    "scene_locations": {"location_id": int, "role": SceneLocationRole},
    "characters": {"canonical_name": str, "kind": CharacterKind, "description": str,
                   "quantity_min": int, "quantity_max": int, "quantity_raw": str},
    "locations": {"name": str, "parent_id": int, "kind": LocationKind, "description": str},
    "script_elements": {"element_type": ElementType},
    "breakdown_occurrences": {"quantity_min": int, "quantity_max": int, "quantity_raw": str, "description": str,
                              "is_inferred": bool, "details": dict, "item_id": int},
    "breakdown_items": {"name": str, "category": str, "description": str, "character_id": int, "attributes": dict},
    "scene_story_days": {"story_day": int, "label": str, "is_flashback": bool},
}
HAS_PROVENANCE = {"appearances", "scenes", "scene_locations", "characters", "locations",
                  "breakdown_occurrences", "breakdown_items", "scene_story_days"}
SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


class ReviewError(ValueError):
    pass


def _coerce(table: str, field: str, value):
    kinds = EDITABLE.get(table, {})
    if field not in kinds:
        raise ReviewError(f"{table}.{field} is not editable")
    if value is None:
        return None
    t = kinds[field]
    try:
        return t(value)
    except (ValueError, TypeError) as exc:
        raise ReviewError(f"invalid value {value!r} for {field}") from exc


def _project_of(session: Session, table: str, obj) -> int:
    if hasattr(obj, "project_id"):
        return obj.project_id
    if table == "scene_lineage":
        from ..models import ScriptRevision
        return session.get(ScriptRevision, obj.revision_id).project_id
    if table in ("appearances", "scene_locations", "breakdown_occurrences", "scene_story_days"):
        obj = session.get(Scene, obj.scene_id)
        table = "scenes"
    if table in ("scenes", "pages", "script_elements"):
        return session.get(ScriptVersion, obj.script_version_id).project_id
    raise ReviewError(f"cannot find project for {table}")


# ------------------------------------------------------------------------- fact-level actions
def confirm_fact(session: Session, table: str, entity_id: int, actor: str, note: str | None = None,
                 review_item_id: int | None = None):
    obj = _get(session, table, entity_id)
    before = snapshot(obj)
    if table in HAS_PROVENANCE:
        obj.review_status, obj.needs_review = ReviewStatus.CONFIRMED, False
        obj.reviewed_at, obj.reviewed_by = utcnow(), actor
        if table == "scenes":
            obj.effective_time_needs_review = False
    elif table == "pages":
        obj.needs_review = False
    session.flush()
    log_event(session, _project_of(session, table, obj), obj, "confirm", actor, before, note, review_item_id)
    _resolve_items_for(session, table, entity_id, Resolution.CONFIRMED, actor, note, skip=review_item_id)
    return obj


def edit_fact(session: Session, table: str, entity_id: int, changes: dict[str, Any], actor: str,
              note: str | None = None, review_item_id: int | None = None):
    if not changes:
        raise ReviewError("no changes given")
    obj = _get(session, table, entity_id)
    before = snapshot(obj)
    values = {field: _coerce(table, field, value) for field, value in changes.items()}
    if table == "appearances":
        if "character_id" in values and values["character_id"] != obj.character_id and "casting_role_id" not in values:
            values["casting_role_id"] = None        # a different person: the old casting role no longer fits
        rid = values.get("casting_role_id", obj.casting_role_id)
        if rid is not None:
            from ..models import CastingRole
            role = session.get(CastingRole, rid)
            if role is None or role.character_id != values.get("character_id", obj.character_id):
                raise ReviewError("that casting role belongs to a different character")
    for field, value in values.items():
        setattr(obj, field, value)
    if table == "appearances" and "presence" in changes and obj.presence == Presence.MENTIONED:
        obj.speaking = Speaking.SILENT if obj.dialogue_block_count == 0 else obj.speaking
    if table in HAS_PROVENANCE:
        obj.review_status, obj.needs_review = ReviewStatus.EDITED, False
        obj.reviewed_at, obj.reviewed_by = utcnow(), actor
    session.flush()
    log_event(session, _project_of(session, table, obj), obj, "edit", actor, before, note, review_item_id)
    _resolve_items_for(session, table, entity_id, Resolution.EDITED, actor, note, skip=review_item_id)
    return obj


def reject_fact(session: Session, table: str, entity_id: int, actor: str, note: str | None = None,
                review_item_id: int | None = None):
    if table not in HAS_PROVENANCE:
        raise ReviewError(f"{table} rows cannot be rejected; confirm or edit them instead")
    obj = _get(session, table, entity_id)
    before = snapshot(obj)
    obj.review_status, obj.needs_review = ReviewStatus.REJECTED, False
    obj.reviewed_at, obj.reviewed_by = utcnow(), actor
    session.flush()
    log_event(session, _project_of(session, table, obj), obj, "reject", actor, before, note, review_item_id)
    _resolve_items_for(session, table, entity_id, Resolution.REJECTED, actor, note, skip=review_item_id)
    return obj


def add_appearance(session: Session, scene_id: int, character_id: int, presence: str, speaking: str,
                   actor: str, note: str | None = None) -> Appearance:
    """The AD adds a character the extraction missed."""
    if session.scalars(select(Appearance).where(Appearance.scene_id == scene_id,
                                                Appearance.character_id == character_id,
                                                Appearance.review_status != ReviewStatus.REJECTED)).first():
        raise ReviewError("that character is already recorded in this scene — edit it instead")
    a = Appearance(scene_id=scene_id, character_id=character_id, presence=Presence(presence),
                   speaking=Speaking(speaking), extracted_by=ExtractorKind.HUMAN, confidence=1.0,
                   review_status=ReviewStatus.CONFIRMED, reviewed_at=utcnow(), reviewed_by=actor,
                   source_scene_id=scene_id, source_excerpt=note)
    session.add(a)
    session.flush()
    log_event(session, _project_of(session, "appearances", a), a, "create", actor, None, note)
    return a


def create_character(session: Session, project_id: int, name: str, actor: str, kind: str = "individual") -> Character:
    from ..extraction.cues import normalize_name
    c = Character(project_id=project_id, canonical_name=normalize_name(name), kind=CharacterKind(kind),
                  extracted_by=ExtractorKind.HUMAN, review_status=ReviewStatus.CONFIRMED)
    session.add(c)
    session.flush()
    log_event(session, project_id, c, "create", actor)
    return c


def _get(session: Session, table: str, entity_id: int):
    model = MODELS.get(table)
    if model is None:
        raise ReviewError(f"unknown table {table!r}")
    obj = session.get(model, entity_id)
    if obj is None:
        raise ReviewError(f"{table} {entity_id} not found")
    return obj


def _resolve_items_for(session, table, entity_id, resolution, actor, note, skip=None):
    for item in session.scalars(select(ReviewItem).where(
            ReviewItem.entity_table == table, ReviewItem.entity_id == entity_id,
            ReviewItem.status == ReviewItemStatus.OPEN, ReviewItem.proposal.is_(None))):
        if item.id == skip:
            continue
        _close(item, resolution, actor, note)


def _close(item: ReviewItem, resolution: Resolution, actor: str, note: str | None,
           status: ReviewItemStatus = ReviewItemStatus.RESOLVED):
    item.status, item.resolution = status, resolution
    item.resolved_by, item.resolved_at, item.resolution_note = actor, utcnow(), note


# -------------------------------------------------------------------------- item-level actions
def act_on_item(session: Session, item_id: int, action: str, actor: str, changes: dict | None = None,
                note: str | None = None) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise ReviewError(f"review item {item_id} not found")
    if action == "undo":
        return undo_item(session, item, actor, note)
    if item.status != ReviewItemStatus.OPEN:
        raise ReviewError("this item is already closed")
    if item.proposal:
        if action == "accept":
            try:
                record = apply_proposal(session, item.proposal, actor, changes)
            except ProposalError as exc:
                raise ReviewError(str(exc)) from exc
            item.proposal = {**item.proposal, "applied": record}
            _close(item, Resolution.PROPOSAL_ACCEPTED, actor, note)
        elif action == "decline":
            _close(item, Resolution.PROPOSAL_DECLINED, actor, note)
        else:
            raise ReviewError("proposals can only be accepted or declined")
        session.flush()
        return item
    table, eid = item.entity_table, item.entity_id
    if action == "confirm":
        if table in MODELS:
            confirm_fact(session, table, eid, actor, note, item.id)
        _close(item, Resolution.CONFIRMED, actor, note)
    elif action == "edit":
        edit_fact(session, table, eid, changes or {}, actor, note, item.id)
        _close(item, Resolution.EDITED, actor, note)
    elif action == "reject":
        reject_fact(session, table, eid, actor, note, item.id)
        _close(item, Resolution.REJECTED, actor, note)
    elif action == "dismiss":
        _close(item, Resolution.CONFIRMED, actor, note or "dismissed", ReviewItemStatus.DISMISSED)
    else:
        raise ReviewError(f"unknown action {action!r}")
    session.flush()
    return item


def undo_item(session: Session, item: ReviewItem, actor: str, note: str | None) -> ReviewItem:
    record = (item.proposal or {}).get("applied")
    if item.resolution != Resolution.PROPOSAL_ACCEPTED or not record:
        raise ReviewError("only accepted proposals can be undone")
    undo(session, record, actor)
    proposal = dict(item.proposal)
    proposal.pop("applied")
    item.proposal = proposal
    item.status, item.resolution = ReviewItemStatus.OPEN, None
    item.resolution_note = f"undone by {actor}" + (f": {note}" if note else "")
    session.flush()
    return item


def bulk_act(session: Session, item_ids: list[int], action: str, actor: str, note: str | None = None) -> dict:
    done, errors = [], {}
    for iid in item_ids:
        try:
            act_on_item(session, iid, action, actor, note=note)
            done.append(iid)
        except ReviewError as exc:
            errors[iid] = str(exc)
    return {"done": done, "errors": errors}


# ------------------------------------------------------------------------------- queue views
def list_items(session: Session, project_id: int, version_id: int | None = None, status: str | None = "open",
               category: str | None = None, severity: str | None = None, reason: str | None = None,
               scene_id: int | None = None, limit: int = 500, offset: int = 0) -> dict[str, Any]:
    q = select(ReviewItem).where(ReviewItem.project_id == project_id)
    if version_id:
        q = q.where(ReviewItem.script_version_id == version_id)
    if status:
        q = q.where(ReviewItem.status == ReviewItemStatus(status))
    if category:
        q = q.where(ReviewItem.category == ReviewCategory(category))
    if severity:
        q = q.where(ReviewItem.severity == Severity(severity))
    if reason:
        q = q.where(ReviewItem.reason_code == reason)
    if scene_id:
        q = q.where(ReviewItem.scene_id == scene_id)
    items = session.scalars(q).all()
    # Flagged/low-confidence first: errors, then warnings, then lowest confidence.
    items.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.confidence if i.confidence is not None else 0.0, i.id))
    scenes = {}
    for i in items:
        if i.scene_id and i.scene_id not in scenes:
            sc = session.get(Scene, i.scene_id)
            scenes[i.scene_id] = sc.scene_number or f"#{sc.seq}"
    facets_q = select(ReviewItem.reason_code, ReviewItem.category, ReviewItem.severity, func.count()).where(
        ReviewItem.project_id == project_id, ReviewItem.status == ReviewItemStatus.OPEN)
    if version_id:
        facets_q = facets_q.where(ReviewItem.script_version_id == version_id)
    facets = session.execute(facets_q.group_by(ReviewItem.reason_code, ReviewItem.category,
                                               ReviewItem.severity)).all()
    return {
        "total": len(items),
        "items": [item_dict(i, scenes.get(i.scene_id)) for i in items[offset: offset + limit]],
        "facets": [{"reason": r, "category": c.value, "severity": s.value, "count": n} for r, c, s, n in facets],
    }


def item_dict(i: ReviewItem, scene_label: str | None = None) -> dict[str, Any]:
    return {
        "id": i.id, "category": i.category.value, "reason": i.reason_code, "severity": i.severity.value,
        "message": i.message, "entity_table": i.entity_table, "entity_id": i.entity_id, "scene_id": i.scene_id,
        "scene_label": scene_label, "page_id": i.page_id, "element_id": i.element_id,
        "confidence": i.confidence, "status": i.status.value,
        "resolution": i.resolution.value if i.resolution else None, "resolution_note": i.resolution_note,
        "resolved_by": i.resolved_by, "has_proposal": bool(i.proposal),
        "proposal": {k: v for k, v in (i.proposal or {}).items() if k != "applied"} or None,
        "can_undo": bool((i.proposal or {}).get("applied")) and i.resolution == Resolution.PROPOSAL_ACCEPTED,
        "editable_fields": sorted(EDITABLE.get(i.entity_table or "", {})),
    }


def item_detail(session: Session, item_id: int) -> dict[str, Any]:
    i = session.get(ReviewItem, item_id)
    if i is None:
        raise ReviewError("not found")
    d = item_dict(i)
    entity = None
    if i.entity_table in MODELS and i.entity_id:
        obj = session.get(MODELS[i.entity_table], i.entity_id)
        if obj is not None:
            entity = snapshot(obj)
            if i.entity_table == "appearances":
                entity["character_name"] = session.get(Character, obj.character_id).canonical_name
            if i.entity_table == "scene_locations":
                entity["location_name"] = session.get(Location, obj.location_id).name
            if i.entity_table == "breakdown_occurrences":
                it = session.get(BreakdownItem, obj.item_id)
                entity["item_name"], entity["item_category"] = it.name, it.category
    d["entity"] = entity
    d["source"] = source_passage(session, i)
    return d


def source_passage(session: Session, i: ReviewItem) -> dict[str, Any] | None:
    """The exact script text behind the item, with the supporting lines highlighted."""
    highlight = set()
    if i.element_id:
        highlight.add(i.element_id)
    if i.entity_table in ("appearances", "breakdown_occurrences") and i.entity_id:
        a = session.get(Appearance if i.entity_table == "appearances" else BreakdownOccurrence, i.entity_id)
        if a and a.source_element_start_id:
            highlight.update(range(a.source_element_start_id, (a.source_element_end_id or a.source_element_start_id) + 1))
    if i.scene_id:
        sc = session.get(Scene, i.scene_id)
        els = session.scalars(select(ScriptElement).where(ScriptElement.scene_id == i.scene_id)
                              .order_by(ScriptElement.seq)).all()
        if sc and sc.source_element_start_id and i.entity_table == "scenes":
            highlight.add(sc.source_element_start_id)
    elif i.element_id:
        el = session.get(ScriptElement, i.element_id)
        els = session.scalars(select(ScriptElement).where(
            ScriptElement.script_version_id == el.script_version_id,
            ScriptElement.seq.between(el.seq - 6, el.seq + 6)).order_by(ScriptElement.seq)).all()
    elif i.page_id:
        els = session.scalars(select(ScriptElement).where(ScriptElement.page_id == i.page_id)
                              .order_by(ScriptElement.seq)).all()
    else:
        return None
    labels = dict(session.execute(select(Page.id, Page.page_label)).all()) if els else {}
    return {"elements": [{"id": e.id, "type": e.element_type.value, "text": e.text,
                          "page": labels.get(e.page_id), "highlight": e.id in highlight} for e in els]}


def audit_log(session: Session, table: str, entity_id: int) -> list[dict[str, Any]]:
    from ..models import ReviewEvent
    return [{"id": e.id, "action": e.action, "actor": e.actor, "at": e.at.isoformat(), "note": e.note,
             "before": e.before, "after": e.after}
            for e in session.scalars(select(ReviewEvent).where(ReviewEvent.entity_table == table,
                                                               ReviewEvent.entity_id == entity_id)
                                     .order_by(ReviewEvent.id))]
