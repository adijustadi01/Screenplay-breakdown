"""Checks every registered department must pass (runs automatically for each new module)."""
import pytest
from sqlalchemy import select

from app.departments import registry
from app.departments.evaluate import evaluate_departments
from app.departments.heuristic import DepartmentHeuristicExtractor
from app.models import BreakdownOccurrence, ReviewItem

from .conftest import DEPT_TRUTH_PATH
from .dept_helpers import TruthDeptExtractor, dept_pipeline

DEPTS = [d.value for d in registry.SPECS]
# Minimum recall of the offline rules on the sample (a baseline, not the LLM).
OFFLINE_MIN_RECALL = {"vehicles": 80, "props": 60, "costumes": 50, "makeup_hair": 40, "background": 60,
                      "animals": 80, "action_effects": 50, "sound_music": 40, "production": 40}


@pytest.mark.parametrize("dept", DEPTS)
def test_perfect_extraction_round_trips(db, project, dept):
    v = dept_pipeline(db, project, [dept])
    r = evaluate_departments(db, v.id, DEPT_TRUTH_PATH, [dept])["departments"][dept]
    assert r["recall"] == 100.0, r
    assert r["quantity_accuracy"] in (100.0, None) and r["character_accuracy"] in (100.0, None)
    assert r["inferred_flagged"] in (100.0, None)


@pytest.mark.parametrize("dept", DEPTS)
def test_every_item_has_provenance(db, project, dept):
    dept_pipeline(db, project, [dept])
    occs = db.scalars(select(BreakdownOccurrence)).all()
    assert occs
    for o in occs:
        assert o.source_excerpt and o.source_scene_id and o.confidence is not None and o.extracted_by


@pytest.mark.parametrize("dept", DEPTS)
def test_flagged_items_reach_the_review_queue(db, project, dept):
    dept_pipeline(db, project, [dept])
    for o in db.scalars(select(BreakdownOccurrence).where(BreakdownOccurrence.needs_review.is_(True))):
        assert db.scalars(select(ReviewItem).where(ReviewItem.entity_table == "breakdown_occurrences",
                                                   ReviewItem.entity_id == o.id)).first(), o.review_reason


@pytest.mark.parametrize("dept", DEPTS)
def test_offline_baseline(db, project, dept):
    v = dept_pipeline(db, project, [dept], DepartmentHeuristicExtractor(registry.get([dept])))
    r = evaluate_departments(db, v.id, DEPT_TRUTH_PATH, [dept])["departments"][dept]
    assert (r["recall"] or 0) >= OFFLINE_MIN_RECALL[dept], r
