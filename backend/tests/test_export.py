"""Excel export: one workbook, numbers identical to the in-app reports, nothing invented."""
import io

from openpyxl import load_workbook

from app.export.excel import build_workbook
from app.scheduling import service as sv

from .stage3_helpers import prepared_draft


def _rows(ws):
    rows = list(ws.iter_rows(values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] in ("Sc", "Character", "Location", "ID", "Shooting day")
               or (r and r[-1] == "Check?"))
    return [dict(zip(rows[hdr], r)) for r in rows[hdr + 1:]]


def test_workbook_has_all_sheets_and_matches_reports(db, project):
    v = prepared_draft(db, project)
    sch = sv.create_schedule(db, v.id, "Main")
    sv.autofill(db, sch.id)
    data, name = build_workbook(db, v.id)
    assert name.endswith(".xlsx")
    wb = load_workbook(io.BytesIO(data))
    for sheet in ("Read me", "Scene breakdown", "Characters", "Locations", "Props", "To check",
                  "Schedule - Main", "DOOD - Main"):
        assert sheet in wb.sheetnames, sheet
    scenes = _rows(wb["Scene breakdown"])
    assert {r["Sc"] for r in scenes} >= {"1", "2", "3"}
    chars = {r["Character"]: r for r in _rows(wb["Characters"])}
    from app.reports.facts import load_facts
    from app.reports.service import character_report
    rep = {c["name"]: c for c in character_report(load_facts(db, v.id))["characters"]}
    assert chars["MEERA"]["Scenes"] == rep["MEERA"]["appearances"]["count"]
    dood = _rows(wb["DOOD - Main"])
    assert {r["Casting requirement"] for r in dood} >= {"ARJUN", "YOUNG ARJUN"}




def test_export_endpoint_downloads_xlsx(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import app

    from .conftest import SAMPLES
    monkeypatch.setattr("app.api.scripts.UPLOAD_DIR", tmp_path)
    deps.configure(f"sqlite:///{tmp_path / 'api.db'}")
    c = TestClient(app)
    pid = c.post("/api/projects", json={"title": "Monsoon"}).json()["id"]
    with open(SAMPLES / "monsoon_ledger.fountain", "rb") as fh:
        vid = c.post(f"/api/projects/{pid}/scripts", files={"file": ("m.fountain", fh)}).json()["script_version_id"]
    r = c.get(f"/api/script-versions/{vid}/export.xlsx")     # works even before extraction
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]
    assert c.get("/api/script-versions/999/export.xlsx").status_code == 404


def test_one_click_breakdown_and_progress(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import app

    from .conftest import SAMPLES
    monkeypatch.setattr("app.api.scripts.UPLOAD_DIR", tmp_path)
    deps.configure(f"sqlite:///{tmp_path / 'api.db'}")
    c = TestClient(app)
    pid = c.post("/api/projects", json={"title": "Highway"}).json()["id"]
    with open(SAMPLES / "highway_song.fountain", "rb") as fh:
        vid = c.post(f"/api/projects/{pid}/scripts", files={"file": ("h.fountain", fh)}).json()["script_version_id"]
    p = c.get(f"/api/script-versions/{vid}/progress").json()
    assert p["scenes"] > 0 and not p["cast_done"] and not p["departments_done"]
    assert c.post(f"/api/script-versions/{vid}/breakdown-all", json={"extractor": "heuristic", "workers": 1}).status_code == 200
    for _ in range(200):
        p = c.get(f"/api/script-versions/{vid}/progress").json()
        if p["job"]["status"] != "running":
            break
        time.sleep(0.1)
    assert p["job"]["status"] == "finished", p["job"]
    assert p["cast_done"] and p["departments_done"]


# ------------------------------------------------------------------ choose what to download; PDF
def test_catalog_lists_every_report_and_department(db, project):
    from app.export import sections as sec
    v = prepared_draft(db, project)
    sch = sv.create_schedule(db, v.id, "Main")
    cat = {c["key"]: c for c in sec.catalog(sec.Context(db, v.id))}
    for key in ("summary", "scenes", "scene_summary", "characters", "locations", "combinations",
                "character_time", "location_time", "character_location_time", "dept:props", "continuity",
                f"schedule:{sch.id}", f"dood:{sch.id}", "to_check"):
        assert key in cat, key
    assert {c["group"] for c in cat.values()} == {"Main", "Reports", "Departments", "Schedule", "Other"}


def test_workbook_with_only_chosen_sections(db, project):
    v = prepared_draft(db, project)
    data, _ = build_workbook(db, v.id, ["characters", "combinations", "locations"])
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Characters", "Locations", "Who appears together"]   # file order, not click order
    pairs = list(wb["Who appears together"].iter_rows(values_only=True))
    hdr = pairs.index(next(r for r in pairs if r and r[0] == "Characters"))
    from app.reports.facts import load_facts
    from app.reports.service import combination_report
    rep = combination_report(load_facts(db, v.id))["combinations"]
    assert len(pairs) - hdr - 1 == len(rep)
    assert pairs[hdr + 1][1] == rep[0]["together"]["count"]


def test_unknown_section_is_rejected(db, project):
    import pytest
    v = prepared_draft(db, project)
    with pytest.raises(KeyError):
        build_workbook(db, v.id, ["characters", "nonsense"])


def test_pdf_all_and_chosen(db, project):
    from app.export.pdf import build_pdf
    v = prepared_draft(db, project)
    sch = sv.create_schedule(db, v.id, "Main")
    sv.autofill(db, sch.id)
    full, name = build_pdf(db, v.id)
    assert full[:5] == b"%PDF-" and name.endswith(".pdf")
    some, _ = build_pdf(db, v.id, ["characters", "combinations"])
    assert some[:5] == b"%PDF-" and len(some) < len(full)
    import pymupdf as fitz
    text = "".join(p.get_text() for p in fitz.open(stream=some, filetype="pdf"))
    assert "Who appears together" in text and "MEERA" in text
    assert "Scene breakdown" not in text


def test_pdf_and_sections_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import app

    from .conftest import SAMPLES
    monkeypatch.setattr("app.api.scripts.UPLOAD_DIR", tmp_path)
    deps.configure(f"sqlite:///{tmp_path / 'api.db'}")
    c = TestClient(app)
    pid = c.post("/api/projects", json={"title": "Monsoon"}).json()["id"]
    with open(SAMPLES / "monsoon_ledger.fountain", "rb") as fh:
        vid = c.post(f"/api/projects/{pid}/scripts", files={"file": ("m.fountain", fh)}).json()["script_version_id"]
    secs = c.get(f"/api/script-versions/{vid}/export/sections").json()
    assert any(x["key"] == "combinations" for x in secs)
    r = c.get(f"/api/script-versions/{vid}/export.pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    assert r.headers["content-type"] == "application/pdf"
    r = c.get(f"/api/script-versions/{vid}/export.xlsx?sections=characters,locations")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert c.get(f"/api/script-versions/{vid}/export.pdf?sections=bogus").status_code == 400
