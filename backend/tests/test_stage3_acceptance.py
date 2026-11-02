"""STAGE 3 ACCEPTANCE — one test per requirement in the Stage 3 brief.

Run:  pytest tests/test_stage3_acceptance.py -v
"""
from sqlalchemy import select

from app.models import Scene
from app.revisions.diff import revision_report
from app.revisions.service import revise
from app.scheduling import service as sv
from app.scheduling.reports import schedule_report
from app.scheduling.story_days import infer_story_days

from .stage3_helpers import EXPECTED, extractors, prepared_draft, revised


def _scenes(rep):
    out = {}
    for s in rep["scenes"]:
        ref = s["new"] or s["old"]
        out[ref["number"] or ("POLICE STATION" if "POLICE" in (ref["heading"] or "") else "?")] = s
    return out


def test_ingest_revised_draft_and_report_which_scenes_changed(db, project):
    a = prepared_draft(db, project)
    b = revised(db, project)
    assert b.is_current and not db.get(type(a), a.id).is_current
    rep = revision_report(db, revise(db, a.id, b.id, *extractors(), workers=1)["revision_id"])
    got = _scenes(rep)
    assert {k: got[k]["status"] for k in EXPECTED["scenes"]} == {k: v["status"] for k, v in EXPECTED["scenes"].items()}


def test_per_scene_characters_props_locations_requirements_added_removed(db, project):
    a = prepared_draft(db, project)
    b = revised(db, project)
    rep = revision_report(db, revise(db, a.id, b.id, *extractors(), workers=1)["revision_id"])
    got = _scenes(rep)
    assert "MRS. D'SOUZA" in got["2"]["cast_removed"]                                   # characters
    assert "INSPECTOR SHARMA" in got["5"]["cast_added"]
    assert got["6"]["location"] == {"from": "ARJUN'S CAR", "to": "KAALI-PEELI TAXI"}    # locations
    assert "STRETCHER" in got["3"]["departments"]["props"]["added"]                     # props
    assert {"LAMP", "FRAMED PHOTOGRAPH"} <= set(got["7"]["departments"]["props"]["removed"])
    assert "TAXI" in got["6"]["departments"]["vehicles"]["added"]                       # other requirements
    assert "ARJUN'S CAR" in got["6"]["departments"]["vehicles"]["removed"]


def test_character_vs_casting_requirement_kept_separate_in_revisions(db, project):
    a = prepared_draft(db, project)
    b = revised(db, project)
    rep = revision_report(db, revise(db, a.id, b.id, *extractors(), workers=1)["revision_id"])
    got = _scenes(rep)
    assert "ARJUN" in got["9A"]["cast_added"] and "ARJUN" in got["9A"]["casting_roles_added"]
    assert set(rep) >= {"characters_new_to_script", "casting_roles_new"}


def test_group_scenes_into_shooting_days(db, project):
    a = prepared_draft(db, project, departments=False)
    sch = sv.create_schedule(db, a.id, "Main")
    d1, d2 = sv.add_day(db, sch.id), sv.add_day(db, sch.id)
    s = {x.scene_number: x for x in db.scalars(select(Scene).where(Scene.script_version_id == a.id))}
    sv.assign(db, d1.id, s["3"].id)
    sv.assign(db, d1.id, s["4"].id)
    sv.assign(db, d2.id, s["1"].id)
    r = schedule_report(db, sch.id)
    assert [[e["scene"] for e in d["scenes"]] for d in r["oneliner"]] == [["3", "4"], ["1"]]


def test_dood_from_scheduled_days_not_story_days(db, project):
    a = prepared_draft(db, project, departments=False)
    infer_story_days(db, a.id)
    sch = sv.create_schedule(db, a.id, "Main")
    s = {x.scene_number: x for x in db.scalars(select(Scene).where(Scene.script_version_id == a.id))}
    d1 = sv.add_day(db, sch.id)
    for n in ("3", "4", "12", "13", "14"):        # night scenes from story nights 1 and 4 — one shooting day
        sv.assign(db, d1.id, s[n].id)
    sv.assign(db, sv.add_day(db, sch.id).id, s["1"].id)
    r = schedule_report(db, sch.id)
    assert len(r["dood"]["days"]) == 2                                     # columns = shooting days
    meera = next(c for c in r["dood"]["cast"] if c["requirement"] == "MEERA")
    assert meera["codes"] == {1: "SW", 2: "WF"} and meera["work_days"] == 2
    sv_ = r["story_vs_shooting"]
    assert sv_["story_nights"] >= 2 and sv_["shooting_nights"] == 1         # many story nights, one shoot night
    assert "shooting days only" in sv_["explanation"]


def test_dood_rows_are_casting_requirements(db, project):
    a = prepared_draft(db, project, departments=False)
    sch = sv.create_schedule(db, a.id, "Main")
    sv.autofill(db, sch.id)
    cast = schedule_report(db, sch.id)["dood"]["cast"]
    arjun_rows = [c for c in cast if c["character"] == "ARJUN"]
    assert {c["requirement"] for c in arjun_rows} == {"ARJUN", "YOUNG ARJUN"}   # one character, two hires
