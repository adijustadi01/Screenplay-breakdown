"""Stage 2 — Action & effects, Songs/dance/sound, Production requirements, and
queries composing across departments."""
from sqlalchemy import select

from app.departments.reports import run_department_report
from app.departments.service import run_department_extraction
from app.models import BreakdownItem, BreakdownOccurrence, Character, ReviewItem
from app.query.engine import ElementFilter, QueryFilters, run_query

from .dept_helpers import TruthDeptExtractor, dept_pipeline


def test_practical_vs_vfx_flags_are_separate(db, project):
    v = dept_pipeline(db, project, ["action_effects"])
    rows = {i["name"]: i for i in run_department_report(db, v.id, "action_effects")["items"]}
    assert rows["RAIN"]["practical"] == 1 and rows["RAIN"]["vfx_candidate"] == 0
    explosion = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(
        BreakdownItem.name == "FUEL DRUM EXPLOSION")).one()
    assert "practical_or_vfx_undecided" in explosion.review_reason and "safety_review" in explosion.review_reason


def test_voiceover_and_song_derived_in_code(db, project):
    def no_llm(label, out):
        out["sound_music"] = [i for i in out["sound_music"] if i.category not in ("voiceover",)
                              and not i.name.startswith("SONG:")]
        return out
    v = dept_pipeline(db, project, ["sound_music"], TruthDeptExtractor(["sound_music"], no_llm))
    vo = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(
        BreakdownItem.name == "VOICEOVER — RAGHAV")).one()
    assert vo.extracted_by.value == "parser" and vo.confidence == 1.0
    song = db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(
        BreakdownItem.name == "SONG: RANG DE RE")).one()
    assert song.details["playback"] is True and "song_sequence" in song.review_reason


def test_inferred_requirements_are_labelled_and_reviewed(db, project):
    v = dept_pipeline(db, project, None)                    # all departments
    inferred = db.scalars(select(BreakdownOccurrence).where(BreakdownOccurrence.is_inferred.is_(True))).all()
    assert inferred and all(o.needs_review and "inferred" in o.review_reason for o in inferred)
    msgs = [i.message for i in db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "inferred"))]
    assert msgs and all(m.startswith("INFERRED — review.") for m in msgs)
    rows = {i["name"]: i for i in run_department_report(db, v.id, "production")["items"]}
    assert rows["ANIMAL HANDLER / WELFARE OFFICER"]["scenes"]["count"] == 3
    assert rows["DRONE"]["inferred_only"] is False                # stated in the script


def test_production_rules_read_stored_departments(db, project):
    v = dept_pipeline(db, project, ["animals", "action_effects"])
    run_department_extraction(db, v.id, TruthDeptExtractor(["production"],
                                                           lambda label, out: {"production": []}), workers=1)
    names = {i.name for i in db.scalars(select(BreakdownItem).where(BreakdownItem.department == "production"))}
    assert {"ANIMAL HANDLER / WELFARE OFFICER", "STUNT COORDINATOR", "RAIN TOWERS FOR NIGHT EXTERIOR"} <= names


def test_query_composes_across_departments(db, project):
    """'every exterior night scene with the hero, a motorcycle and rain'"""
    v = dept_pipeline(db, project, None)
    hero = db.scalars(select(Character).where(Character.canonical_name == "RAGHAV")).one()
    q = QueryFilters(int_ext=["EXT"], times=["NIGHT"], character_ids=[hero.id],
                     elements=[ElementFilter(department="vehicles", category="motorcycle"),
                               ElementFilter(department="action_effects", category="rain")])
    r = run_query(db, v.id, q)
    assert [s["number"] for s in r["scenes"]] == ["1"]
    assert "motorcycle" in r["description"] and "rain" in r["description"]
    # The hero must be the one riding it:
    q.elements[0].character_id, q.elements[0].role = hero.id, "driver"
    assert [s["number"] for s in run_query(db, v.id, q)["scenes"]] == ["1"]
    # Items as rows: which props are where, split by time
    r = run_query(db, v.id, QueryFilters(rows="item", rows_department="animals", columns="time"))
    assert r["table"]["cells"]["DECORATED WHITE HORSE"]["DAY"]["count"] == 1
