"""The structured output every extractor (Claude or offline) must return for one scene.

The LLM fills this in; code then verifies it against the scene text and the
deterministic dialogue cues before anything is written to the database.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PresenceLit = Literal["present", "off_screen", "voice_only", "mentioned", "uncertain"]
SpeakingLit = Literal["speaking", "silent", "unknown"]


class CharacterOut(BaseModel):
    name_as_written: str = Field(description="How this scene refers to the character, e.g. 'YOUNG ARJUN', 'Dadi', 'Papa'.")
    canonical_name: str = Field(description="The character's standard name in CAPS, matching the script's cue names when possible.")
    presence: PresenceLit
    speaking: SpeakingLit
    is_group: bool = Field(description="True for a named group such as GOONS or VILLAGERS.")
    quantity_text: str | None = Field(description="Headcount wording exactly as written ('THREE GOONS', 'several of his men'), else null.")
    quantity_min: int | None = Field(description="Only if the script states a number; never estimate.")
    quantity_max: int | None
    age_or_version: str | None = Field(description="Age or version cue ('10', 'young', 'old', 'adult') if the scene gives one.")
    evidence: str = Field(description="A short VERBATIM quote from the scene that proves the presence value.")
    confidence: float = Field(description="0.0-1.0")
    uncertainty_note: str | None = None


class LocationOut(BaseModel):
    story_location: str = Field(description="The parent place in the story, e.g. \"ARJUN'S HOUSE\", 'CITY HOSPITAL'.")
    sub_location: str | None = Field(description="The specific set within it, e.g. 'KITCHEN', 'ICU'; null if none.")
    modifiers: list[str] = Field(description="Heading words that are not places: MOVING, FLASHBACK, ESTABLISHING...")
    evidence: str
    confidence: float


class SceneExtraction(BaseModel):
    characters: list[CharacterOut]
    location: LocationOut | None
    synopsis: str = Field(description="One or two sentences: what happens in the scene.")
    int_ext_suggestion: Literal["INT", "EXT", "INT/EXT", "UNKNOWN"] = Field(
        description="Only meaningful when the heading lacks INT/EXT; otherwise UNKNOWN.")
    notes: str | None = None


def json_schema() -> dict:
    """Strict JSON schema for structured outputs (every property required, no extras)."""

    def nullable(t: str) -> dict:
        return {"type": [t, "null"]}

    character = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name_as_written": {"type": "string"},
            "canonical_name": {"type": "string"},
            "presence": {"type": "string", "enum": ["present", "off_screen", "voice_only", "mentioned", "uncertain"]},
            "speaking": {"type": "string", "enum": ["speaking", "silent", "unknown"]},
            "is_group": {"type": "boolean"},
            "quantity_text": nullable("string"),
            "quantity_min": nullable("integer"),
            "quantity_max": nullable("integer"),
            "age_or_version": nullable("string"),
            "evidence": {"type": "string"},
            "confidence": {"type": "number"},
            "uncertainty_note": nullable("string"),
        },
    }
    character["required"] = list(character["properties"])
    location = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "story_location": {"type": "string"},
            "sub_location": nullable("string"),
            "modifiers": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "string"},
            "confidence": {"type": "number"},
        },
    }
    location["required"] = list(location["properties"])
    root = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "characters": {"type": "array", "items": character},
            "location": {"anyOf": [location, {"type": "null"}]},
            "synopsis": {"type": "string"},
            "int_ext_suggestion": {"type": "string", "enum": ["INT", "EXT", "INT/EXT", "UNKNOWN"]},
            "notes": nullable("string"),
        },
    }
    root["required"] = list(root["properties"])
    return root
