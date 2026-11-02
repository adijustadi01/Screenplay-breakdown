"""Desktop launcher for Screenplay Breakdown (used by the Windows installer).

Starts the local server on 127.0.0.1, keeps all data in the user's own folder
(%APPDATA%\\ScreenplayBreakdown on Windows), and opens the app in the browser.
Close the console window to quit.

    ScreenplayBreakdown.exe            run the app
    ScreenplayBreakdown.exe --smoke    self-test (used by the build): start, import a sample, check, exit
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "ScreenplayBreakdown"
PREFERRED_PORT = 8765


def bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    root = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(root) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure_environment(data: Path) -> None:
    base = bundle_dir()
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(base / "backend"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(data / 'breakdown.db').as_posix()}")
    os.environ.setdefault("UPLOAD_DIR", str(data / "uploads"))
    os.environ.setdefault("SB_CONFIG_DIR", str(data))
    for fe in (base / "frontend_dist", base / "frontend" / "dist"):
        if (fe / "index.html").exists():
            os.environ.setdefault("SB_FRONTEND_DIST", str(fe))
            break
    tess_dir = base / "tesseract"
    for exe in (tess_dir / "tesseract.exe", tess_dir / "tesseract"):
        if exe.exists():
            os.environ.setdefault("TESSERACT_CMD", str(exe))
            os.environ.setdefault("TESSDATA_PREFIX", str(tess_dir / "tessdata"))
            break
    cfg = data / "config.json"
    if cfg.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            key = json.loads(cfg.read_text(encoding="utf-8")).get("anthropic_api_key")
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
        except (OSError, ValueError):
            pass


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def our_app_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def pick_port() -> int:
    if port_free(PREFERRED_PORT):
        return PREFERRED_PORT
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int):
    import uvicorn

    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if our_app_running(port):
            return server, t
        time.sleep(0.1)
    raise RuntimeError("the server did not start")


SMOKE_SCRIPT = """Title: SMOKE TEST

INT. KITCHEN - DAY #1#

ARJUN (40) makes tea. Rain on the window.

ARJUN
Again?

EXT. STREET - CONTINUOUS #2#

Arjun walks into the rain.
"""


def smoke(port: int) -> int:
    import httpx
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120)
    assert c.get("/api/health").json()["status"] == "ok"
    assert "<html" in c.get("/").text.lower(), "UI not served"
    pid = c.post("/api/projects", json={"title": "Smoke"}).json()["id"]
    path = Path(tempfile.mkdtemp()) / "smoke.fountain"
    path.write_text(SMOKE_SCRIPT, encoding="utf-8")
    with open(path, "rb") as fh:
        r = c.post(f"/api/projects/{pid}/scripts", files={"file": ("smoke.fountain", fh)}).json()
    assert r["coverage"]["verdict"] == "COMPLETE", r["coverage"]["verdict"]
    vid = r["script_version_id"]
    c.post(f"/api/script-versions/{vid}/extract", json={"extractor": "heuristic"})
    for _ in range(100):
        job = c.get(f"/api/script-versions/{vid}/extract").json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.2)
    assert job["status"] == "finished", job
    c.post(f"/api/script-versions/{vid}/departments/extract", json={"extractor": "heuristic"})
    for _ in range(100):
        job = c.get(f"/api/script-versions/{vid}/departments/extract").json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.2)
    assert job["status"] == "finished", job
    rain = c.get(f"/api/script-versions/{vid}/departments/action_effects/report").json()
    assert rain["unique_items"] >= 1
    sid = c.post(f"/api/script-versions/{vid}/schedules", json={"name": "S"}).json()["id"]
    c.post(f"/api/schedules/{sid}/autofill", json={"max_eighths": 40})
    dood = c.get(f"/api/schedules/{sid}").json()["dood"]["cast"]
    assert dood and dood[0]["requirement"] == "ARJUN"
    s = c.get("/api/settings").json()
    print(f"SMOKE OK — OCR available: {s['ocr_available']}, data folder: {s['data_dir']}")
    return 0


def main() -> int:
    data = Path(tempfile.mkdtemp()) if "--smoke" in sys.argv else data_dir()
    configure_environment(data)
    if "--smoke" not in sys.argv and our_app_running(PREFERRED_PORT):
        webbrowser.open(f"http://127.0.0.1:{PREFERRED_PORT}/")       # already running: just open it
        return 0
    port = pick_port()
    _server, thread = start_server(port)
    if "--smoke" in sys.argv:
        try:
            return smoke(port)
        except Exception as exc:  # noqa: BLE001
            print(f"SMOKE FAILED: {exc!r}")
            return 1
    url = f"http://127.0.0.1:{port}/"
    print("=" * 64)
    print("  Screenplay Breakdown is running.")
    print(f"  Open {url} in your browser (it should open by itself).")
    print(f"  Your projects are stored in {data}")
    print("  Close this window to quit.")
    print("=" * 64)
    webbrowser.open(url)
    try:
        while thread.is_alive():
            thread.join(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
