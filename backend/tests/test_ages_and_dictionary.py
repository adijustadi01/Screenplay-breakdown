"""Built-in dictionary (people vs things) and age-based casting roles — all offline, no API key."""
import pytest
from sqlalchemy import select

from app.extraction.heuristic_extractor import HeuristicExtractor, intro_names
from app.extraction.schema import CharacterOut, SceneExtraction
from app.extraction.service import run_extraction
from app.importers.service import import_script
from app.lexicon import is_people, kind_of
from app.models import Appearance, CastingRole, Character, ReviewItem, Scene
from app.reconciliation.ages import _evidence_from_note, _evidence_in
from app.reconciliation.service import propose
from app.review.service import act_on_item
from app.scheduling.reports import schedule_report
from app.scheduling.service import add_day, assign, create_schedule

from .conftest import SAMPLES


# ------------------------------------------------------------------ dictionary
def test_dictionary_tells_people_from_things():
    for w in ("CONSTABLE", "WARD BOY", "TAXI DRIVER", "GOONS", "POLICEMEN", "FAKIR", "LINEMAN", "WIDOW",
              "PANDIT", "DHOBI", "AUNTY"):
        assert kind_of(w) == "person", w
    for w in ("CROWD", "MOB", "GANG"):
        assert kind_of(w) == "group", w
    for w in ("MIRROR", "KNIFE", "GUNSHOT", "JEEPS", "PHOTOGRAPH", "THUNDER"):
        assert kind_of(w) == "thing", w
    assert kind_of("GOLDEN RETRIEVER") == "animal"
    assert kind_of("RAGHAV") is None            # a name, not an English word
    assert is_people("A WARD BOY") and not is_people("A MIRROR")


def test_people_found_without_a_hand_written_list():
    names = intro_names(["A FAKIR sings at the gate. A LINEMAN climbs the pole. A MIRROR cracks.",
                         "The WIDOW lights a lamp. The GUNSHOT echoes. A TRUCK passes."])
    assert names == {"FAKIR", "LINEMAN", "WIDOW"}


def test_counted_things_are_not_groups_of_people():
    from app.extraction.heuristic_extractor import _GROUP_RE
    v_text = "TWO JEEPS rumble past. THREE VILLAGERS laugh. FOUR GUNSHOTS ring out."
    found = [m.group(2) for m in _GROUP_RE.finditer(v_text) if is_people(m.group(2))]
    assert found == ["VILLAGERS"]


def test_object_reported_as_character_is_flagged(db, project):
    """Even the LLM path: a 'character' that never speaks and is a thing goes to the checklist."""

    class Fake:
        name = model = prompt_version = "fake"

        def set_script_context(self, *a, **k):
            pass

        def extract(self, text, ctx):
            return SceneExtraction(characters=[CharacterOut(
                name_as_written="MIRROR", canonical_name="MIRROR", presence="present", speaking="silent",
                is_group=False, quantity_text=None, quantity_min=None, quantity_max=None, age_or_version=None,
                evidence="A MIRROR", confidence=0.9, uncertainty_note=None)],
                location=None, synopsis="x", int_ext_suggestion="UNKNOWN", notes=None)

    v = import_script(db, project.id, SAMPLES / "river_years.fountain")
    run_extraction(db, v.id, Fake(), workers=1)
    mirror = db.scalars(select(Character).where(Character.canonical_name == "MIRROR")).one()
    apps = db.scalars(select(Appearance).where(Appearance.character_id == mirror.id)).all()
    assert apps and all(a.needs_review and "looks_like_object" in a.review_reason for a in apps)


# ------------------------------------------------------------------ age evidence
def test_age_evidence_forms():
    n = ["ARJUN"]
    assert _evidence_in("ARJUN (6), barefoot, runs.", n).note == "6"
    assert _evidence_in("ARJUN (early 30s) waits.", n).note == "early 30s"
    assert _evidence_in("ARJUN (age 6) waits.", n).low == 6
    assert _evidence_in("Arjun, now 28, signs the form.", n).low == 28
    assert _evidence_in("LITTLE ARJUN hides.", n).note == "little"
    assert _evidence_in("ARJUN (YOUNG)", n).low is None
    assert _evidence_in("Arjun, Meera and Vikram sit.", n) is None
    assert _evidence_from_note("10").low == 10 and _evidence_from_note("young").note == "young"


