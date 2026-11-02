"""STAGE 2 ACCEPTANCE — one test per requirement in the Stage 2 brief.

Run:  pytest tests/test_stage2_acceptance.py -v
"""
import pytest
from sqlalchemy import select

from app.departments import registry
from app.departments.reports import run_department_report
from app.models import BreakdownItem, BreakdownOccurrence, Character, ReviewItem
from app.models.enums import Department
from app.query.engine import ElementFilter, QueryFilters, run_query
from app.review.service import act_on_item

from .dept_helpers import dept_pipeline

ALL = [d.value for d in Department]


def test_all_nine_departments_exist_each_as_its_own_module():
    assert sorted(d.value for d in registry.SPECS) == sorted(ALL)
    for spec in registry.SPECS.values():
        assert spec.prompt and spec.categories and spec.lexicon is not None


@pytest.fixture()
def v(db, project):
    return dept_pipeline(db, project, None)


def _report(db, v, d):
    return {i["name"]: i for i in run_department_report(db, v.id, d)["items"]}


def test_vehicles_identity_vs_quantity_drivers_passengers_actions(db, v):
    rows = _report(db, v, "vehicles")
    assert rows["KHAN'S POLICE JEEP"]["scenes"]["count"] == 2 and rows["KHAN'S POLICE JEEP"]["max_in_one_scene"] == 1
    assert rows["POLICE JEEPS"]["max_in_one_scene"] == 3
    bike = rows["RAGHAV'S ROYAL ENFIELD"]
    assert bike["drivers"] == ["RAGHAV"] and any(c["role"] == "passenger" for c in bike["characters"])
    assert "chase" in bike["actions"]


def test_props_who_uses_them_where_which_scenes(db, v):
    rows = _report(db, v, "props")
    assert rows["SCISSORS"]["users"] == ["TARA"] and rows["SCISSORS"]["first_scene"] == "7"
    assert rows["KHAN'S REVOLVER"]["category"] == "weapon"


def test_costumes_per_character_changes_uniform_period_and_states(db, v):
    rows = _report(db, v, "costumes")
    assert rows["KHAN — KHAKI POLICE UNIFORM"]["category"] == "uniform"
    assert rows["DEV — 1970S BELL-BOTTOMS AND WIDE-COLLARED SHIRT"]["category"] == "period"
    assert set(rows["RAGHAV — LEATHER JACKET"]["states"]) == {"wet", "damp"}
    assert "bloodied" in rows["RAGHAV — SHIRT"]["states"] and "torn" in rows["TARA — DUPATTA"]["states"]


def test_makeup_hair_injury_ageing_prosthetic_disguise_hairstyle_continuity(db, v):
    cats = {i["category"] for i in _report(db, v, "makeup_hair").values()}
    assert {"injury", "ageing", "prosthetic", "disguise", "hairstyle"} <= cats
    cont = run_department_report(db, v.id, "continuity")
    raghav = next(c for c in cont["characters"] if c["name"] == "RAGHAV")
    states = [e["state"] for t in raghav["timeline"] for e in t["entries"] if e["item"] == "RAGHAV — FOREHEAD CUT"]
    assert states == ["fresh", "bandaged"]


def test_background_quantity_where_specified_unknown_where_not(db, v):
    rows = _report(db, v, "background")
    assert rows["DANCERS"]["max_in_one_scene"] == 200 and rows["CONSTABLES"]["max_in_one_scene"] == 2
    assert rows["VILLAGERS"]["max_in_one_scene"] is None and rows["VILLAGERS"]["unknown_quantity"]["count"] == 1


def test_animals_with_handler_assessment_flag(db, v):
    rows = _report(db, v, "animals")
    assert len(rows) == 4 and all(r["handler_assessment_required"] for r in rows.values())


def test_action_effects_practical_vs_vfx_separate(db, v):
    rows = _report(db, v, "action_effects")
    assert {"fight", "fall", "chase", "explosion", "rain", "gunfire"} <= {r["category"] for r in rows.values()}
    assert rows["RAIN"]["practical"] == 1
    assert rows["FUEL DRUM EXPLOSION"]["undecided"] == 1      # not guessed: flagged for the VFX/SFX call


def test_songs_dance_sound_playback_voiceover_scripted(db, v):
    rows = _report(db, v, "sound_music")
    assert rows["SONG: RANG DE RE"]["playback"] and rows["SONG: RANG DE RE"]["song_title"] == "RANG DE RE"
    assert "VOICEOVER — RAGHAV" in rows and rows["TEMPLE BELLS"]["category"] == "scripted_sound"


def test_production_requirements_inferred_clearly_flagged(db, v):
    rows = _report(db, v, "production")
    assert rows["DRONE"]["inferred_only"] is False and rows["CROWD MANAGEMENT"]["inferred_only"] is True
    for o in db.scalars(select(BreakdownOccurrence).where(BreakdownOccurrence.is_inferred.is_(True))):
        assert o.needs_review
    assert all(i.message.startswith("INFERRED — review.") for i in
               db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "inferred")))


def test_every_item_keeps_source_passage_and_confidence(db, v):
    for o in db.scalars(select(BreakdownOccurrence)):
        assert o.source_excerpt and o.confidence is not None and o.source_scene_id


def test_same_review_queue_correction_propagates(db, v):
    item = db.scalars(select(ReviewItem).where(ReviewItem.entity_table == "breakdown_occurrences",
                                               ReviewItem.reason_code == "unknown_quantity")).first()
    occ = db.get(BreakdownOccurrence, item.entity_id)
    name = db.get(BreakdownItem, occ.item_id).name
    act_on_item(db, item.id, "edit", "AD", {"quantity_min": 40, "quantity_max": 40}, "counted from the storyboard")
    row = next(i for i in run_department_report(db, v.id, occ.department.value)["items"] if i["name"] == name)
    assert row["max_in_one_scene"] == 40


def test_same_query_engine_composes_across_departments(db, v):
    hero = db.scalars(select(Character).where(Character.canonical_name == "RAGHAV")).one()
    r = run_query(db, v.id, QueryFilters(
        int_ext=["EXT"], times=["NIGHT"], character_ids=[hero.id],
        elements=[ElementFilter(department="vehicles", category="motorcycle"),
                  ElementFilter(department="action_effects", category="rain")]))
    assert [s["number"] for s in r["scenes"]] == ["1"]
