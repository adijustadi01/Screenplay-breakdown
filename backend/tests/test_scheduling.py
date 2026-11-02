"""Stage 3 — story days vs shooting days, schedule, DOOD."""
from sqlalchemy import select

from app.models import Scene, SceneStoryDay
from app.revisions.service import compare
from app.scheduling import service as sv
from app.scheduling.reports import _dood_line, materialize_roles, schedule_report
from app.scheduling.story_days import infer_story_days

from .stage3_helpers import prepared_draft, revised


def _scene(db, v, num):
    return db.scalars(select(Scene).where(Scene.script_version_id == v.id, Scene.scene_number == num)).one()


def test_story_days_are_inferred_and_flagged(db, project):
    v = prepared_draft(db, project, departments=False)
    out = infer_story_days(db, v.id)
    assert out["story_days"] == 4
    labels = {s.scene_number: db.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == s.id)).first()
              for s in db.scalars(select(Scene).where(Scene.script_version_id == v.id)) if not s.is_omitted}
    assert labels["1"].label == "D1" and labels["2"].label == "D1"          # CONTINUOUS stays on the day
    assert labels["3"].label == "N1" and labels["8"].is_flashback and labels["8A"].is_flashback
    assert all(sd.needs_review and sd.extracted_by.value == "rules" for sd in labels.values())


def test_confirmed_story_day_is_kept_on_reinfer(db, project):
    from app.review.service import edit_fact
    v = prepared_draft(db, project, departments=False)
    infer_story_days(db, v.id)
    sd = db.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == _scene(db, v, "5").id)).one()
    edit_fact(db, "scene_story_days", sd.id, {"story_day": 7, "label": "N7"}, "AD")
    infer_story_days(db, v.id)
    again = db.scalars(select(SceneStoryDay).where(SceneStoryDay.scene_id == _scene(db, v, "5").id)).one()
    assert again.label == "N7" and again.review_status.value == "edited"


def test_shooting_days_are_not_story_days(db, project):
    v = prepared_draft(db, project, departments=False)
    infer_story_days(db, v.id)
    sch = sv.create_schedule(db, v.id, "Main")
    d1 = sv.add_day(db, sch.id)
    for num in ("3", "4", "6", "7", "12", "14"):                 # six night scenes, ONE shooting night
        sv.assign(db, d1.id, _scene(db, v, num).id)
    r = schedule_report(db, sch.id)
    sv_ = r["story_vs_shooting"]
    assert sv_["night_scenes"] == 7 and sv_["story_nights"] == 3 and sv_["shooting_nights"] == 1
    assert sv_["shooting_days"] == 1 and sv_["story_days"] == 4
    assert r["oneliner"][0]["story_days"] == ["N1", "N2", "N4"]  # shown alongside, never merged


def test_dood_codes():
    assert [_dood_line([1], 3, 10)["codes"][n] for n in (1, 2, 3)] == ["SWF", "", ""]
    line = _dood_line([1, 3, 4], 5, 10)
    assert [line["codes"][n] for n in range(1, 6)] == ["SW", "H", "W", "WF", ""]
    assert line["work_days"] == 3 and line["hold_days"] == 1
    gap = _dood_line([1, 15], 15, 10)
    assert gap["codes"][1] == "WD" and gap["codes"][15] == "PW" and gap["hold_days"] == 0 and gap["drop_days"] == 13


def test_dood_rows_are_casting_requirements_not_characters(db, project):
    v = prepared_draft(db, project, departments=False)       # reconciliation created YOUNG ARJUN / ARJUN roles
    sch = sv.create_schedule(db, v.id, "Main")
    sv.autofill(db, sch.id, max_eighths=24)
    r = schedule_report(db, sch.id)
    reqs = {c["requirement"]: c for c in r["dood"]["cast"]}
    assert reqs["ARJUN"]["character"] == "ARJUN" and reqs["YOUNG ARJUN"]["character"] == "ARJUN"   # 1 char, 2 rows
    assert reqs["ARJUN"]["implicit"] is False and reqs["MEERA"]["implicit"] is True
    assert [x["requirement"] for x in r["dood"]["voice"]] == ["VOICE FOR MEERA"]               # V.O. is not on set
    assert "GOONS" in [g["requirement"] for g in r["dood"]["background_groups"]]                # group != cast
    assert r["dood"]["cast"][0]["cast_id"] == 1
    made = materialize_roles(db, v.id, "AD")
    assert "MEERA" in made
    assert next(c for c in schedule_report(db, sch.id)["dood"]["cast"] if c["requirement"] == "MEERA")["implicit"] is False


def test_unassigned_role_is_flagged(db, project):
    from app.models import CastingRole, Character
    from app.models.enums import CastingRoleKind, ExtractorKind
    v = prepared_draft(db, project, departments=False)
    meera = db.scalars(select(Character).where(Character.canonical_name == "MEERA")).one()
    for n in ("MEERA (30s)", "MEERA (60s)"):
        db.add(CastingRole(project_id=project.id, character_id=meera.id, name=n, kind=CastingRoleKind.ACTOR,
                           extracted_by=ExtractorKind.HUMAN))
    db.flush()
    sch = sv.create_schedule(db, v.id, "Main")
    sv.assign(db, sv.add_day(db, sch.id).id, _scene(db, v, "1").id)
    r = schedule_report(db, sch.id)
    assert any("MEERA has 2 casting roles" in w for w in r["warnings"])


def test_autofill_is_a_suggestion_grouped_by_location_and_light(db, project):
    v = prepared_draft(db, project, departments=False)
    sch = sv.create_schedule(db, v.id, "Main")
    sv.autofill(db, sch.id, max_eighths=24)
    r = schedule_report(db, sch.id)
    assert not r["unscheduled"] and all(d["label"].startswith("Suggested") for d in r["oneliner"])
    assert not any("mixes day and night" in w for w in r["warnings"])


def test_schedule_follows_revision(db, project):
    a = prepared_draft(db, project, departments=False)
    sch = sv.create_schedule(db, a.id, "Main")
    sv.autofill(db, sch.id, max_eighths=24)
    b = revised(db, project)
    rev = compare(db, a.id, b.id)
    out = sv.migrate_schedule(db, sch.id, rev.id)
    assert [d["scene"] for d in out["dropped"]] == ["7"] and len(out["new_scenes_unscheduled"]) == 1
    new = schedule_report(db, out["schedule_id"])
    assert [u["scene"] for u in new["unscheduled"]] == ["9A"]
