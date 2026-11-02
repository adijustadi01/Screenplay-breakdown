"""The data model (see DATA_MODEL.md). Every extracted fact carries provenance."""
from ..db.base import Base
from .breakdown import BreakdownItem, BreakdownOccurrence, OccurrenceCharacter
from .revision import SceneLineage, ScriptRevision
from .schedule import Schedule, SceneStoryDay, ScheduledScene, ShootingDay
from .character import Appearance, CastingRole, Character, CharacterAlias
from .location import Location, LocationAlias, SceneLocation, ShootingVenue
from .review import ReviewEvent, ReviewItem
from .scene import Scene
from .script import ExtractionRun, Page, Project, ScriptElement, ScriptVersion

__all__ = [
    "Base", "Project", "ScriptVersion", "Page", "ScriptElement", "ExtractionRun",
    "Scene", "Character", "CharacterAlias", "CastingRole", "Appearance",
    "Location", "LocationAlias", "SceneLocation", "ShootingVenue",
    "ReviewItem", "ReviewEvent",
    "BreakdownItem", "BreakdownOccurrence", "OccurrenceCharacter",
    "ScriptRevision", "SceneLineage", "Schedule", "ShootingDay", "ScheduledScene", "SceneStoryDay",
]
