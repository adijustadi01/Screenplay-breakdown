"""Story locations (hierarchical) and shooting venues (kept separate)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, enum_col
from .enums import EntityStatus, LocationKind, SceneLocationRole
from .provenance import ProvenanceMixin, provenance_args


class Location(ProvenanceMixin, Base):
    """A place in the STORY. Self-referencing tree of any depth:

        ARJUN'S HOUSE (story_location)
          ├── KITCHEN (set)
          └── BEDROOM (set)

    Reports roll children up to parents on request ("all scenes at ARJUN'S HOUSE").
    `venue_id` (where it is actually shot) is filled in later by the location dept.
    """

    __tablename__ = "locations"
    __table_args__ = provenance_args(UniqueConstraint("project_id", "parent_id", "name"))

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))                 # "KITCHEN"
    kind: Mapped[LocationKind] = mapped_column(enum_col(LocationKind))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EntityStatus] = mapped_column(enum_col(EntityStatus), default=EntityStatus.ACTIVE)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("shooting_venues.id", ondelete="SET NULL")
    )

    parent = relationship("Location", remote_side=[id], foreign_keys=[parent_id], backref="children")
    aliases = relationship(
        "LocationAlias", back_populates="location", cascade="all, delete-orphan",
        foreign_keys="LocationAlias.location_id",
    )


class LocationAlias(ProvenanceMixin, Base):
    """Every heading spelling that maps to a location: "ARJUNS HOUSE - KITCHEN",
    "ARJUN'S HOUSE (KITCHEN)", "KITCHEN - ARJUN'S HOUSE". Mapping variants together is a
    reconciliation decision -> review item."""

    __tablename__ = "location_aliases"
    __table_args__ = provenance_args(UniqueConstraint("location_id", "alias"))

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255), index=True)

    location = relationship("Location", back_populates="aliases", foreign_keys=[location_id])


class SceneLocation(ProvenanceMixin, Base):
    """Scene <-> Location. Usually one PRIMARY row; INTERCUT scenes have more."""

    __tablename__ = "scene_locations"
    __table_args__ = provenance_args(UniqueConstraint("scene_id", "location_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    role: Mapped[SceneLocationRole] = mapped_column(
        enum_col(SceneLocationRole), default=SceneLocationRole.PRIMARY
    )

    scene = relationship("Scene", back_populates="scene_locations", foreign_keys=[scene_id])
    location = relationship("Location", foreign_keys=[location_id])


class ShootingVenue(Base):
    """A real-world place where sets are shot. Empty in Stage 1; used by scheduling (Stage 3)."""

    __tablename__ = "shooting_venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
