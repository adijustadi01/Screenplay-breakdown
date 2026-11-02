"""Schema integrity tests: the data model must express the Stage 1 rules."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import configure_mappers

from app.db.session import make_engine, make_session_factory
from app.models import (
    Appearance, Base, CastingRole, Character, Location, Page, Project, Scene,
    SceneLocation, ScriptElement, ScriptVersion,
)
from app.models.enums import (
    APPEARANCE_PRESENCES, CastingRoleKind, CharacterKind, ElementType, ExtractorKind,
    IntExt, LocationKind, PageTextSource, Presence, SourceFormat, Speaking, TimeOfDay,
)


@pytest.fixture()
def db():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as s:
        yield s


def _version(db):
    p = Project(title="Test")
    v = ScriptVersion(project=p, source_filename="x.pdf", source_format=SourceFormat.PDF,
                      source_sha256="0" * 64)
    db.add(v)
    db.flush()
    return p, v


def _scene(db, v, seq, tod=TimeOfDay.DAY, ie=IntExt.INT, number=None):
    s = Scene(script_version_id=v.id, seq=seq, scene_number=number or str(seq), int_ext=ie,
              time_of_day=tod, extracted_by=ExtractorKind.PARSER)
    db.add(s)
    db.flush()
    return s


def test_mappers_configure():
    configure_mappers()


def test_scene_number_preserved_verbatim_and_continuous_kept(db):
    _, v = _version(db)
    s1 = _scene(db, v, 1, TimeOfDay.NIGHT, number="23A")
    s2 = _scene(db, v, 2, TimeOfDay.CONTINUOUS, number="24")
    s2.effective_time_of_day = TimeOfDay.NIGHT
    s2.effective_time_source_scene_id = s1.id
    s2.effective_time_needs_review = True
    db.commit()
    got = db.get(Scene, s2.id)
    assert db.get(Scene, s1.id).scene_number == "23A"
    assert got.time_of_day is TimeOfDay.CONTINUOUS       # as written, not collapsed
    assert got.effective_time_of_day is TimeOfDay.NIGHT   # inherited, separately


def test_one_character_two_casting_roles(db):
    p, v = _version(db)
    s = _scene(db, v, 1)
    arjun = Character(project_id=p.id, canonical_name="ARJUN", extracted_by=ExtractorKind.LLM)
    db.add(arjun)
    db.flush()
    young = CastingRole(project_id=p.id, character_id=arjun.id, name="Young Arjun",
                        kind=CastingRoleKind.ACTOR, extracted_by=ExtractorKind.RECONCILIATION)
    adult = CastingRole(project_id=p.id, character_id=arjun.id, name="Adult Arjun",
                        kind=CastingRoleKind.ACTOR, extracted_by=ExtractorKind.RECONCILIATION)
    db.add_all([young, adult])
    db.flush()
    # Mirror scene: both versions of the same character in one scene.
    db.add_all([
        Appearance(scene_id=s.id, character_id=arjun.id, casting_role_id=young.id,
                   presence=Presence.PRESENT, speaking=Speaking.SILENT, extracted_by=ExtractorKind.LLM),
        Appearance(scene_id=s.id, character_id=arjun.id, casting_role_id=adult.id,
                   presence=Presence.PRESENT, speaking=Speaking.SPEAKING, dialogue_block_count=2,
                   extracted_by=ExtractorKind.LLM),
    ])
    db.commit()
    assert len(db.get(Character, arjun.id).casting_roles) == 2
    # Character-level count is DISTINCT character, so still one character in one scene.
    n = db.scalar(select(func.count(func.distinct(Appearance.character_id)))
                  .where(Appearance.scene_id == s.id))
    assert n == 1


def test_mentioned_is_stored_but_not_an_appearance(db):
    p, v = _version(db)
    s = _scene(db, v, 1)
    hero = Character(project_id=p.id, canonical_name="HERO", extracted_by=ExtractorKind.LLM)
    villain = Character(project_id=p.id, canonical_name="VILLAIN", extracted_by=ExtractorKind.LLM)
    guard = Character(project_id=p.id, canonical_name="GUARD", extracted_by=ExtractorKind.LLM)
    db.add_all([hero, villain, guard])
    db.flush()
    db.add_all([
        Appearance(scene_id=s.id, character_id=hero.id, presence=Presence.PRESENT,
                   speaking=Speaking.SPEAKING, extracted_by=ExtractorKind.LLM),
        Appearance(scene_id=s.id, character_id=villain.id, presence=Presence.MENTIONED,
                   speaking=Speaking.SILENT, extracted_by=ExtractorKind.LLM,
                   source_excerpt="HERO: Vikram will come for us."),
        Appearance(scene_id=s.id, character_id=guard.id, presence=Presence.PRESENT,
                   speaking=Speaking.SILENT, extracted_by=ExtractorKind.LLM),
    ])
    db.commit()
    present = db.scalars(select(Appearance.character_id)
                         .where(Appearance.presence.in_(APPEARANCE_PRESENCES))).all()
    assert set(present) == {hero.id, guard.id}  # silent guard counts, mentioned villain doesn't


def test_location_hierarchy_and_scene_link(db):
    p, v = _version(db)
    s = _scene(db, v, 1)
    house = Location(project_id=p.id, name="ARJUN'S HOUSE", kind=LocationKind.STORY_LOCATION,
                     extracted_by=ExtractorKind.PARSER)
    db.add(house)
    db.flush()
    kitchen = Location(project_id=p.id, parent_id=house.id, name="KITCHEN", kind=LocationKind.SET,
                       extracted_by=ExtractorKind.PARSER)
    db.add(kitchen)
    db.flush()
    db.add(SceneLocation(scene_id=s.id, location_id=kitchen.id, extracted_by=ExtractorKind.PARSER))
    db.commit()
    assert db.get(Location, kitchen.id).parent.name == "ARJUN'S HOUSE"
    assert [c.name for c in db.get(Location, house.id).children] == ["KITCHEN"]
    assert db.get(Location, house.id).venue_id is None


def test_group_character_unknown_headcount(db):
    p, _ = _version(db)
    crowd = Character(project_id=p.id, canonical_name="CROWD", kind=CharacterKind.GROUP,
                      quantity_raw="a huge crowd", extracted_by=ExtractorKind.LLM,
                      confidence=0.4, needs_review=True, review_reason="unknown_headcount")
    db.add(crowd)
    db.commit()
    got = db.get(Character, crowd.id)
    assert got.quantity_min is None and got.quantity_max is None and got.needs_review


def test_confidence_must_be_in_range(db):
    p, _ = _version(db)
    db.add(Character(project_id=p.id, canonical_name="X", extracted_by=ExtractorKind.LLM,
                     confidence=1.5))
    with pytest.raises(IntegrityError):
        db.commit()


def test_invalid_enum_rejected(db):
    p, v = _version(db)
    db.add(Scene(script_version_id=v.id, seq=1, int_ext=IntExt.INT, time_of_day="NIGHTISH",
                 extracted_by=ExtractorKind.PARSER))
    with pytest.raises((StatementError, LookupError)):
        db.commit()


def test_element_links_page_and_scene(db):
    _, v = _version(db)
    page = Page(script_version_id=v.id, page_index=1, page_label="1",
                text_source=PageTextSource.TEXT_LAYER)
    db.add(page)
    db.flush()
    s = _scene(db, v, 1)
    el = ScriptElement(script_version_id=v.id, page_id=page.id, scene_id=s.id, seq=1,
                       element_type=ElementType.SCENE_HEADING, text="INT. HOUSE - DAY",
                       raw_text="1   INT. HOUSE - DAY   1")
    db.add(el)
    db.flush()
    s.source_element_start_id = el.id
    db.commit()
    assert db.get(Scene, s.id).source_element_start_id == el.id
