"""Stage 2 — Vehicles."""
from sqlalchemy import select

from app.departments import registry
from app.departments.evaluate import evaluate_departments
from app.departments.heuristic import DepartmentHeuristicExtractor
from app.departments.reports import run_department_report
from app.models import BreakdownItem, BreakdownOccurrence, ReviewItem
from app.query.engine import ElementFilter, QueryFilters, run_query

from .conftest import DEPT_TRUTH_PATH
from .dept_helpers import TruthDeptExtractor, dept_pipeline

D = ["vehicles"]


def test_identity_vs_quantity(db, project):
    v = dept_pipeline(db, project, D)
    r = run_department_report(db, v.id, "vehicles")
    rows = {x["name"]: x for x in r["items"]}
    assert rows["KHAN'S POLICE JEEP"]["scenes"]["count"] == 2          # one jeep, two scenes
    assert rows["KHAN'S POLICE JEEP"]["max_in_one_scene"] == 1
    assert rows["POLICE JEEPS"]["max_in_one_scene"] == 3                # three together
    assert rows["RAGHAV'S ROYAL ENFIELD"]["scenes"]["count"] == 2
    assert r["unique_items"] == 4
    assert r["peak_units_in_one_scene"]["count"] == 3 and r["peak_units_in_one_scene"]["scene"] == "4"


def test_drivers_passengers_and_owner(db, project):
    v = dept_pipeline(db, project, D)
    rows = {x["name"]: x for x in run_department_report(db, v.id, "vehicles")["items"]}
    bike = rows["RAGHAV'S ROYAL ENFIELD"]
    assert bike["drivers"] == ["RAGHAV"] and bike["owner"] == "RAGHAV"
    assert {(c["name"], c["role"]) for c in bike["characters"]} >= {("RAGHAV", "driver"), ("TARA", "passenger")}
    assert bike["moving_scenes"] == 1 and "parked" in bike["actions"] and "chase" in bike["actions"]


def test_perfect_extraction_scores_100(db, project):
    v = dept_pipeline(db, project, D)
    r = evaluate_departments(db, v.id, DEPT_TRUTH_PATH, D)["departments"]["vehicles"]
    assert r["recall"] == 100.0 and r["quantity_accuracy"] == 100.0 and r["character_accuracy"] == 100.0


def test_chase_flagged_for_review(db, project):
    dept_pipeline(db, project, D)
    reasons = {i.reason_code for i in db.scalars(select(ReviewItem).where(
        ReviewItem.entity_table == "breakdown_occurrences"))}
    assert "vehicle_action" in reasons


def test_unknown_driver_flagged(db, project):
    def drop_driver(label, out):
        if label == "4":
            for it in out["vehicles"]:
                it.quantity_min = it.quantity_max = 1
                it.characters = []
        return out
    dept_pipeline(db, project, D, TruthDeptExtractor(D, drop_driver))
    occ = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(BreakdownItem.name == "POLICE JEEPS")).one()
    assert "driver_not_identified" in occ.review_reason


def test_query_motorcycle_filter(db, project):
    v = dept_pipeline(db, project, D)
    r = run_query(db, v.id, QueryFilters(elements=[ElementFilter(department="vehicles", category="motorcycle")]))
    assert [s["number"] for s in r["scenes"]] == ["1", "8"]
    r = run_query(db, v.id, QueryFilters(elements=[ElementFilter(department="vehicles", category="motorcycle")],
                                         int_ext=["EXT"], times=["NIGHT"]))
    assert [s["number"] for s in r["scenes"]] == ["1"]


def test_offline_baseline_finds_vehicles(db, project):
    v = dept_pipeline(db, project, D, DepartmentHeuristicExtractor(registry.get(D)))
    r = evaluate_departments(db, v.id, DEPT_TRUTH_PATH, D)["departments"]["vehicles"]
    assert r["recall"] >= 80
    # everything the offline rules find is low confidence -> reviewed
    assert all(o.needs_review for o in db.scalars(select(BreakdownOccurrence)))
