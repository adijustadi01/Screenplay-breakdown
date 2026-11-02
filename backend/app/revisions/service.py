"""Compare drafts, store the scene lineage, carry reviewed facts forward.

carry_forward copies the facts of UNCHANGED scenes (cast, location, department items,
story day, synopsis — including the AD's confirmations and edits) to the new draft, so
only changed and added scenes need extraction.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Appearance,
    BreakdownOccurrence,
    OccurrenceCharacter,
    ReviewItem,
    Scene,
    SceneLineage,
    SceneLocation,
    SceneStoryDay,
    ScriptElement,
    ScriptRevision,
    ScriptVersion,
)
from ..models.enums import LineageStatus, ReviewCategory, ReviewItemStatus, Severity
from ..review.audit import snapshot
from .matcher import match_versions

REASON_TEXT = {
    "low_similarity": "same scene number but the text changed a lot — is it still the same scene?",
    "content_match_uncertain": "matched by similar text only — confirm it is the same scene",
    "renumbered": "the scene number changed between drafts",
    "number_reused": "this scene number belonged to a different scene in the previous draft",
}


def compare(session: Session, old_version_id: int, new_version_id: int) -> ScriptRevision:
    old_v, new_v = session.get(ScriptVersion, old_version_id), session.get(ScriptVersion, new_version_id)
    if old_v is None or new_v is None or old_v.project_id != new_v.project_id:
        raise ValueError("both drafts must exist and belong to the same project")
    rev = session.scalars(select(ScriptRevision).where(ScriptRevision.from_version_id == old_version_id,
                                                       ScriptRevision.to_version_id == new_version_id)).first()
    if rev is None:
        rev = ScriptRevision(project_id=old_v.project_id, from_version_id=old_version_id,
                             to_version_id=new_version_id)
        session.add(rev)
        session.flush()
    else:
        for row in session.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id)):
            session.delete(row)
        for item in session.scalars(select(ReviewItem).where(ReviewItem.category == ReviewCategory.REVISION,
                                                             ReviewItem.script_version_id == new_version_id,
                                                             ReviewItem.status == ReviewItemStatus.OPEN)):
            item.status = ReviewItemStatus.DISMISSED
            item.resolution_note = "superseded by a new comparison"
        session.flush()
    counts: dict[str, int] = {}
    for m in match_versions(session, old_version_id, new_version_id):
        delta = None
        if m.old is not None and m.new is not None:
            delta = (m.new.length_eighths or 0) - (m.old.length_eighths or 0)
        row = SceneLineage(revision_id=rev.id, old_scene_id=m.old.id if m.old else None,
                           new_scene_id=m.new.id if m.new else None, status=m.status, method=m.method,
                           similarity=round(m.similarity, 3) if m.similarity is not None else None,
                           renumbered=m.renumbered, heading_changed=m.heading_changed, length_delta_eighths=delta,
                           needs_review=bool(m.reasons), review_reason=",".join(m.reasons) or None)
        session.add(row)
        session.flush()
        counts[m.status.value] = counts.get(m.status.value, 0) + 1
        if m.reasons:
            label = _label(m.new or m.old)
            session.add(ReviewItem(
                project_id=rev.project_id, script_version_id=new_version_id, category=ReviewCategory.REVISION,
                reason_code=m.reasons[0], severity=Severity.WARNING,
                message=f"Revision: scene {label}: " + "; ".join(REASON_TEXT.get(r, r) for r in m.reasons)
                        + (f" (similarity {m.similarity:.0%})" if m.similarity is not None else ""),
                entity_table="scene_lineage", entity_id=row.id, scene_id=(m.new or m.old).id,
                confidence=m.similarity))
    rev.stats = counts
    session.flush()
    return rev


def _label(sc: Scene) -> str:
    return sc.scene_number or f"#{sc.seq}"


def rematch(session: Session, lineage_id: int, old_scene_id: int | None, actor: str) -> SceneLineage:
    """The AD links a new scene to a different old scene (or to none = added)."""
    from ..models.enums import MatchMethod
    from .matcher import load_texts, similarity
    row = session.get(SceneLineage, lineage_id)
    rev = session.get(ScriptRevision, row.revision_id)
    # free the old scene from any other row in this revision
    if old_scene_id is not None:
        for other in session.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id,
                                                                SceneLineage.old_scene_id == old_scene_id,
                                                                SceneLineage.id != row.id)):
            if other.new_scene_id is None:
                session.delete(other)
            else:
                other.old_scene_id, other.status, other.method = None, LineageStatus.ADDED, MatchMethod.NONE
    if row.old_scene_id and row.old_scene_id != old_scene_id:
        session.add(SceneLineage(revision_id=rev.id, old_scene_id=row.old_scene_id, new_scene_id=None,
                                 status=LineageStatus.REMOVED, method=MatchMethod.NONE))
    row.old_scene_id = old_scene_id
    row.method = MatchMethod.MANUAL
    row.needs_review, row.review_reason, row.note = False, None, f"matched by {actor}"
    if old_scene_id is None:
        row.status, row.similarity = LineageStatus.ADDED, None
    else:
        texts = {t.scene.id: t for t in load_texts(session, rev.from_version_id) + load_texts(session, rev.to_version_id)}
        o, n = texts[old_scene_id], texts[row.new_scene_id]
        row.similarity = round(similarity(o.body, n.body), 3)
        row.status = LineageStatus.UNCHANGED if (o.body == n.body and o.heading_key == n.heading_key) \
            else LineageStatus.CHANGED
    session.flush()
    return row


def _element_map(session: Session, old_scene_id: int, new_scene_id: int) -> dict[int, int]:
    """Old element id -> new element id, pairing equal texts in order."""
    o = session.scalars(select(ScriptElement).where(ScriptElement.scene_id == old_scene_id)
                        .order_by(ScriptElement.seq)).all()
    n = session.scalars(select(ScriptElement).where(ScriptElement.scene_id == new_scene_id)
                        .order_by(ScriptElement.seq)).all()
    out, j = {}, 0
    for e in o:
        for k in range(j, len(n)):
            if n[k].text == e.text:
                out[e.id] = n[k].id
                j = k + 1
                break
    return out


def _clone(obj, overrides: dict):
    data = snapshot(obj)
    data.pop("id", None)
    model = type(obj)
    cols = {c.key for c in model.__mapper__.column_attrs}
    kwargs = {}
    for k, v in data.items():
        if k not in cols:
            continue
        kwargs[k] = getattr(obj, k)          # keep enums/objects as-is
    kwargs.update(overrides)
    return model(**kwargs)


def carry_forward(session: Session, revision_id: int) -> dict[str, int]:
    rev = session.get(ScriptRevision, revision_id)
    copied = {"scenes": 0, "appearances": 0, "scene_locations": 0, "breakdown_occurrences": 0, "story_days": 0}
    for row in session.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id,
                                                          SceneLineage.status == LineageStatus.UNCHANGED)):
        if row.old_scene_id is None or row.new_scene_id is None:
            continue
        old_sc, new_sc = session.get(Scene, row.old_scene_id), session.get(Scene, row.new_scene_id)
        # Fill only what the new scene is missing, per fact type and per department.
        has_cast = session.scalars(select(Appearance.id).where(Appearance.scene_id == new_sc.id)).first() is not None
        has_loc = session.scalars(select(SceneLocation.id).where(SceneLocation.scene_id == new_sc.id)).first() is not None
        new_depts = {d for (d,) in session.execute(select(BreakdownOccurrence.department).where(
            BreakdownOccurrence.scene_id == new_sc.id))}
        emap = _element_map(session, old_sc.id, new_sc.id)
        before = sum(copied.values())

        def common(o):
            return {"scene_id": new_sc.id, "source_scene_id": new_sc.id,
                    "source_element_start_id": emap.get(o.source_element_start_id),
                    "source_element_end_id": emap.get(o.source_element_end_id)}
        fact_map: dict[tuple[str, int], int] = {}
        for a in ([] if has_cast else session.scalars(select(Appearance).where(Appearance.scene_id == old_sc.id)).all()):
            c = _clone(a, common(a))
            session.add(c)
            session.flush()
            fact_map[("appearances", a.id)] = c.id
            copied["appearances"] += 1
        for sl in ([] if has_loc else session.scalars(select(SceneLocation).where(SceneLocation.scene_id == old_sc.id)).all()):
            c = _clone(sl, common(sl))
            session.add(c)
            session.flush()
            fact_map[("scene_locations", sl.id)] = c.id
            copied["scene_locations"] += 1
        for o in session.scalars(select(BreakdownOccurrence).where(BreakdownOccurrence.scene_id == old_sc.id)).all():
            if o.department in new_depts:
                continue
            c = _clone(o, common(o))
            session.add(c)
            session.flush()
            for oc in o.characters:
                session.add(OccurrenceCharacter(occurrence_id=c.id, character_id=oc.character_id, role=oc.role))
            fact_map[("breakdown_occurrences", o.id)] = c.id
            copied["breakdown_occurrences"] += 1
        sd = session.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == old_sc.id)).first()
        if sd and not session.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == new_sc.id)).first():
            session.add(_clone(sd, {"scene_id": new_sc.id, "source_scene_id": new_sc.id,
                                    "source_element_start_id": None, "source_element_end_id": None}))
            copied["story_days"] += 1
        if old_sc.synopsis and not new_sc.synopsis:
            new_sc.synopsis, new_sc.synopsis_run_id = old_sc.synopsis, old_sc.synopsis_run_id
        # open review items on carried facts follow them to the new draft
        for item in session.scalars(select(ReviewItem).where(ReviewItem.scene_id == old_sc.id,
                                                             ReviewItem.status == ReviewItemStatus.OPEN,
                                                             ReviewItem.category == ReviewCategory.EXTRACTION)):
            new_id = fact_map.get((item.entity_table, item.entity_id))
            if new_id is None:
                continue
            session.add(ReviewItem(project_id=item.project_id, script_version_id=new_sc.script_version_id,
                                   category=item.category, reason_code=item.reason_code, severity=item.severity,
                                   message=item.message, entity_table=item.entity_table, entity_id=new_id,
                                   scene_id=new_sc.id, confidence=item.confidence))
        row.carried_forward = True
        if sum(copied.values()) > before:
            copied["scenes"] += 1
    session.flush()
    return copied


def scenes_needing_extraction(session: Session, revision_id: int) -> list[int]:
    rows = session.scalars(select(SceneLineage).where(SceneLineage.revision_id == revision_id)).all()
    return [r.new_scene_id for r in rows if r.new_scene_id and r.status in (LineageStatus.CHANGED, LineageStatus.ADDED)
            and not r.carried_forward]


def revise(session: Session, old_version_id: int, new_version_id: int, extractor=None, dept_extractor=None,
           workers: int = 4) -> dict:
    """Full revision workflow: match scenes, carry unchanged facts forward, extract only what
    changed (cast/locations and, if given, departments), propose merges, report."""
    from ..extraction.service import run_extraction
    from ..reconciliation.service import propose
    rev = compare(session, old_version_id, new_version_id)
    copied = carry_forward(session, rev.id)
    todo = scenes_needing_extraction(session, rev.id)
    if extractor is not None and todo:
        run_extraction(session, new_version_id, extractor, workers=workers, scene_ids=todo)
    if dept_extractor is not None and todo:
        from ..departments.service import run_department_extraction
        run_department_extraction(session, new_version_id, dept_extractor, workers=workers, scene_ids=todo)
    proposals = propose(session, new_version_id) if extractor is not None else {}
    session.flush()
    return {"revision_id": rev.id, "carried_forward": copied, "extracted_scenes": len(todo) if extractor else 0,
            "proposals": proposals}
