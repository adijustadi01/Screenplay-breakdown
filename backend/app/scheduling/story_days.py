"""Infer STORY days (when scenes happen in the story) — not shooting days.

Rules (deterministic, every result inferred and reviewable):
  * a new story day starts when the time of day goes backwards (NIGHT -> DAY/DAWN) or the
    text says so ("NEXT MORNING", "THE NEXT DAY", "DAYS LATER");
  * CONTINUOUS / SAME / LATER / MOMENTS LATER stay on the previous scene's day;
  * flashbacks (FLASHBACK, or a year in the heading) sit outside the present-day count;
  * a scene with no usable time stays on the previous day and is flagged.
Labels: D2 (day of story day 2), N2 (night of story day 2), DAWN 3, FB (1975)...
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReviewItem, Scene, SceneStoryDay, ScriptElement, ScriptVersion
from ..models.enums import (
    RELATIVE_TIMES,
    ElementType,
    ExtractorKind,
    ReviewCategory,
    ReviewStatus,
    Severity,
    TimeOfDay,
)

PHASE = {TimeOfDay.DAWN: 0, TimeOfDay.SUNRISE: 0, TimeOfDay.MORNING: 1, TimeOfDay.DAY: 2, TimeOfDay.AFTERNOON: 3,
         TimeOfDay.DUSK: 4, TimeOfDay.SUNSET: 4, TimeOfDay.EVENING: 4, TimeOfDay.NIGHT: 5}
PREFIX = {0: "DAWN ", 1: "D", 2: "D", 3: "D", 4: "DUSK ", 5: "N"}
_NEXT_RE = re.compile(r"\b(next (?:day|morning|night|evening)|the following (?:day|morning)|days later|"
                      r"weeks later|months later|years later|a week later|the next)\b", re.I)
_FLASH_RE = re.compile(r"\b(flashback|flash back)\b|\((?:19|20)\d\d\)", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d\d)\b")
LOCKED = (ReviewStatus.CONFIRMED, ReviewStatus.EDITED)


def infer_story_days(session: Session, version_id: int) -> dict[str, int]:
    v = session.get(ScriptVersion, version_id)
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)).all()
    first_action: dict[int, str] = {}
    for e in session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                             .order_by(ScriptElement.seq)):
        if e.scene_id and e.element_type == ElementType.ACTION and e.scene_id not in first_action:
            first_action[e.scene_id] = e.text
    existing = {sd.scene_id: sd for sd in session.scalars(
        select(SceneStoryDay).where(SceneStoryDay.scene_id.in_([s.id for s in scenes])))}
    day, phase = 1, None
    prev: dict | None = None
    flagged = made = 0
    for s in scenes:
        if s.is_omitted:
            continue
        heading = s.heading_raw or ""
        text = f"{heading} {s.time_of_day_raw or ''} {first_action.get(s.id, '')[:160]}"
        reasons: list[str] = []
        if _FLASH_RE.search(f"{heading} {s.time_of_day_raw or ''}"):
            year = _YEAR_RE.search(heading)
            res = {"story_day": None, "label": f"FB ({year.group(1)})" if year else "FB", "is_flashback": True,
                   "basis": "flashback marker in heading"}
        elif s.time_of_day in RELATIVE_TIMES and prev is not None:
            res = dict(prev, basis=f"{s.time_of_day.value}: same story day as the previous scene")
        else:
            p = PHASE.get(s.effective_time_of_day or s.time_of_day)
            basis = []
            if _NEXT_RE.search(text):
                day += 1 if phase is not None else 0
                basis.append(f"'{_NEXT_RE.search(text).group(0)}'")
            elif p is not None and phase is not None and p < phase:
                day += 1
                basis.append("time of day went backwards")
            if p is None:
                p = phase if phase is not None else 2
                reasons.append("no_usable_time")
                basis.append("no usable time of day — kept on previous day")
            phase = p
            res = {"story_day": day, "label": f"{PREFIX[p]}{day}", "is_flashback": False,
                   "basis": "; ".join(basis) or "same part of the story day"}
        prev = {k: res[k] for k in ("story_day", "label", "is_flashback")}
        old = existing.get(s.id)
        if old is not None and (old.review_status in LOCKED or old.extracted_by == ExtractorKind.HUMAN):
            prev = {"story_day": old.story_day, "label": old.label, "is_flashback": old.is_flashback}
            if not old.is_flashback and old.story_day:
                day = old.story_day
            continue
        if old is not None:
            session.delete(old)
            session.flush()
        sd = SceneStoryDay(scene_id=s.id, story_day=res["story_day"], label=res["label"],
                           is_flashback=res["is_flashback"], basis=res["basis"][:255],
                           extracted_by=ExtractorKind.RULES, confidence=0.4 if reasons else 0.6,
                           needs_review=True, review_reason=",".join(["inferred_story_day", *reasons]),
                           source_scene_id=s.id, source_excerpt=heading[:500])
        session.add(sd)
        session.flush()
        made += 1
        if reasons:
            flagged += 1
            session.add(ReviewItem(
                project_id=v.project_id, script_version_id=version_id, category=ReviewCategory.EXTRACTION,
                reason_code="story_day_uncertain", severity=Severity.WARNING,
                message=f"Scene {s.scene_number or f'#{s.seq}'}: story day {sd.label} is a guess ({sd.basis}).",
                entity_table="scene_story_days", entity_id=sd.id, scene_id=s.id, confidence=sd.confidence))
    session.add(ReviewItem(
        project_id=v.project_id, script_version_id=version_id, category=ReviewCategory.EXTRACTION,
        reason_code="story_days_inferred", severity=Severity.INFO,
        message=f"Story days inferred for {made} scene(s) from times of day and transitions "
                f"(story time, NOT shooting days). Check them in the Schedule tab.",
        entity_table="script_versions", entity_id=version_id))
    session.flush()
    return {"inferred": made, "flagged": flagged, "story_days": day}
