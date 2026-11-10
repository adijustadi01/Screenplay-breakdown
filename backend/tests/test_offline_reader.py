"""The offline reader (no API key): grammar tagger, dictionary categories, name finding and
department finds, checked on a realistic script the word lists were never tuned to
(samples/mitti_ke_sher.fountain)."""
import pytest
from sqlalchemy import select

from app.departments import registry
from app.departments.heuristic import DepartmentHeuristicExtractor
from app.departments.service import run_department_extraction
from app.extraction.cues import split_cue
from app.extraction.heuristic_extractor import HeuristicExtractor, _mention_kind
from app.extraction.names import discover_names
from app.extraction.service import run_extraction
from app.importers.service import import_script
from app.lexicon import entry, is_english
from app.lexicon.grammar import noun_groups, read
from app.models import Appearance, BreakdownItem, BreakdownOccurrence, Character, ReviewItem, Scene
from app.reconciliation.service import propose

from .conftest import SAMPLES


# ------------------------------------------------------------------ grammar
def _tags(sentence, names=frozenset()):
    return {t.text: t.tag for t in read(sentence, names)}


def test_same_word_as_thing_or_action():
    assert _tags("The phone rings.")["rings"] == "VBZ"
    assert _tags("She takes off her rings.")["rings"] == "NNS"
    assert _tags("A rooster crows somewhere.")["crows"] == "VBZ"
    assert _tags("The dog barks at the watch seller.")["barks"] == "VBZ"
    t = _tags("Savitri ducks, grabs his leg and throws him.", frozenset({"SAVITRI"}))
    assert t["ducks"] == "VBZ" and t["Savitri"] == "NNP"
    assert _tags("He sells ducks, hens and goats.")["ducks"] == "NNS"
    assert _tags("Hundreds of spectators crowd around a mud pit.")["crowd"] == "VBP"
    assert _tags("A crowd of villagers cheers from the rooftops.")["cheers"] == "VBZ"


def test_capitals_and_names():
    t = _tags("A MIRROR hangs on the wall. RAGHAV fires.")
    assert t["MIRROR"] == "NN" and t["RAGHAV"] == "NNP"
    assert _tags("Children play in the dust.")["Children"] == "NNS"
    # A character called ROSE is not a flower.
    assert _tags("Rose picks a rose.", frozenset({"ROSE"}))["Rose"] == "NNP"


def test_noun_groups_head_number_and_owner():
    groups = {g.head.lemma: g for g in noun_groups(read("Two police jeeps stop near Geeta's gold medal and a dozen "
                                                        "neighbours.", frozenset({"GEETA"})))}
    assert groups["jeep"].number == "two" and [m.low for m in groups["jeep"].modifiers] == ["police"]
    assert groups["medal"].determiner == "geeta's"
    assert groups["neighbour"].number == "dozen" and groups["neighbour"].head.plural


# ------------------------------------------------------------------ dictionary
@pytest.mark.parametrize("word,tag", [
    ("tonga", "veh:animal_drawn"), ("scooter", "veh:motorcycle"), ("tractor", "veh:truck"), ("bus", "veh:bus"),
    ("clippers", "prop:other"), ("medals", "prop:other"), ("lathi", "prop:weapon"), ("syringe", "prop:medical"),
    ("singlet", "cos:sportswear"), ("turban", "cos:accessory"), ("kurta", "cos:traditional"),
    ("rooster", "animal:bird"), ("buffalo", "animal:buffalo"), ("dhol", "inst"), ("eyes", "body"),
    ("tumbler", "prop:other"), ("cricket", ""), ("pocket", ""), ("tank", ""),
])
def test_dictionary_categories(word, tag):
    assert entry(word)[1] == tag


def test_dictionary_knows_plurals_and_grammar_words():
    assert entry("knives")[2] == "knife" and entry("clippers")[2] == ""       # one pair of clippers
    assert entry("tumbler")[0] == "O"                                         # a glass, not an acrobat
    assert is_english("She") and is_english("towards") and not is_english("Savitri")


# ------------------------------------------------------------------ names
def test_shared_cue_names_every_speaker():
    assert split_cue("SAVITRI / KAMLA") == ["SAVITRI", "KAMLA"]
    assert split_cue("RAJ & SIMRAN") == ["RAJ", "SIMRAN"]
    assert split_cue("MRS. D'SOUZA") == ["MRS. D'SOUZA"]


def test_names_found_in_action_lines():
    actions = ["His daughters SAVITRI and KAMLA jog around the pit. Savitri carries a brick.",
               "Dadi sits on a charpai. She says nothing.", "Masterji hurries over, cane in hand.",
               "The REFEREE raises his hand. A MIRROR hangs there. THE GUNSHOT echoes.",
               "They drive to Lahore. In Lahore it rains.", "He picks up the gulal. The gulal flies.",
               'A poster: "DISTRICT DANGAL - PRIZE 500 RUPEES".', "SUPER: EIGHT YEARS LATER"]
    found = discover_names(actions, known={"KAMLA"}, location_names=["SONPUR VILLAGE - AKHADA"])
    assert found == {"SAVITRI", "DADI", "MASTERJI", "REFEREE"}


def test_in_the_scene_or_only_referred_to():
    text = "On the wall hangs a framed photograph of Baldev. Kamla's eyes fill with tears. They reach Arjun's house."
    kind = lambda name: _mention_kind(text, text.index(name), text.index(name) + len(name))  # noqa: E731
    assert kind("Baldev") == "depicted" and kind("Kamla") == "there" and kind("Arjun") == "owner"


