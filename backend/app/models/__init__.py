"""The data model (see DATA_MODEL.md). Every extracted fact carries provenance."""
from ..db.base import Base
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
]
