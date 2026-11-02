"""Stage 2 — Costumes and Makeup & hair: continuity across scenes."""
from sqlalchemy import select

from app.departments.reports import run_department_report
from app.models import BreakdownOccurrence, ReviewItem

from .dept_helpers import TruthDeptExtractor, dept_pipeline

D = ["costumes", "makeup_hair"]


def _timeline(r, name):
    return next(c for c in r["characters"] if c["name"] == name)["timeline"]


def test_costume_states_and_multiples(db, project):
    v = dept_pipeline(db, project, D)
    rows = {x["name"]: x for x in run_department_report(db, v.id, "costumes")["items"]}
    jacket = rows["RAGHAV — LEATHER JACKET"]
    assert jacket["scenes"]["count"] == 2 and jacket["states"] == ["damp", "wet"] and jacket["needs_multiples"]
    assert rows["DEV — 1970S BELL-BOTTOMS AND WIDE-COLLARED SHIRT"]["category"] == "period"
    assert rows["KHAN — KHAKI POLICE UNIFORM"]["wearers"] == ["INSPECTOR KHAN"]
    reasons = {o.review_reason for o in db.scalars(select(BreakdownOccurrence)) if o.review_reason}
    assert any("continuity_state" in r for r in reasons)


def test_injury_continuity_fresh_then_bandaged(db, project):
    v = dept_pipeline(db, project, D)
    r = run_department_report(db, v.id, "continuity")
    t = _timeline(r, "RAGHAV")
    by_scene = {x["scene"]: x for x in t}
    assert {"item": "RAGHAV — FOREHEAD CUT", "state": "fresh"}.items() <= next(
        e for e in by_scene["4"]["entries"] if e["item"] == "RAGHAV — FOREHEAD CUT").items()
    assert next(e for e in by_scene["5"]["entries"] if e["item"] == "RAGHAV — FOREHEAD CUT")["state"] == "bandaged"
    assert by_scene["5"]["changed"]


def test_continuity_check_when_injury_disappears(db, project):
    def forget_bandage(label, out):
        if label == "5":
            out["makeup_hair"] = []          # extraction misses the cut in scene 5
        return out
    v = dept_pipeline(db, project, D, TruthDeptExtractor(D, forget_bandage))
    r = run_department_report(db, v.id, "continuity")
    raghav = next(c for c in r["characters"] if c["name"] == "RAGHAV")
    chk = [c for c in raghav["continuity_checks"] if c["item"] == "RAGHAV — FOREHEAD CUT"]
    assert [c["scene"] for c in chk] == ["5"]     # a check for the AD, not a silent fix


def test_special_makeup_flagged(db, project):
    dept_pipeline(db, project, D)
    codes = {i.reason_code for i in db.scalars(select(ReviewItem))}
    assert "special_makeup" in codes          # prosthetic scar, disguise, ageing
