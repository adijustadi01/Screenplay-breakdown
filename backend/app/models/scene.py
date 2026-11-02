"""Scenes: detected deterministically from headings; never renumbered."""
from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import enum_col
from ..db.base import Base
from .enums import HeadingStatus, IntExt, LengthMethod, SceneNumberStatus, TimeOfDay
from .provenance import ProvenanceMixin, provenance_args


class Scene(ProvenanceMixin, Base):
    """Provenance on a Scene covers the heading parse (source = the heading element)."""

    __tablename__ = "scenes"
    __table_args__ = provenance_args(
        UniqueConstraint("script_version_id", "seq"),
        CheckConstraint("length_eighths IS NULL OR length_eighths >= 0", name="length_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    script_version_id: Mapped[int] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # order in the script (1..N); NOT the scene number

    # --- Numbering: preserved verbatim, never generated ---------------------
    scene_number: Mapped[str | None] = mapped_column(String(20), index=True)  # "23", "23A", "A23"
    scene_number_right: Mapped[str | None] = mapped_column(String(20))         # right-margin number if different
    number_status: Mapped[SceneNumberStatus] = mapped_column(
        enum_col(SceneNumberStatus), default=SceneNumberStatus.OK
    )
    is_omitted: Mapped[bool] = mapped_column(Boolean, default=False)          # "23 OMITTED"

    # --- Heading ------------------------------------------------------------
    heading_raw: Mapped[str | None] = mapped_column(Text)   # None only if heading_status=INFERRED
    heading_status: Mapped[HeadingStatus] = mapped_column(
        enum_col(HeadingStatus), default=HeadingStatus.OK
    )
    int_ext: Mapped[IntExt] = mapped_column(enum_col(IntExt), index=True)
    int_ext_raw: Mapped[str | None] = mapped_column(String(20))               # "INT./EXT."
    location_raw: Mapped[str | None] = mapped_column(Text)                    # "ARJUN'S HOUSE - KITCHEN"

    # Time of day AS WRITTEN. CONTINUOUS stays CONTINUOUS.
    time_of_day: Mapped[TimeOfDay] = mapped_column(enum_col(TimeOfDay), index=True)
    time_of_day_raw: Mapped[str | None] = mapped_column(String(60))
    # Inherited lighting for relative times (CONTINUOUS/SAME/LATER...).
    # Filled by walking back to the nearest scene with an absolute time; always
    # flagged for review and reported separately from the as-written value.
    effective_time_of_day: Mapped[TimeOfDay | None] = mapped_column(enum_col(TimeOfDay))
    effective_time_source_scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL")
    )
    effective_time_needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Pages & length -----------------------------------------------------
    start_page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"))
    end_page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"))
    start_page_label: Mapped[str | None] = mapped_column(String(20))
    end_page_label: Mapped[str | None] = mapped_column(String(20))
    length_eighths: Mapped[int | None] = mapped_column(Integer)  # 13 = "1 5/8 pages"
    length_method: Mapped[LengthMethod | None] = mapped_column(enum_col(LengthMethod))

    # --- Description (LLM, Stage 1 extraction) ------------------------------
    synopsis: Mapped[str | None] = mapped_column(Text)
    synopsis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="SET NULL")
    )

    appearances = relationship(
        "Appearance", back_populates="scene", cascade="all, delete-orphan",
        foreign_keys="Appearance.scene_id",
    )
    scene_locations = relationship(
        "SceneLocation", back_populates="scene", cascade="all, delete-orphan",
        foreign_keys="SceneLocation.scene_id",
    )