# ------------------------------------------------------------------ the realistic sample
@pytest.fixture()
def mitti(db, project):
    v = import_script(db, project.id, SAMPLES / "mitti_ke_sher.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    run_department_extraction(db, v.id, DepartmentHeuristicExtractor(registry.get()), workers=1)
    return v


def _scenes_of(db, name):
    c = db.scalars(select(Character).where(Character.canonical_name == name)).one()
    return sorted(int(db.get(Scene, a.scene_id).scene_number) for a in db.scalars(
        select(Appearance).where(Appearance.character_id == c.id)) if a.presence.value in ("present", "off_screen"))


def _items(db, dept):
    out = {}
    for o in db.scalars(select(BreakdownOccurrence).join(BreakdownItem).where(BreakdownItem.department == dept)):
        out.setdefault(db.get(BreakdownItem, o.item_id).name, []).append(o)
    return out


def test_cast_of_a_realistic_script(db, mitti):
    cast = {c.canonical_name for c in db.scalars(select(Character))}
    assert {"SAVITRI", "KAMLA", "BALDEV", "SHANTI", "DADI", "MASTERJI", "PAPPU", "RITU", "COACH MEHRA", "REFEREE",
            "DHABA OWNER", "RUSSIAN WRESTLER"} <= cast
    assert not cast & {"SAVITRI / KAMLA", "SHE", "SONPUR", "AKHADA", "DANGAL", "MEHRA", "HANUMAN"}
    # The lead never has a cue to herself in scenes 1-4, yet is found in every scene she is in.
    assert _scenes_of(db, "SAVITRI") == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert _scenes_of(db, "KAMLA") == [1, 2, 3, 6, 7, 10]
    assert _scenes_of(db, "DADI") == [2, 9] and _scenes_of(db, "MASTERJI") == [3]
    assert _scenes_of(db, "COACH MEHRA") == [7, 10]          # "Mehra opens a file" counts for COACH MEHRA


def test_props_costumes_vehicles_animals_from_the_dictionary(db, mitti):
    props = _items(db, "props")
    assert {"HAIR CLIPPERS", "STEEL TUMBLER", "WHISTLE", "MEDAL", "SILVER MACE", "BARBELL", "TRANSISTOR RADIO",
            "STRETCHER", "INCENSE STICK", "LANTERN", "TIN TRUNK", "MOBILE PHONE", "TELEVISION"} <= set(props)
    assert "RUPEE" not in props and "CLIPPERS" not in {n for n in props if len(props[n]) and
                                                         props[n][0].scene_id == props["HAIR CLIPPERS"][0].scene_id}
    costumes = _items(db, "costumes")
    assert {"BALDEV — LANGOT", "SAVITRI — SINGLET", "SAVITRI — TRACKSUIT", "COACH MEHRA — BLAZER",
            "DHABA OWNER — VEST", "SAVITRI — JERSEY"} <= set(costumes)
    vehicles = _items(db, "vehicles")
    assert set(vehicles) == {"TRUCK", "SCOOTER", "AMBASSADOR CAR", "TRACTOR"}
    assert vehicles["TRUCK"][0].quantity_max == 3            # "Three trucks", not "A truck stop"
    animals = _items(db, "animals")
    assert set(animals) == {"ROOSTER", "BUFFALO", "HEN", "DOG", "GOAT"} and "CROWS" not in animals
    assert animals["HEN"][0].quantity_max == 2


def test_background_people_and_headcounts(db, mitti):
    bg = _items(db, "background")
    assert bg["NEIGHBOURS"][0].quantity_max == 12 and bg["PARAMEDICS"][0].quantity_max == 2
    assert bg["SPECTATORS"][0].quantity_max is None           # "hundreds of" is not a number
    assert {"TEACHER", "DRIVERS", "SECURITY GUARD", "DOCTOR", "DHOL PLAYERS", "VILLAGE BOYS"} <= set(bg)
    # Not people to hire: a description of Baldev, a glass, a row of trucks.
    assert not set(bg) & {"WRESTLER", "STEEL TUMBLER", "ROW", "BALD MAN"}


def test_action_and_makeup_read_the_sentence(db, mitti):
    action = _items(db, "action_effects")
    assert "FIGHT" in action and "GUNFIRE" not in action      # "shoots for the legs" is wrestling
    falls = {int(db.get(Scene, o.scene_id).scene_number) for o in action["FALL"]}
    assert 3 in falls and 2 not in falls and 10 not in falls   # Pappu falls; a braid and confetti do not
    makeup = _items(db, "makeup_hair")
    assert {"SAVITRI — BLOOD", "SAVITRI — BRUISED", "BALDEV — OLDER NOW"} <= set(makeup)


def test_full_name_and_first_name_suggested_as_one_person(db, project, tmp_path):
    p = tmp_path / "names.fountain"
    p.write_text("Title: Names\n\nINT. HALL - DAY #1#\n\nSAVITRI KUMARI (20) waits.\n\nSAVITRI KUMARI\nYes.\n\n"
                 "INT. ROOM - DAY #2#\n\nSAVITRI\nNo.\n", encoding="utf-8")
    v = import_script(db, project.id, p)
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    propose(db, v.id)
    msgs = [i.message for i in db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "character_alias"))]
    assert any("'SAVITRI'" in m and "SAVITRI KUMARI" in m for m in msgs)
