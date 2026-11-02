"""Steps 4 and 6: counts computed in code must match the ground truth exactly,
and a correction must change every affected number."""
from collections import Counter

import pytest
from sqlalchemy import select

from app.models import Appearance, Character, Location
from app.query.engine import QueryFilters, run_query
from app.reports.service import run_report
from app.review.service import reject_fact

from .conftest import GROUND_TRUTH
from .helpers import pipeline

LIVE = [s for s in GROUND_TRUTH["scenes"] if not s.get("omitted")]
ON_SET = ("present", "off_screen")


def _truth_char_scenes(name):
    return [s for s in LIVE for c in s["characters"] if c["character"] == name and c["presence"] in ON_SET]


@pytest.fixture()
def v(db, project):
    return pipeline(db, project, reconcile=True)


def test_scene_report_matches_truth(db, v):
    r = run_report(db, v.id, "scenes")
    assert r["total"]["count"] == len(LIVE) and r["omitted"]["count"] == 1
    assert {k: x["count"] for k, x in r["by_int_ext"].items()} == dict(Counter(s["int_ext"] for s in LIVE))
    assert {k: x["count"] for k, x in r["by_time"].items()} == dict(Counter(s["time"] for s in LIVE))
    eff = run_report(db, v.id, "scenes", time_mode="effective")
    want = Counter(s["effective_time"] or s["time"] for s in LIVE)
    assert {k: x["count"] for k, x in eff["by_time"].items()} == dict(want)
    assert eff["inferred_time"]["count"] == 3


def test_character_report_matches_truth(db, v):
    r = run_report(db, v.id, "characters")
    rows = {c["name"]: c for c in r["characters"]}
    for name in ("ARJUN", "MEERA", "VIKRAM", "DADI", "KABIR"):
        truth = _truth_char_scenes(name)
        assert rows[name]["appearances"]["count"] == len(truth), name
        dlg = sum(c["dialogue"] for s in LIVE for c in s["characters"] if c["character"] == name)
        assert rows[name]["dialogue_blocks"] == dlg, name
    assert rows["MEERA"]["voice_only"]["count"] == 1
    assert rows["KABIR"]["off_screen"]["count"] == 1
    mentioned = [s for s in LIVE for c in s["characters"] if c["character"] == "VIKRAM" and c["presence"] == "mentioned"]
    assert rows["VIKRAM"]["mentioned_only"]["count"] == len(mentioned) == 3   # never counted as on set
    assert rows["ARJUN"]["casting_roles"] == ["ARJUN", "YOUNG ARJUN"]
    assert rows["ARJUN"]["first_appearance"]["number"] == "1"
    assert rows["DADI"]["silent"]["count"] == 1 and rows["DADI"]["speaking"]["count"] == 1


def test_every_cell_carries_its_scenes(db, v):
    r = run_report(db, v.id, "characters")
    arjun = next(c for c in r["characters"] if c["name"] == "ARJUN")
    assert len(arjun["appearances"]["scene_ids"]) == arjun["appearances"]["count"]


def test_location_report_rolls_up_children(db, v):
    r = run_report(db, v.id, "locations")
    rows = {n["label"]: n for n in r["locations"]}
    house = [s for s in LIVE if s["location"] and s["location"][0] == "ARJUN'S HOUSE"]
    assert rows["ARJUN'S HOUSE"]["scenes"]["count"] == len(house)
    assert rows["ARJUN'S HOUSE > KITCHEN"]["scenes"]["count"] == 2
    assert rows["CITY HOSPITAL"]["scenes"]["count"] == 4
    kitchen_cast = {c["name"]: c["count"] for c in rows["ARJUN'S HOUSE > KITCHEN"]["characters"]}
    assert kitchen_cast == {"ARJUN": 2, "DADI": 2, "MEERA": 2, "KABIR": 2}


def test_combinations(db, v):
    r = run_report(db, v.id, "combinations")
    pair = next(c for c in r["combinations"] if set(c["names"]) == {"ARJUN", "MEERA"})
    truth = [s for s in LIVE if {"ARJUN", "MEERA"} <= {c["character"] for c in s["characters"]
                                                     if c["presence"] in ON_SET}]
    assert pair["together"]["count"] == len(truth)


def test_crosstabs(db, v):
    ct = run_report(db, v.id, "crosstab:character_time")
    assert ct["cells"]["ARJUN"]["NIGHT"]["count"] == len([s for s in _truth_char_scenes("ARJUN")
                                                        if s["time"] == "NIGHT"])
    loc = run_report(db, v.id, "crosstab:location_time")
    assert loc["cells"]["CITY HOSPITAL"]["NIGHT"]["count"] == 3
    clt = run_report(db, v.id, "crosstab:character_location_time")
    assert "MEERA @ CITY HOSPITAL > ICU" in clt["rows"]


def test_rejecting_a_fact_changes_every_report(db, v):
    meera = db.scalars(select(Character).where(Character.canonical_name == "MEERA")).one()
    before = next(c for c in run_report(db, v.id, "characters")["characters"] if c["name"] == "MEERA")
    a = db.scalars(select(Appearance).where(Appearance.character_id == meera.id,
                                            Appearance.presence == "present")).first()
    reject_fact(db, "appearances", a.id, "AD")
    after = next(c for c in run_report(db, v.id, "characters")["characters"] if c["name"] == "MEERA")
    assert after["appearances"]["count"] == before["appearances"]["count"] - 1
    assert a.scene_id not in after["appearances"]["scene_ids"]


def test_query_exterior_night_featuring_hero(db, v):
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    r = run_query(db, v.id, QueryFilters(int_ext=["EXT"], times=["NIGHT"], character_ids=[arjun.id]))
    truth = [s for s in _truth_char_scenes("ARJUN") if s["int_ext"] == "EXT" and s["time"] == "NIGHT"]
    assert r["total"]["count"] == len(truth) == 1
    assert r["scenes"][0]["number"] == "14"


def test_query_hospital_per_character_day_night(db, v):
    hosp = db.scalars(select(Location).where(Location.name == "CITY HOSPITAL")).one()
    r = run_query(db, v.id, QueryFilters(location_ids=[hosp.id], rows="character", columns="time"))
    assert r["total"]["count"] == 4                     # includes ICU, CORRIDOR, ROOFTOP children
    assert r["table"]["cells"]["MEERA"]["NIGHT"]["count"] == 2
    assert r["table"]["cells"]["MEERA"]["DAWN"]["count"] == 1
    assert "CITY HOSPITAL" in r["description"]


def test_query_effective_time_includes_inherited(db, v):
    r1 = run_query(db, v.id, QueryFilters(times=["DAY"]))
    r2 = run_query(db, v.id, QueryFilters(times=["DAY"], time_mode="effective"))
    assert r2["total"]["count"] == r1["total"]["count"] + 2   # CONTINUOUS sc 2 and LATER sc 8A