# ------------------------------------------------------------------ age-based casting roles
def _river(db, project):
    v = import_script(db, project.id, SAMPLES / "river_years.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    return v


def _scene_numbers(db, app_ids):
    return sorted(int(db.get(Scene, db.get(Appearance, i).scene_id).scene_number) for i in app_ids)


def test_props_stay_out_of_the_cast(db, project):
    _river(db, project)
    cast = {c.canonical_name for c in db.scalars(select(Character))}
    assert {"ARJUN", "MAA", "WARD BOY", "PEON", "VILLAGERS"} <= cast
    assert not cast & {"MIRROR", "KNIFE", "THUNDER", "JEEPS", "TWO JEEPS"}


def test_same_character_at_6_and_28_gets_two_casting_roles(db, project):
    v = _river(db, project)
    made = propose(db, v.id)
    assert made.get("age_roles") == 1
    item = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "age_roles")).one()
    roles = item.proposal["roles"]
    assert [r["name"] for r in roles] == ["ARJUN — CHILD (6)", "ARJUN — YOUNG (28)"]
    assert _scene_numbers(db, roles[0]["appearance_ids"]) == [1, 2, 3]
    assert _scene_numbers(db, roles[1]["appearance_ids"]) == [5, 6, 8]
    # The flashback after the time jump has no stated age: left for the AD, never guessed.
    assert "Age not clear: scene(s) 7 (flashback)" in item.message
    # MAA is 35 throughout (and only a voice later): no split proposed for her.
    assert "MAA" not in item.message
    assert propose(db, v.id) == {}          # no duplicates on a re-run

    # Nothing changed until accepted.
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    assert not db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id)).all()

    act_on_item(db, item.id, "accept", "AD")
    by_role = {r.name: r for r in db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id))}
    assert set(by_role) == {"ARJUN — CHILD (6)", "ARJUN — YOUNG (28)"}
    apps = db.scalars(select(Appearance).where(Appearance.character_id == arjun.id)).all()
    role_of = {int(db.get(Scene, a.scene_id).scene_number): a.casting_role_id for a in apps}
    assert {s for s, r in role_of.items() if r == by_role["ARJUN — CHILD (6)"].id} == {1, 2, 3}
    assert {s for s, r in role_of.items() if r == by_role["ARJUN — YOUNG (28)"].id} == {5, 6, 8}
    assert role_of[7] is None
    # Still ONE character in every report.
    assert db.scalars(select(Character).where(Character.canonical_name.like("ARJUN%"))).all() == [arjun]

    # The DOOD lists two actors (and flags the flashback scene as unassigned).
    sched = create_schedule(db, v.id, "Main")
    scenes = {s.scene_number: s for s in db.scalars(select(Scene).where(Scene.script_version_id == v.id))}
    d1, d2 = add_day(db, sched.id), add_day(db, sched.id)
    for n in ("1", "2", "3"):
        assign(db, d1.id, scenes[n].id)
    for n in ("5", "7", "8"):
        assign(db, d2.id, scenes[n].id)
    names = {r["requirement"] for r in schedule_report(db, sched.id)["dood"]["cast"]}
    assert {"ARJUN — CHILD (6)", "ARJUN — YOUNG (28)", "ARJUN — UNASSIGNED ROLE"} <= names

    act_on_item(db, item.id, "undo", "AD")
    assert not db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id)).all()
    assert all(a.casting_role_id is None for a in
               db.scalars(select(Appearance).where(Appearance.character_id == arjun.id)))


def test_close_ages_stay_one_role(db, project):
    """ARJUN (45) in the sample script and no other adult age: no proposal."""
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    assert "age_roles" not in propose(db, v.id)


# ------------------------------------------------------------------ three ages, director's choice


