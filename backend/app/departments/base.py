"""The contract every department module fills in."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..models.enums import Department


@dataclass(frozen=True)
class DetailField:
    """A department-specific per-scene fact. Every detail is optional (null = not stated)."""

    name: str
    type: str          # "string" | "boolean" | "integer" | "string_list"
    description: str
    choices: tuple[str, ...] | None = None


@dataclass
class ItemOut:
    """One department item found in one scene (what extractors return)."""

    name: str
    category: str
    description: str | None = None
    quantity_text: str | None = None
    quantity_min: int | None = None
    quantity_max: int | None = None
    characters: list[dict[str, str]] = field(default_factory=list)   # [{"name":..., "role":...}]
    details: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    confidence: float = 0.5
    inferred: bool = False
    uncertainty_note: str | None = None
    extracted_by: str | None = None    # override (e.g. "parser" for code-derived items)


@dataclass
class SceneContext:
    """What a department rule can see about one scene (Stage 1 facts + text)."""

    scene_id: int
    label: str
    int_ext: str
    time: str
    time_effective: str
    location_label: str
    text: str
    action_text: str
    present: set[str]                  # canonical names of characters on set
    voice_only: set[str]
    elements: list[Any]


@dataclass
class DepartmentSpec:
    key: Department
    label: str
    item_noun: str
    categories: tuple[str, ...]
    roles: tuple[str, ...]
    details: tuple[DetailField, ...]
    prompt: str
    quantity_matters: bool = False           # unknown headcount -> flagged
    # Offline rules: category -> list of regex fragments (word-ish) found in action lines.
    lexicon: dict[str, tuple[str, ...]] = field(default_factory=dict)
    default_role: str | None = None
    # Roles that make a character the item's owner (costume wearer, car's driver...).
    owner_roles: tuple[str, ...] = ()
    # Extra review reasons for one item in one scene (department judgement in code).
    review: Callable[[ItemOut, SceneContext], list[str]] | None = None
    # Deterministic items derived in code from the scene (e.g. V.O. cues -> voiceover).
    derive: Callable[[SceneContext, dict[Department, list[ItemOut]]], list[ItemOut]] | None = None
    # Extra per-item report fields: f(item_row, occurrences) -> dict
    report_extra: Callable[[dict, list], dict] | None = None

    def detail_names(self) -> set[str]:
        return {d.name for d in self.details}


def item_schema(spec: DepartmentSpec) -> dict:
    """Strict JSON schema for one department's items (all properties required, nullable)."""

    def nullable(t):
        return {"type": [t, "null"]}

    def detail_schema(d: DetailField):
        if d.type == "string_list":
            return {"type": ["array", "null"], "items": {"type": "string"}}
        if d.choices:
            return {"type": ["string", "null"], "enum": [*d.choices, None]}
        return nullable(d.type)

    details = {"type": "object", "additionalProperties": False,
               "properties": {d.name: detail_schema(d) for d in spec.details}}
    details["required"] = list(details["properties"])
    character = {"type": "object", "additionalProperties": False,
                 "properties": {"name": {"type": "string"},
                                "role": {"type": "string", "enum": list(spec.roles)}},
                 "required": ["name", "role"]}
    item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "category": {"type": "string", "enum": [*spec.categories, "other"]},
            "description": nullable("string"),
            "quantity_text": nullable("string"),
            "quantity_min": nullable("integer"),
            "quantity_max": nullable("integer"),
            "characters": {"type": "array", "items": character},
            "details": details,
            "evidence": {"type": "string"},
            "confidence": {"type": "number"},
            "inferred": {"type": "boolean"},
            "uncertainty_note": nullable("string"),
        },
    }
    item["required"] = list(item["properties"])
    return {"type": "array", "items": item}
