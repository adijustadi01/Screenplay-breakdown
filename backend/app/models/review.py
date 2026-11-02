"""The review queue and the audit log."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, enum_col, ts_col, utcnow
from .enums import ReviewCategory, ReviewItemStatus, Resolution, Severity


class ReviewItem(Base):
    """One thing the AD should look at. A single queue for everything:

    IMPORT          page with low OCR confidence, missing scene number, ambiguous
                    heading, text not assigned to any scene ...
    EXTRACTION      a fact with needs_review=True (uncertain presence, unknown headcount,
                    CONTINUOUS time inheritance, low confidence ...)
    RECONCILIATION  a proposed merge/link ("YOUNG ARJUN is a casting variant of ARJUN",
                    "HOSPITAL CORRIDOR belongs under CITY HOSPITAL"). Nothing is merged
                    until the proposal is accepted.

    `entity_table`/`entity_id` point at the fact under review (polymorphic by design so
    Stage 2 tables plug in without schema changes). `scene_id`/`page_id`/`element_id`
    jump straight to the source passage.
    """

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    script_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[ReviewCategory] = mapped_column(enum_col(ReviewCategory), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)   # "continuous_time", "alias_merge"
    severity: Mapped[Severity] = mapped_column(enum_col(Severity), default=Severity.WARNING, index=True)
    message: Mapped[str] = mapped_column(Text)

    entity_table: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), index=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"))
    element_id: Mapped[int | None] = mapped_column(
        ForeignKey("script_elements.id", ondelete="SET NULL")
    )
    # Machine-applicable proposal, e.g.
    # {"action": "merge_character", "from_id": 12, "into_id": 3, "evidence": [...]}.
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column()

    status: Mapped[ReviewItemStatus] = mapped_column(
        enum_col(ReviewItemStatus), default=ReviewItemStatus.OPEN, index=True
    )
    resolution: Mapped[Resolution | None] = mapped_column(enum_col(Resolution))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    resolved_at: Mapped[datetime | None] = mapped_column(ts_col())
    created_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)


class ReviewEvent(Base):
    """Append-only audit log of every change to a reviewable fact (human or machine).
    `before`/`after` are JSON snapshots, so any correction can be inspected or undone."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    review_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_items.id", ondelete="SET NULL"), index=True
    )
    entity_table: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))     # confirm/edit/reject/create/merge/unmerge
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(120))     # "AD:priya" or "system:reconciliation"
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)
