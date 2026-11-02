"""Every sample format must reproduce the ground truth scene list exactly."""
import pytest
from sqlalchemy import select

from app.importers.coverage import coverage_report
from app.importers.service import import_script
from app.models import Scene, ScriptElement
from app.models.enums import ElementType

from .conftest import GROUND_TRUTH, HAS_TESSERACT, SAMPLES

FORMATS = [
    "monsoon_ledger.fountain",
    "monsoon_ledger.fdx",
    "monsoon_ledger.pdf",
    "monsoon_ledger.docx",
    "monsoon_ledger_plain.docx",
    pytest.param("monsoon_ledger_scanned.pdf",
                 marks=pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")),
]


def _import(db, project, name):
    v = import_script(db, project.id, SAMPLES / name)
    scenes = db.scalars(select(Scene).where(Scene.script_version_id == v.id).order_by(Scene.seq)).all()
    return v, scenes


@pytest.mark.parametrize("name", FORMATS)
def test_scenes_match_ground_truth(db, project, name):
    v, scenes = _import(db, project, name)
    truth = GROUND_TRUTH["scenes"]
    assert len(scenes) == GROUND_TRUTH["scene_count"]
    unnumbered_file = name == "monsoon_ledger.docx"
    for sc, t in zip(scenes, truth):
        ctx = f"{name} seq {t['seq']}"
        assert sc.int_ext.value == t["int_ext"], ctx
        assert sc.time_of_day.value == t["time"], ctx
        assert (sc.effective_time_of_day.value if sc.effective_time_of_day else None) == t["effective_time"], ctx
        assert sc.is_omitted == t.get("omitted", False), ctx
        if not sc.is_omitted:
            assert sc.location_raw == t["location_raw"], ctx
        if unnumbered_file:
            assert sc.scene_number is None and sc.number_status.value == "missing", ctx
        else:
            assert sc.scene_number == t["number"], ctx
            assert sc.number_status.value == t.get("number_status", "ok"), ctx
        assert sc.heading_status.value == t.get("heading_status", "ok"), ctx
        # every uncertainty is flagged
        if t["time"] in ("CONTINUOUS", "LATER") or t.get("heading_status") or t.get("number_status"):
            assert sc.needs_review, ctx


@pytest.mark.parametrize("name", FORMATS)
def test_coverage_is_complete(db, project, name):
    v, scenes = _import(db, project, name)
    r = coverage_report(db, v.id)
    assert r["text_accounting"]["ok"], r["text_accounting"]
    assert r["elements"]["unassigned"] == []
    assert r["review_queue"]["by_severity"].get("error", 0) == 0
    assert r["verdict"] == "COMPLETE"
    # Omitted scenes have zero length; every live scene at least 1/8.
    for sc in scenes:
        assert sc.length_eighths == 0 if sc.is_omitted else sc.length_eighths >= 1


@pytest.mark.parametrize("name", ["monsoon_ledger.fountain", "monsoon_ledger.pdf"])
def test_eighths_sum_matches_page_count(db, project, name):
    v, scenes = _import(db, project, name)
    total = sum(s.length_eighths for s in scenes)
    body_pages = v.page_count - 1  # minus title page
    # Full pages sum to 8/8 each; the last page is partial.
    assert 8 * (body_pages - 1) < total <= 8 * body_pages


def test_dialogue_elements_belong_to_their_scene(db, project):
    v, scenes = _import(db, project, "monsoon_ledger.pdf")
    first = scenes[0]
    cues = db.scalars(select(ScriptElement.text).where(
        ScriptElement.scene_id == first.id, ScriptElement.element_type == ElementType.CHARACTER)).all()
    assert cues == ["MEERA", "ARJUN", "MEERA", "ARJUN", "KABIR (O.S.)"]


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
def test_scanned_pdf_is_marked_ocr(db, project):
    v, _ = _import(db, project, "monsoon_ledger_scanned.pdf")
    r = coverage_report(db, v.id)
    assert r["pages"]["ocr"] == r["pages"]["total"]
