"""Source-of-truth tables: what was imported, page by page, line by line."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, enum_col, ts_col, utcnow
from .enums import (
    ElementType,
    PageTextSource,
    RunKind,
    RunStatus,
    ScriptVersionStatus,
    SourceFormat,
)


class Project(Base):
    """One production. Story entities (characters, locations) belong to the project,
    so they survive script revisions (Stage 3)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)

    script_versions: Mapped[list[ScriptVersion]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ScriptVersion(Base):
    """One imported file. Stage 1 uses a single version; Stage 3 diffs versions."""

    __tablename__ = "script_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(120))          # "Shooting Draft"
    revision_color: Mapped[str | None] = mapped_column(String(40))  # "Blue" (Stage 3)
    revision_date: Mapped[str | None] = mapped_column(String(40))
    source_filename: Mapped[str] = mapped_column(String(512))
    source_format: Mapped[SourceFormat] = mapped_column(enum_col(SourceFormat))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ScriptVersionStatus] = mapped_column(
        enum_col(ScriptVersionStatus), default=ScriptVersionStatus.IMPORTING
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="script_versions")
    pages: Mapped[list[Page]] = relationship(
        back_populates="script_version", cascade="all, delete-orphan", order_by="Page.page_index"
    )


class Page(Base):
    """A physical (PDF) or computed (DOCX/FDX/Fountain) page. Coverage is checked per page."""

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("script_version_id", "page_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_version_id: Mapped[int] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    page_index: Mapped[int] = mapped_column(Integer)               # 1-based physical order
    page_label: Mapped[str | None] = mapped_column(String(20))     # printed number, e.g. "23A"
    text_source: Mapped[PageTextSource] = mapped_column(enum_col(PageTextSource))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    ocr_mean_confidence: Mapped[float | None] = mapped_column(Float)  # 0–100 from Tesseract
    is_title_page: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    script_version: Mapped[ScriptVersion] = relationship(back_populates="pages")


class ScriptElement(Base):
    """One typed block of script text (heading, action, cue, dialogue...).

    This is the provenance anchor: every extracted fact points at a range of
    elements. Every element is either assigned to a scene, marked as non-scene
    (title page, page header), or flagged — that is how coverage is proven.
    """

    __tablename__ = "script_elements"
    __table_args__ = (UniqueConstraint("script_version_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_version_id: Mapped[int] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    # use_alter: scenes reference elements and elements reference scenes.
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL", use_alter=True), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)                      # global order in the script
    element_type: Mapped[ElementType] = mapped_column(enum_col(ElementType))
    text: Mapped[str] = mapped_column(Text)                        # normalised text
    raw_text: Mapped[str] = mapped_column(Text)                    # exactly as imported
    line_count: Mapped[int] = mapped_column(Integer, default=1)    # rendered lines, for 1/8 pages
    # PDF geometry in points from top of page (None for structured formats).
    y_top: Mapped[float | None] = mapped_column(Float)
    y_bottom: Mapped[float | None] = mapped_column(Float)
    is_dual_dialogue: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    classification_confidence: Mapped[float] = mapped_column(Float, default=1.0)


class ExtractionRun(Base):
    """Every automated pass is recorded, so any fact can be traced to model + prompt version."""

    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_version_id: Mapped[int] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[RunKind] = mapped_column(enum_col(RunKind))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[RunStatus] = mapped_column(enum_col(RunStatus), default=RunStatus.RUNNING)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(ts_col())
