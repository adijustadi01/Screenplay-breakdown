"""Provenance mixin: every extracted fact says where it came from and how sure we are."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from ..db.base import enum_col, ts_col
from .enums import ExtractorKind, ReviewStatus


class ProvenanceMixin:
    """Columns shared by every table that holds an extracted (i.e. reviewable) fact.

    * source_*         — the exact passage the fact was read from.
    * extracted_by     — parser / OCR / LLM / reconciliation / human.
    * confidence       — 0.0–1.0. Parser facts from clean text are 1.0.
    * needs_review     — True whenever anything is uncertain. Set liberally.
    * review_status    — the AD's verdict. CONFIRMED/EDITED facts are locked:
                         re-extraction never overwrites them.
    """

    @declared_attr
    def source_scene_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("scenes.id", ondelete="SET NULL"), index=True)

    @declared_attr
    def source_element_start_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("script_elements.id", ondelete="SET NULL"))

    @declared_attr
    def source_element_end_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("script_elements.id", ondelete="SET NULL"))

    # Verbatim quote of the supporting text, so the review UI never has to guess.
    source_excerpt: Mapped[str | None] = mapped_column(Text)

    extracted_by: Mapped[ExtractorKind] = mapped_column(enum_col(ExtractorKind))

    @declared_attr
    def extraction_run_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("extraction_runs.id", ondelete="SET NULL"))

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Machine-readable reason(s) the item was flagged, e.g. "continuous_time,low_confidence".
    review_reason: Mapped[str | None] = mapped_column(String(255))
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_col(ReviewStatus), default=ReviewStatus.UNREVIEWED, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(ts_col())
    reviewed_by: Mapped[str | None] = mapped_column(String(120))



def provenance_args(*extra) -> tuple:
    """__table_args__ for tables using ProvenanceMixin (adds the confidence range check)."""
    return (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
        *extra,
    )


# Used by quantity-bearing facts (group characters now; background, props, vehicles in Stage 2).
class QuantityMixin:
    """A headcount/quantity that may be unknown. Never invent a number.

    "FOUR GOONS"     -> min=4, max=4, raw="FOUR GOONS"
    "a few cops"     -> min=None, max=None, raw="a few cops", needs_review
    "a huge crowd"   -> min=None, max=None, raw="a huge crowd", needs_review
    """

    quantity_min: Mapped[int | None] = mapped_column(Integer)
    quantity_max: Mapped[int | None] = mapped_column(Integer)
    quantity_raw: Mapped[str | None] = mapped_column(String(255))
