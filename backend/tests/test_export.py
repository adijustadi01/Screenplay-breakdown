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
