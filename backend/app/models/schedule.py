"""Stage 3: scheduling foundation.

STORY DAY vs SHOOTING DAY — two different things, kept in different tables:
  * SceneStoryDay: when the scene happens IN THE STORY (story day 3, or a flashback).
    Inferred from the script, always reviewable. Thirty night scenes may all be story night 2.
  * ShootingDay / ScheduledScene: when the scene is FILMED (shoot day 14, 12 March).
    Set by the AD. The DOOD report is computed from these only.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, ts_col, utcnow
from .provenance import ProvenanceMixin, provenance_args


class SceneStoryDay(ProvenanceMixin, Base):
    __tablename__ = "scene_story_days"
    __table_args__ = provenance_args(UniqueConstraint("scene_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    story_day: Mapped[int | None] = mapped_column(Integer)        # 1, 2, 3... in story order
    label: Mapped[str] = mapped_column(String(64))                # "Day 2", "Night 2", "Flashback (1975)"
    is_flashback: Mapped[bool] = mapped_column(Boolean, default=False)
    basis: Mapped[str | None] = mapped_column(String(255))        # why this day was assigned


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_version_id: Mapped[int] = mapped_column(ForeignKey("script_versions.id", ondelete="CASCADE"),
                                                   index=True)
    name: Mapped[str] = mapped_column(String(120))
    drop_after_days: Mapped[int] = mapped_column(Integer, default=10)  # gap that turns holds into drop/pickup
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(ts_col(), default=utcnow)

    days = relationship("ShootingDay", back_populates="schedule", cascade="all, delete-orphan",
                        order_by="ShootingDay.day_number")


class ShootingDay(Base):
    __tablename__ = "shooting_days"
    __table_args__ = (UniqueConstraint("schedule_id", "day_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)
    day_number: Mapped[int] = mapped_column(Integer)        # shoot day 1..N, in shooting order
    shoot_date: Mapped[date | None] = mapped_column(Date)
    label: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)

    schedule = relationship("Schedule", back_populates="days")
    scenes = relationship("ScheduledScene", back_populates="day", cascade="all, delete-orphan",
                          order_by="ScheduledScene.position")


class ScheduledScene(Base):
    __tablename__ = "scheduled_scenes"
    __table_args__ = (UniqueConstraint("shooting_day_id", "scene_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shooting_day_id: Mapped[int] = mapped_column(ForeignKey("shooting_days.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # A scene split across days records how much is shot here (eighths); NULL = the whole scene.
    portion_eighths: Mapped[int | None] = mapped_column(Integer)

    day = relationship("ShootingDay", back_populates="scenes")
