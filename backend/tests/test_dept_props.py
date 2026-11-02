"""Stage 2 — Props."""
from sqlalchemy import select

from app.departments.reports import run_department_report
from app.models import BreakdownItem, BreakdownOccurrence, Character, ReviewItem
from app.query.engine import ElementFilter, QueryFilters, run_query
from app.reconciliation.service import propose
from app.review.service import act_on_item

from .dept_helpers import dept_pipeline

D = ["props"]


def test_users_and_weapon_flag(db, project):
    v = dept_pipeline(db, project, D)
    rows = {x["name"]: x for x in run_department_report(db, v.id, "props")["items"]}
    assert rows["KHAN'S REVOLVER"]["users"] == ["INSPECTOR KHAN"]
    occ = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(BreakdownItem.name == "KHAN'S REVOLVER")).one()
    assert "weapon" in occ.review_reason


def test_query_by_character_role(db, project):
    v = dept_pipeline(db, project, D)
    tara = db.scalars(select(Character).where(Character.canonical_name == "TARA")).one()
    r = run_query(db, v.id, QueryFilters(elements=[ElementFilter(department="props", character_id=tara.id,
                                                                  role="user")]))
    assert [s["number"] for s in r["scenes"]] == ["7"]        # the scissors


def test_variant_names_proposed_not_merged(db, project):
    v = dept_pipeline(db, project, D)
    # add a second name for the lamp in another scene and check a merge is proposed
    lamp = db.scalars(select(BreakdownItem).where(BreakdownItem.name == "BRASS LAMP")).one()
    db.add(BreakdownItem(project_id=lamp.project_id, department=lamp.department, name="LAMP",
                         category=lamp.category, extracted_by=lamp.extracted_by))
    db.flush()
    made = propose(db, v.id)
    assert made.get("item_variant", 0) >= 1
    assert db.scalars(select(BreakdownItem).where(BreakdownItem.name == "LAMP")).one().status.value == "active"
    item = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "item_variant")).first()
    act_on_item(db, item.id, "accept", "AD")
    assert db.scalars(select(BreakdownItem).where(BreakdownItem.name == "LAMP")).one().status.value == "merged"
    act_on_item(db, item.id, "undo", "AD")
    assert db.scalars(select(BreakdownItem).where(BreakdownItem.name == "LAMP")).one().status.value == "active"
