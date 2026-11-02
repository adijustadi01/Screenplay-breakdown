"""STAGE 1 ACCEPTANCE — one test per requirement in the Stage 1 brief.

Run:  pytest tests/test_stage1_acceptance.py -v
Each test name says which requirement it proves. All must pass for Stage 1 to be complete.
"""
import pytest
from sqlalchemy import select

from app.extraction.evaluate import evaluate
from app.importers.coverage import coverage_report
from app.importers.service import import_script
from app.models import Appearance, Character, Location, ReviewEvent, ReviewItem, Scene
from app.models.enums import Presence, TimeOfDay
from app.query.engine import QueryFilters, run_query
from app.reports.service import run_report
from app.review.service import act_on_item, edit_fact

from .conftest import GROUND_TRUTH, HAS_TESSERACT, SAMPLES
from .helpers import pipeline, scenes

TRUTH = str(SAMPLES / "monsoon_ledger.ground_truth.json")
FORMATS = ["monsoon_ledger.pdf", "monsoon_ledger.docx", "monsoon_ledger.fdx", "monsoon_ledger.fountain",
           pytest.param("monsoon_ledger_scanned.pdf",
                        marks=pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed"))]


# ---------------------------------------------------------------- Step 1: import + coverage
@pytest.mark.parametrize("fmt", FORMATS)
def test_step1_imports_every_format_with_complete_coverage(db, project, fmt):
    v = import_script(db, project.id, SAMPLES / fmt)
    r = coverage_report(db, v.id)
    assert r["verdict"] == "COMPLETE" and r["text_accounting"]["difference"] == 0
    assert r["scenes"]["total"] == GROUND_TRUTH["scene_count"]


def test_step1_scene_numbers_preserved_and_missing_flagged_not_invented(db, project):
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.pdf")
    nums = [s.scene_number for s in scenes(db, v)]
    assert nums == [s["number"] for s in GROUND_TRUTH["scenes"]]   # includes "8A" and one None
    assert db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "missing_scene_number")).first()


def test_step1_time_of_day_never_collapsed(db, project):
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.pdf")
    times = {s.time_of_day for s in scenes(db, v)}
    assert {TimeOfDay.DUSK, TimeOfDay.DAWN, TimeOfDay.CONTINUOUS, TimeOfDay.LATER} <= times
    cont = next(s for s in scenes(db, v) if s.time_of_day == TimeOfDay.CONTINUOUS)
    assert cont.effective_time_of_day == TimeOfDay.DAY and cont.effective_time_needs_review


def test_step1_ambiguous_heading_flagged_not_guessed(db, project):
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.pdf")
    roof = next(s for s in scenes(db, v) if s.location_raw == "HOSPITAL ROOFTOP")
    assert roof.int_ext.value == "UNKNOWN" and roof.needs_review


# ------------------------------------------------------------- Step 2: per-scene extraction
def test_step2_present_vs_mentioned_and_speaking_vs_silent(db, project):
    v = pipeline(db, project)
    s1 = scenes(db, v)[0]
    by = {db.get(Character, a.character_id).canonical_name: a
          for a in db.scalars(select(Appearance).where(Appearance.scene_id == s1.id))}
    assert by["VIKRAM"].presence == Presence.MENTIONED          # talked about, NOT an appearance
    assert by["DADI"].presence == Presence.PRESENT and by["DADI"].speaking.value == "silent"
    assert by["KABIR"].presence == Presence.OFF_SCREEN


def test_step2_every_fact_has_provenance_confidence_and_flag(db, project):
    pipeline(db, project)
    for a in db.scalars(select(Appearance)):
        assert a.source_scene_id and a.source_excerpt and a.extracted_by and a.confidence is not None
        assert a.needs_review in (True, False)


def test_step2_unknown_headcount_preserved(db, project):
    pipeline(db, project)
    men = db.scalars(select(Appearance).join(Character).where(Character.canonical_name == "VIKRAM'S MEN")).one()
    assert men.quantity_min is None and men.needs_review


# ------------------------------------------------------------------ Step 3: reconciliation
def test_step3_merges_are_proposed_in_queue_and_accepting_reaches_truth(db, project):
    v = pipeline(db, project)
    assert evaluate(db, v.id, TRUTH)["location_accuracy"] < 100   # before reconciliation
    v2 = pipeline(db, project, reconcile=True)
    r = evaluate(db, v2.id, TRUTH)
    assert r["location_accuracy"] == 100.0 and r["appearances"]["precision"] == 100.0


# ------------------------------------------------------------------- Step 4: reports in code
@pytest.mark.parametrize("name", ["scenes", "characters", "locations", "combinations",
                                  "crosstab:character_time", "crosstab:location_time",
                                  "crosstab:character_location_time"])
def test_step4_reports_exist_and_every_count_has_its_scenes(db, project, name):
    v = pipeline(db, project, reconcile=True)
    r = run_report(db, v.id, name)

    def walk(x):
        if isinstance(x, dict):
            if "count" in x and "scene_ids" in x:
                assert x["count"] == len(x["scene_ids"])
            for y in x.values():
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(r)


# ----------------------------------------------------------------------- Step 5: review queue
def test_step5_correction_propagates_to_reports_and_is_audited(db, project):
    v = pipeline(db, project, reconcile=True)
    dadi = db.scalars(select(Character).where(Character.canonical_name == "DADI")).one()
    before = next(c for c in run_report(db, v.id, "characters")["characters"] if c["name"] == "DADI")
    a = db.scalars(select(Appearance).where(Appearance.character_id == dadi.id)).first()
    edit_fact(db, "appearances", a.id, {"presence": "mentioned"}, "AD")
    after = next(c for c in run_report(db, v.id, "characters")["characters"] if c["name"] == "DADI")
    assert after["appearances"]["count"] == before["appearances"]["count"] - 1
    assert db.scalars(select(ReviewEvent).where(ReviewEvent.entity_id == a.id,
                                                ReviewEvent.action == "edit")).first()


def test_step5_merge_can_be_undone(db, project):
    v = pipeline(db, project, reconcile=True)
    item = db.scalars(select(ReviewItem).where(ReviewItem.script_version_id == v.id,
                                               ReviewItem.reason_code == "location_parent")).one()
    act_on_item(db, item.id, "undo", "AD")
    assert db.scalars(select(Location).where(Location.name == "HOSPITAL ROOFTOP")).one().parent_id is None


# ------------------------------------------------------------------------ Step 6: query panel
def test_step6_composable_filters_with_drilldown(db, project):
    v = pipeline(db, project, reconcile=True)
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    hosp = db.scalars(select(Location).where(Location.name == "CITY HOSPITAL")).one()
    r = run_query(db, v.id, QueryFilters(int_ext=["EXT"], times=["NIGHT"], character_ids=[arjun.id]))
    assert [s["number"] for s in r["scenes"]] == ["14"]
    r = run_query(db, v.id, QueryFilters(location_ids=[hosp.id], rows="character", columns="time"))
    cell = r["table"]["cells"]["MEERA"]["NIGHT"]
    assert cell["count"] == 2 and all(db.get(Scene, i) for i in cell["scene_ids"])