def _three(db, project, tmp_path):
    v = import_script(db, project.id, SAMPLES / "three_ages.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    propose(db, v.id)
    return v


def test_10_28_50_suggests_three_actors_with_plain_age_names(db, project, tmp_path):
    _three(db, project, tmp_path)
    items = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "age_roles")).all()
    assert len(items) == 1                       # RAVI at 30 and 40s: one actor, no suggestion
    roles = items[0].proposal["roles"]
    assert [r["name"] for r in roles] == ["ARJUN — CHILD (10)", "ARJUN — YOUNG (28)", "ARJUN — MIDDLE-AGED (50)"]
    assert [r["scene_count"] for r in roles] == [2, 2, 2]
    assert "the director decides" in items[0].message


def test_director_chooses_one_actor_for_28_and_50(db, project, tmp_path):
    v = _three(db, project, tmp_path)
    item = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "age_roles")).one()
    act_on_item(db, item.id, "accept", "AD", changes={"actors": ["A", "B", "B"]})
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    names = {r.name for r in db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id))}
    assert names == {"ARJUN — CHILD (10)", "ARJUN — YOUNG + MIDDLE-AGED (28, 50)"}

    # Cast report: ARJUN once with all 6 scenes, then each actor's own count.
    from app.reports.facts import load_facts
    from app.reports.service import character_report
    row = next(c for c in character_report(load_facts(db, v.id))["characters"] if c["name"] == "ARJUN")
    assert row["appearances"]["count"] == 6
    assert {r["name"]: r["appearances"]["count"] for r in row["by_role"]} == {
        "ARJUN — CHILD (10)": 2, "ARJUN — YOUNG + MIDDLE-AGED (28, 50)": 4}

    # Excel/PDF section "Characters by age / actor".
    from app.export.sections import Context, build
    t = build(Context(db, v.id), "cast_by_role")
    assert [r[1] for r in t.rows] == ["ALL AGES (total)", "ARJUN — CHILD (10)", "ARJUN — YOUNG + MIDDLE-AGED (28, 50)"]
    assert [r[2] for r in t.rows] == [6, 2, 4]

    # The director changes their mind: undo, then one actor for every age.
    act_on_item(db, item.id, "undo", "AD")
    act_on_item(db, item.id, "accept", "AD", changes={"actors": ["A", "A", "A"]})
    names = {r.name for r in db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id))}
    assert names == {"ARJUN"}


def test_played_by_can_be_changed_per_scene_and_survives_a_reread(db, project, tmp_path):
    from app.review.service import ReviewError, edit_fact
    v = _three(db, project, tmp_path)
    item = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "age_roles")).one()
    act_on_item(db, item.id, "accept", "AD")
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    roles = {r.name: r.id for r in db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id))}
    sc4 = db.scalars(select(Scene).where(Scene.script_version_id == v.id, Scene.scene_number == "4")).one()
    a4 = db.scalars(select(Appearance).where(Appearance.scene_id == sc4.id,
                                             Appearance.character_id == arjun.id)).one()
    edit_fact(db, "appearances", a4.id, {"casting_role_id": roles["ARJUN — MIDDLE-AGED (50)"]}, "AD")
    assert a4.casting_role_id == roles["ARJUN — MIDDLE-AGED (50)"]
    from app.models.enums import CastingRoleKind, ExtractorKind
    ravi_role = CastingRole(project_id=project.id, name="RAVI", kind=CastingRoleKind.ACTOR, extracted_by=ExtractorKind.HUMAN,
                            character_id=db.scalars(select(Character).where(Character.canonical_name == "RAVI")).one().id)
    db.add(ravi_role)
    db.flush()
    with pytest.raises(ReviewError):
        edit_fact(db, "appearances", a4.id, {"casting_role_id": ravi_role.id}, "AD")
    assert a4.casting_role_id == roles["ARJUN — MIDDLE-AGED (50)"]      # unchanged

    # Re-reading the script keeps every scene's role.
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    apps = db.scalars(select(Appearance).where(Appearance.character_id == arjun.id)).all()
    assert all(a.casting_role_id is not None for a in apps)
