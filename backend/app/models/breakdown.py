"""Stage 2: departmental breakdown items, one generic model for every department.

    BreakdownItem        WHAT the thing is, across the whole script (identity)
                         "ARJUN'S MARUTI", "BRASS KEY", "MEERA — WHITE COTTON SAREE"
    BreakdownOccurrence  the item IN ONE SCENE: how many (QuantityMixin; NULL = unknown),
                         scene-specific details (moving? wet? torn? practical or VFX?),
                         source passage, confidence, review status
    OccurrenceCharacter  who is involved and how: driver, passenger, user, wearer, performer...

Identity vs quantity: one recurring jeep in 10 scenes is ONE item with 10 occurrences of
quantity 1; three police jeeps together is one item with an occurrence of quantity 3.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, enum_col
from .enums import Department, EntityStatus
from .provenance import ProvenanceMixin, QuantityMixin, provenance_args


class BreakdownItem(ProvenanceMixin, Base):
    __tablename__ = "breakdown_items"
    __table_args__ = provenance_args(UniqueConstraint("project_id", "department", "name"))

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    department: Mapped[Department] = mapped_column(enum_col(Department), index=True)
    name: Mapped[str] = mapped_column(String(255))            # normalised, upper case
    category: Mapped[str | None] = mapped_column(String(64), index=True)  # e.g. "motorcycle", "weapon"
    description: Mapped[str | None] = mapped_column(Text)
    # Whose it is (a character's costume, makeup, signature prop or car), when known.
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"),
                                                     index=True)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON)   # department-specific, stable facts
    status: Mapped[EntityStatus] = mapped_column(enum_col(EntityStatus), default=EntityStatus.ACTIVE)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("breakdown_items.id", ondelete="SET NULL"))

    occurrences = relationship("BreakdownOccurrence", back_populates="item",
                               foreign_keys="BreakdownOccurrence.item_id")


class BreakdownOccurrence(ProvenanceMixin, QuantityMixin, Base):
    __tablename__ = "breakdown_occurrences"
    __table_args__ = provenance_args(UniqueConstraint("scene_id", "item_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("breakdown_items.id", ondelete="CASCADE"), index=True)
    department: Mapped[Department] = mapped_column(enum_col(Department), index=True)  # denormalised for queries
    description: Mapped[str | None] = mapped_column(Text)    # how it appears in THIS scene
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)   # validated per department
    # Not stated in the script but inferred (e.g. "rain at night -> rain towers"). Always reviewed.
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    item = relationship("BreakdownItem", back_populates="occurrences", foreign_keys=[item_id])
    characters = relationship("OccurrenceCharacter", back_populates="occurrence", cascade="all, delete-orphan")


class OccurrenceCharacter(Base):
    __tablename__ = "occurrence_characters"
    __table_args__ = (UniqueConstraint("occurrence_id", "character_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("breakdown_occurrences.id", ondelete="CASCADE"),
                                               index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))   # driver, passenger, user, wearer, performer, handler...

    occurrence = relationship("BreakdownOccurrence", back_populates="characters")
