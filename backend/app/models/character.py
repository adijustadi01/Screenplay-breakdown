"""Characters (story entities), casting requirements, and scene appearances."""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, enum_col
from .enums import CastingRoleKind, CharacterKind, EntityStatus, Presence, Speaking
from .provenance import ProvenanceMixin, QuantityMixin, provenance_args


class Character(ProvenanceMixin, QuantityMixin, Base):
    """A person (or named group) in the STORY. Not a casting slot.

    "Young Arjun" and "Adult Arjun" are ONE Character with TWO CastingRoles.
    Merges are soft: status=MERGED + merged_into_id, so they can be undone.
    Quantity columns are used only when kind=GROUP.
    """

    __tablename__ = "characters"
    __table_args__ = provenance_args()

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)   # "ARJUN"
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[CharacterKind] = mapped_column(
        enum_col(CharacterKind), default=CharacterKind.INDIVIDUAL
    )
    status: Mapped[EntityStatus] = mapped_column(enum_col(EntityStatus), default=EntityStatus.ACTIVE)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"))

    aliases = relationship(
        "CharacterAlias", back_populates="character", cascade="all, delete-orphan",
        foreign_keys="CharacterAlias.character_id",
    )
    casting_roles = relationship(
        "CastingRole",
        back_populates="character",
        cascade="all, delete-orphan",
        foreign_keys="CastingRole.character_id",
    )


class CharacterAlias(ProvenanceMixin, Base):
    """Every name the script uses for a character: cue names and in-action names.

    "ARJUN", "ARJUN (CONT'D)" (normalised away), "ARJU", "INSPECTOR SHARMA" / "SHARMA".
    Linking an alias to a different character is a reconciliation decision -> review item.
    """

    __tablename__ = "character_aliases"
    __table_args__ = provenance_args(UniqueConstraint("character_id", "alias"))

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255), index=True)    # normalised upper-case
    alias_raw: Mapped[str | None] = mapped_column(String(255))
    is_cue_name: Mapped[bool] = mapped_column(default=False)        # appears as a dialogue cue

    character = relationship("Character", back_populates="aliases", foreign_keys=[character_id])


class CastingRole(ProvenanceMixin, Base):
    """A casting requirement: who must be hired to realise a character.

    Examples for character ARJUN:
      ACTOR  "Young Arjun (8)"     ACTOR  "Adult Arjun"
      VOICE  "Arjun (narrator)"    STUNT_DOUBLE "Adult Arjun stunt double" (doubles_role_id -> Adult Arjun)
    """

    __tablename__ = "casting_roles"
    __table_args__ = provenance_args()

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))                 # "Young Arjun"
    kind: Mapped[CastingRoleKind] = mapped_column(enum_col(CastingRoleKind))
    age_note: Mapped[str | None] = mapped_column(String(120))      # "8 yrs", "60s" — as written
    doubles_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("casting_roles.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    character = relationship("Character", back_populates="casting_roles", foreign_keys=[character_id])


class Appearance(ProvenanceMixin, Base):
    """Character <-> Scene link. Exactly one row per (scene, character, casting role).

    `presence` decides whether this counts as an appearance:
      PRESENT, OFF_SCREEN  -> appearance (character must be on the day)
      VOICE_ONLY           -> not an on-set appearance; a voice casting need
      MENTIONED            -> NEVER an appearance (kept so the AD can see it was considered)
      UNCERTAIN            -> not counted until reviewed; always needs_review

    dialogue_block_count is computed by the PARSER from dialogue cues, not by the LLM.
    """

    __tablename__ = "appearances"
    __table_args__ = provenance_args(
        UniqueConstraint("scene_id", "character_id", "casting_role_id"),
        CheckConstraint("dialogue_block_count >= 0", name="dialogue_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    casting_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("casting_roles.id", ondelete="SET NULL"), index=True
    )
    presence: Mapped[Presence] = mapped_column(enum_col(Presence), index=True)
    speaking: Mapped[Speaking] = mapped_column(enum_col(Speaking), default=Speaking.UNKNOWN)
    dialogue_block_count: Mapped[int] = mapped_column(Integer, default=0)
    name_as_written: Mapped[str | None] = mapped_column(String(255))  # how this scene refers to them

    scene = relationship("Scene", back_populates="appearances", foreign_keys=[scene_id])
    character = relationship("Character", foreign_keys=[character_id])
