"""Projects, script import, coverage, scenes and source text."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..importers.coverage import coverage_report
from ..importers.service import UnsupportedFormat, import_script
from ..models import Page, Project, Scene, ScriptElement, ScriptVersion
from ..scene_detection.pagination import format_eighths
from .deps import get_session

router = APIRouter(prefix="/api")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))


class ProjectIn(BaseModel):
    title: str


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/projects")
def list_projects(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    out = []
    for p in s.scalars(select(Project).order_by(Project.id)):
        out.append({"id": p.id, "title": p.title, "script_versions": [
            {"id": v.id, "label": v.label, "file": v.source_filename, "format": v.source_format.value,
             "status": v.status.value, "pages": v.page_count, "imported_at": v.imported_at.isoformat()}
            for v in p.script_versions]})
    return out


@router.post("/projects")
def create_project(body: ProjectIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = Project(title=body.title)
    s.add(p)
    s.commit()
    return {"id": p.id, "title": p.title}


@router.post("/projects/{project_id}/scripts")
def upload_script(
    project_id: int,
    file: UploadFile = File(...),
    label: str | None = Form(None),
    ocr_lang: str = Form("eng"),
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "script").suffix
    fd, tmp = tempfile.mkstemp(suffix=suffix, dir=UPLOAD_DIR)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        v = import_script(s, project_id, tmp, label=label, ocr_lang=ocr_lang,
                          original_filename=file.filename)
    except UnsupportedFormat as exc:
        s.rollback()
        raise HTTPException(415, str(exc)) from exc
    s.commit()
    return {"script_version_id": v.id, "coverage": coverage_report(s, v.id)}


@router.get("/script-versions/{version_id}/coverage")
def get_coverage(version_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return coverage_report(s, version_id)
    except KeyError:
        raise HTTPException(404, "script version not found") from None


def _scene_dict(sc: Scene) -> dict[str, Any]:
    return {
        "id": sc.id, "seq": sc.seq, "scene_number": sc.scene_number,
        "scene_number_right": sc.scene_number_right, "number_status": sc.number_status.value,
        "is_omitted": sc.is_omitted, "heading_raw": sc.heading_raw, "heading_status": sc.heading_status.value,
        "int_ext": sc.int_ext.value, "int_ext_raw": sc.int_ext_raw, "location_raw": sc.location_raw,
        "time_of_day": sc.time_of_day.value, "time_of_day_raw": sc.time_of_day_raw,
        "effective_time_of_day": sc.effective_time_of_day.value if sc.effective_time_of_day else None,
        "effective_time_source_scene_id": sc.effective_time_source_scene_id,
        "start_page": sc.start_page_label, "end_page": sc.end_page_label,
        "length_eighths": sc.length_eighths, "length": format_eighths(sc.length_eighths),
        "length_method": sc.length_method.value if sc.length_method else None,
        "synopsis": sc.synopsis, "confidence": sc.confidence, "needs_review": sc.needs_review,
        "review_reason": sc.review_reason, "review_status": sc.review_status.value,
    }


@router.get("/script-versions/{version_id}/scenes")
def list_scenes(version_id: int, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    if s.get(ScriptVersion, version_id) is None:
        raise HTTPException(404, "script version not found")
    return [_scene_dict(sc) for sc in
            s.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq))]


@router.get("/scenes/{scene_id}")
def get_scene(scene_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    sc = s.get(Scene, scene_id)
    if sc is None:
        raise HTTPException(404, "scene not found")
    labels = dict(s.execute(select(Page.id, Page.page_label).where(
        Page.script_version_id == sc.script_version_id)).all())
    els = s.scalars(select(ScriptElement).where(ScriptElement.scene_id == scene_id)
                    .order_by(ScriptElement.seq)).all()
    d = _scene_dict(sc)
    d["elements"] = [{"id": e.id, "seq": e.seq, "type": e.element_type.value, "text": e.text,
                      "raw_text": e.raw_text, "page": labels.get(e.page_id),
                      "classification_confidence": e.classification_confidence,
                      "ocr_confidence": e.ocr_confidence} for e in els]
    return d
