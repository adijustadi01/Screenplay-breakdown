"""Stage 3 — revision tracking."""
from sqlalchemy import select

from app.models import Appearance, SceneLineage
from app.revisions.diff import revision_report
from app.revisions.service import compare, rematch, revise
from app.review.service import edit_fact

from .stage3_helpers import EXPECTED, extractors, prepared_draft, revised


def _key(entry):
    ref = entry["new"] or entry["old"]
    return ref["number"] or ("POLICE STATION" if "POLICE STATION" in (ref["heading"] or "") else f"#{ref['seq']}")


def test_scene_statuses_match_expected(db, project):
    a = prepared_draft(db, project)
    b = revised(db, project)
    rep = revision_report(db, compare(db, a.id, b.id).id)
    got = {_key(s): s for s in rep["scenes"]}
    for num, exp in EXPECTED["scenes"].items():
        assert got[num]["status"] == exp["status"], num
    assert got["6"]["heading_changed"] and got["POLICE STATION"]["method"] == "content"
    assert rep["summary"] == {"unchanged": 10, "changed": 4, "omitted": 1, "added": 1}


def test_cast_location_and_department_changes_per_scene(db, project):
    a = prepared_draft(db, project)
    b = revised(db, project)
    out = revise(db, a.id, b.id, *extractors(), workers=1)
    rep = revision_report(db, out["revision_id"])
    got = {_key(s): s for s in rep["scenes"]}
    for num, exp in EXPECTED["scenes"].items():
        for k in ("cast_added", "cast_removed"):
            for name in exp.get(k, []):
                assert name in got[num][k], (num, k, name)
    assert got["6"]["location"] == {"from": "ARJUN'S CAR", "to": "KAALI-PEELI TAXI"}
    assert "TAXI" in got["6"]["departments"]["vehicles"]["added"]
    assert "POLICE JEEP" in got["5"]["departments"]["vehicles"]["added"]
    assert "MRS. D'SOUZA" in rep["characters_dropped_from_script"]
    assert "BANK MANAGER" in rep["characters_new_to_script"]


def test_unchanged_scenes_carry_forward_reviewed_facts(db, project):
    a = prepared_draft(db, project, departments=False)
    sc1_apps = db.scalars(select(Appearance).join(Appearance.scene).where(
        Appearance.scene.has(script_version_id=a.id, scene_number="1"))).all()
    dadi = next(x for x in sc1_apps if x.name_as_written and "DADI" in x.name_as_written.upper())
    edit_fact(db, "appearances", dadi.id, {"speaking": "unknown"}, "AD", "check with director")
    b = revised(db, project)
    out = revise(db, a.id, b.id, None, None)
    assert out["carried_forward"]["scenes"] == 9          # 10 unchanged, one is OMITTED in both (nothing to copy)
    new1 = db.scalars(select(Appearance).where(Appearance.scene.has(script_version_id=b.id, scene_number="1"))).all()
    copy = next(x for x in new1 if x.character_id == dadi.character_id)
    assert copy.review_status.value == "edited" and copy.speaking.value == "unknown"   # the AD's work survives


def test_only_changed_scenes_need_extraction(db, project):
    a = prepared_draft(db, project, departments=False)
    b = revised(db, project)
    out = revise(db, a.id, b.id, None, None)
    rep = revision_report(db, out["revision_id"])
    assert rep["not_extracted"] == 5                     # 4 changed + 1 added, nothing else


def test_departments_only_compared_when_both_drafts_have_them(db, project):
    a = prepared_draft(db, project, departments=False)
    b = revised(db, project)
    out = revise(db, a.id, b.id, *extractors(), workers=1)
    rep = revision_report(db, out["revision_id"])
    assert rep["departments_compared"] == [] and "vehicles" in rep["departments_not_compared"]
    assert all(not s["departments"] for s in rep["scenes"])   # no false "added" items


def test_renumbered_scene_is_matched_by_content_and_flagged(db, project):
    a = prepared_draft(db, project, departments=False)
    b = revised(db, project)
    from app.models import Scene
    s14 = db.scalars(select(Scene).where(Scene.script_version_id == b.id, Scene.scene_number == "14")).one()
    s14.scene_number = "15"                               # pretend the revision renumbered it
    rev = compare(db, a.id, b.id)
    row = db.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id,
                                                SceneLineage.new_scene_id == s14.id)).one()
    assert row.method.value == "content" and row.renumbered and row.needs_review


def test_ad_can_rematch(db, project):
    a = prepared_draft(db, project, departments=False)
    b = revised(db, project)
    rev = compare(db, a.id, b.id)
    added = db.scalars(select(SceneLineage).where(SceneLineage.revision_id == rev.id,
                                                  SceneLineage.status == "added")).one()
    from app.models import Scene
    old7 = db.scalars(select(Scene).where(Scene.script_version_id == a.id, Scene.scene_number == "7")).one()
    row = rematch(db, added.id, old7.id, "AD")
    assert row.method.value == "manual" and row.status.value == "changed"


def test_departments_pending_until_changed_scenes_are_extracted(db, project):
    from app.extraction.heuristic_extractor import HeuristicExtractor
    a = prepared_draft(db, project)                        # old draft has departments
    b = revised(db, project)
    out = revise(db, a.id, b.id, HeuristicExtractor(), None, workers=1)   # cast only, no departments yet
    rep = revision_report(db, out["revision_id"])
    s6 = next(s for s in rep["scenes"] if s["new"] and s["new"]["number"] == "6")
    assert "vehicles" in s6["departments_not_extracted"] and not s6["departments"]   # no false "removed"
    assert rep["departments_not_extracted"] == 5


def test_new_appearances_get_casting_role_by_name(db, project):
    from app.models import CastingRole, Scene
    from app.extraction.heuristic_extractor import HeuristicExtractor
    a = prepared_draft(db, project, departments=False)     # ARJUN now has roles ARJUN and YOUNG ARJUN
    b = revised(db, project)
    revise(db, a.id, b.id, HeuristicExtractor(), None, workers=1)
    s9a = db.scalars(select(Scene).where(Scene.script_version_id == b.id, Scene.scene_number == "9A")).one()
    arjun_app = next(x for x in db.scalars(select(Appearance).where(Appearance.scene_id == s9a.id))
                     if x.name_as_written == "ARJUN")
    assert db.get(CastingRole, arjun_app.casting_role_id).name == "ARJUN"


def test_no_false_script_wide_drops_while_departments_pending(db, project):
    from app.extraction.heuristic_extractor import HeuristicExtractor
    a = prepared_draft(db, project)
    b = revised(db, project)
    out = revise(db, a.id, b.id, HeuristicExtractor(), None, workers=1)
    rep = revision_report(db, out["revision_id"])
    dropped = {n for v in rep["departments"].values() for n in v["dropped_from_script"]}
    # Only items from the OMITTED scene 7 (really gone) may be reported dropped.
    assert dropped <= {"LAMP", "FRAMED PHOTOGRAPH", "KEY"}
