"""Local settings for the desktop app: the Anthropic API key, OCR availability, data folder."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api")


def _config_path() -> Path:
    d = Path(os.environ.get("SB_CONFIG_DIR") or Path.home() / ".screenplay-breakdown")
    d.mkdir(parents=True, exist_ok=True)
    return d / "config.json"


@router.get("/settings")
def get_settings() -> dict:
    tess = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    return {"has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "ocr_available": bool(tess and Path(tess).exists() if os.environ.get("TESSERACT_CMD") else tess),
            "data_dir": str(_config_path().parent)}


class SettingsIn(BaseModel):
    anthropic_api_key: str | None = None


@router.post("/settings")
def save_settings(body: SettingsIn) -> dict:
    """Stores the key in the user's own config file (plain text, on this computer only)."""
    path = _config_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    key = (body.anthropic_api_key or "").strip()
    if key:
        data["anthropic_api_key"] = key
        os.environ["ANTHROPIC_API_KEY"] = key
    else:
        data.pop("anthropic_api_key", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return get_settings()
