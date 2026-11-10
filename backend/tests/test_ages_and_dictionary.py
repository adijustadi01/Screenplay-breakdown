"""Built-in dictionary (people vs things) and age-based casting roles — all offline, no API key."""
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
    assert [r["name"] for r in roles] == ["ARJUN — CHILD (6)", "ARJUN — ADULT (28)"]
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
    assert set(by_role) == {"ARJUN — CHILD (6)", "ARJUN — ADULT (28)"}
    apps = db.scalars(select(Appearance).where(Appearance.character_id == arjun.id)).all()
    role_of = {int(db.get(Scene, a.scene_id).scene_number): a.casting_role_id for a in apps}
    assert {s for s, r in role_of.items() if r == by_role["ARJUN — CHILD (6)"].id} == {1, 2, 3}
    assert {s for s, r in role_of.items() if r == by_role["ARJUN — ADULT (28)"].id} == {5, 6, 8}
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
    assert {"ARJUN — CHILD (6)", "ARJUN — ADULT (28)", "ARJUN — UNASSIGNED ROLE"} <= names

    act_on_item(db, item.id, "undo", "AD")
    assert not db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id)).all()
    assert all(a.casting_role_id is None for a in
               db.scalars(select(Appearance).where(Appearance.character_id == arjun.id)))


def test_close_ages_stay_one_role(db, project):
    """ARJUN (45) in the sample script and no other adult age: no proposal."""
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    assert "age_roles" not in propose(db, v.id)
