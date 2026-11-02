"""Stage 2 — Background performers and Animals."""
from sqlalchemy import select

from app.departments import registry
from app.departments.heuristic import DepartmentHeuristicExtractor
from app.departments.reports import run_department_report
from app.models import BreakdownItem, BreakdownOccurrence, ReviewItem

from .dept_helpers import dept_pipeline


def _occ(db, name):
    return db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(BreakdownItem.name == name)).one()


def test_unknown_crowd_never_gets_a_number(db, project):
    for ex in (None, DepartmentHeuristicExtractor(registry.get(["background"]))):
        v = dept_pipeline(db, project, ["background"], ex)
        r = run_department_report(db, v.id, "background")
        villagers = next(i for i in r["items"] if "VILLAGERS" in i["name"])
        assert villagers["max_in_one_scene"] is None and villagers["unknown_quantity"]["count"] == 1
        occ = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(
            BreakdownItem.name.like("%VILLAGERS%"), BreakdownItem.project_id == project.id)).first()
        assert "unknown_quantity" in occ.review_reason


def test_stated_headcounts_and_peak(db, project):
    v = dept_pipeline(db, project, ["background"])
    r = run_department_report(db, v.id, "background")
    rows = {i["name"]: i for i in r["items"]}
    assert rows["DANCERS"]["max_in_one_scene"] == 200 and rows["DHOL PLAYERS"]["max_in_one_scene"] == 6
    assert r["peak_units_in_one_scene"] == {"count": 206, "scene_id": r["peak_units_in_one_scene"]["scene_id"],
                                            "scene": "2"}
    assert r["scenes_with_unknown_quantity"]["count"] == 2     # villagers (2), Bhim's men (4)
    assert "large_crowd" in _occ(db, "DANCERS").review_reason


def test_every_animal_needs_handler_assessment(db, project):
    v = dept_pipeline(db, project, ["animals"])
    occs = db.scalars(select(BreakdownOccurrence)).all()
    assert len(occs) == 4 and all("handler_assessment" in o.review_reason for o in occs)
    rows = {i["name"]: i for i in run_department_report(db, v.id, "animals")["items"]}
    assert rows["GOLDEN RETRIEVER"]["owner"] == "OLD RAGHAV"
    assert db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "handler_assessment")).first()
