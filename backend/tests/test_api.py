from fastapi.testclient import TestClient

from app.api import deps
from app.main import app

from .conftest import SAMPLES


def test_upload_and_browse(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.scripts.UPLOAD_DIR", tmp_path)
    deps.configure(f"sqlite:///{tmp_path / 'api.db'}")
    c = TestClient(app)
    pid = c.post("/api/projects", json={"title": "Monsoon"}).json()["id"]
    with open(SAMPLES / "monsoon_ledger.fountain", "rb") as fh:
        r = c.post(f"/api/projects/{pid}/scripts", files={"file": ("monsoon_ledger.fountain", fh)})
    assert r.status_code == 200, r.text
    vid = r.json()["script_version_id"]
    assert r.json()["coverage"]["verdict"] == "COMPLETE"
    scenes = c.get(f"/api/script-versions/{vid}/scenes").json()
    assert len(scenes) == 15 and scenes[8]["scene_number"] == "8A"
    detail = c.get(f"/api/scenes/{scenes[0]['id']}").json()
    assert detail["elements"][0]["type"] == "scene_heading"
    assert c.get("/api/script-versions/999/coverage").status_code == 404
    with open(SAMPLES / "build_samples.py", "rb") as fh:
        bad = c.post(f"/api/projects/{pid}/scripts", files={"file": ("x.py", fh)})
    assert bad.status_code == 415
