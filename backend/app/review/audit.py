"""Audit log helpers shared by extraction, reconciliation and review."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from ..models import ReviewEvent

TABLE_TO_MODEL: dict[str, type] = {}


def snapshot(obj) -> dict[str, Any]:
    """Column values of an ORM object as JSON-safe data."""
    out = {}
    for col in sa_inspect(obj).mapper.column_attrs:
        v = getattr(obj, col.key)
        if isinstance(v, enum.Enum):
            v = v.value
        elif isinstance(v, datetime):
            v = v.isoformat()
        out[col.key] = v
    return out


def log_event(session: Session, project_id: int, obj, action: str, actor: str,
              before: dict | None = None, note: str | None = None, review_item_id: int | None = None) -> None:
    session.add(ReviewEvent(
        project_id=project_id, review_item_id=review_item_id, entity_table=obj.__tablename__,
        entity_id=obj.id, action=action, before=before, after=snapshot(obj), actor=actor, note=note,
    ))
