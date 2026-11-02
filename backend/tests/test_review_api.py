"""Step 5 through the API: queue -> source passage -> correction -> reports update."""
import time

from fastapi.testclient import TestClient

from app.api import deps
from app.main import app

from .conftest import SAMPLES


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.scripts.UPLOAD_DIR", tmp_path)
    deps.configure(f"sqlite:///{tmp_path / 'r.db'}")
    return TestClient(app)


def _setup(c):
    pid = c.post("/api/projects", json={"title": "M"}).json()["id"]
    with open(SAMPLES / "monsoon_ledger.fountain", "rb") as fh:
        vid = c.post(f"/api/projects/{pid}/scripts", files={"file": ("m.fountain", fh)}).json()["script_version_id"]
    assert c.post(f"/api/script-versions/{vid}/extract", json={"extractor": "heuristic", "workers": 2}).status_code == 200
    for _ in range(100):
        job = c.get(f"/api/script-versions/{vid}/extract").json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "finished", job
    return pid, vid


def test_review_flow(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    pid, vid = _setup(c)
    q = c.get(f"/api/projects/{pid}/review", params={"version_id": vid}).json()
    assert q["total"] > 0
    sev = [i["severity"] for i in q["items"]]
    assert sev == sorted(sev, key=["error", "warning", "info"].index)   # flagged items first

    # The photograph trap: heuristic marks VIKRAM present in scene 7 — find and fix it.
    scenes = c.get(f"/api/script-versions/{vid}/scenes").json()
    sc7 = next(s for s in scenes if s["scene_number"] == "7")
    bd = c.get(f"/api/scenes/{sc7['id']}/breakdown").json()
    vik = next(a for a in bd["appearances"] if a["character_name"] == "VIKRAM")
    assert vik["presence"] == "present"
    item = next(i for i in c.get(f"/api/projects/{pid}/review", params={"scene_id": sc7["id"]}).json()["items"]
                if i["entity_table"] == "appearances" and i["entity_id"] == vik["id"])
    detail = c.get(f"/api/review/{item['id']}").json()
    assert any(e["highlight"] for e in detail["source"]["elements"])          # exact passage shown
    before = c.get(f"/api/script-versions/{vid}/reports/characters").json()
    vb = next(x for x in before["characters"] if x["name"] == "VIKRAM")
    r = c.post(f"/api/review/{item['id']}", json={"action": "edit", "changes": {"presence": "mentioned"},
                                                  "note": "only in the photograph"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    after = c.get(f"/api/script-versions/{vid}/reports/characters").json()
    va = next(x for x in after["characters"] if x["name"] == "VIKRAM")
    assert va["appearances"]["count"] == vb["appearances"]["count"] - 1
    assert va["mentioned_only"]["count"] == vb["mentioned_only"]["count"] + 1
    hist = c.get(f"/api/facts/appearances/{vik['id']}/history").json()
    assert hist[-1]["action"] == "edit" and hist[-1]["before"]["presence"] == "present"

    # Add the missed group by hand.
    sc12 = next(s for s in scenes if s["scene_number"] == "12")
    added = c.post(f"/api/scenes/{sc12['id']}/appearances",
                   json={"new_character_name": "Vikram's men", "presence": "present", "speaking": "silent"})
    assert added.status_code == 200 and added.json()["review_status"] == "confirmed"

    # Reconcile + accept through the API, then drill into a number.
    made = c.post(f"/api/script-versions/{vid}/reconcile").json()["proposals_created"]
    assert made
    props = c.get(f"/api/projects/{pid}/review", params={"category": "reconciliation"}).json()["items"]
    out = c.post("/api/review-bulk", json={"item_ids": [p["id"] for p in props], "action": "accept"}).json()
    assert not out["errors"]
    q = c.post(f"/api/script-versions/{vid}/query", json={"int_ext": ["EXT"], "rows": "character",
                                                          "columns": "time"}).json()
    ids = q["table"]["cells"]["ARJUN"]["NIGHT"]["scene_ids"]
    listed = c.get(f"/api/script-versions/{vid}/scene-list", params={"ids": ",".join(map(str, ids))}).json()
    assert [s["number"] for s in listed] == ["14"]
    opts = c.get(f"/api/script-versions/{vid}/query/options").json()
    assert any(loc["label"] == "CITY HOSPITAL > ROOFTOP" for loc in opts["locations"])
