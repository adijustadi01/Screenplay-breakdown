"""Stage 3: revision tracking between two imported drafts of the same project."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, enum_col, ts_col, utcnow
from .enums import LineageStatus, MatchMethod


class ScriptRevision(Base):
    """One comparison: draft `from_version` -> revised draft `to_version`."""

    __tablename__ = "script_revisions"
    __table_args__ = (UniqueConstraint("from_version_id", "to_version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    from_version_id: Mapped[int] = mapped_column(ForeignKey("script_versions.id", ondelete="CASCADE"))
    to_version_id: Mapped[int] = mapped_column(ForeignKey("script_versions.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SceneLineage(Base):
    """Scene-to-scene link between two drafts (one row per new scene, plus one per removed scene).

    Reviewable: a low-similarity match is flagged and the AD can re-match by hand.
    """

    __tablename__ = "scene_lineage"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("script_revisions.id", ondelete="CASCADE"), index=True)
    old_scene_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    new_scene_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    status: Mapped[LineageStatus] = mapped_column(enum_col(LineageStatus), index=True)
    method: Mapped[MatchMethod] = mapped_column(enum_col(MatchMethod))
    similarity: Mapped[float | None] = mapped_column(Float)      # 0..1 normalised text similarity
    renumbered: Mapped[bool] = mapped_column(Boolean, default=False)
    heading_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    length_delta_eighths: Mapped[int | None] = mapped_column()
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(255))
    carried_forward: Mapped[bool] = mapped_column(Boolean, default=False)  # facts copied from old scene
    note: Mapped[str | None] = mapped_column(Text)
