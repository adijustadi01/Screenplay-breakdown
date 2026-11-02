"""Makeup & hair: injuries, blood, ageing, prosthetics, disguises, hairstyle changes —
with continuity across scenes (fresh cut -> bandaged -> healed)."""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext
from .continuity_words import MAKEUP_STATES, state_from

STATES = ("fresh", "healing", "bandaged", "healed", "applied", "removed")
SPECIAL = {"prosthetic", "ageing", "disguise", "wig_facial_hair"}


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.category in SPECIAL:
        reasons.append("special_makeup")          # prosthetics/ageing: lead time, tests, artist
    if not item.characters and not item.quantity_max:
        reasons.append("wearer_not_identified")
    return reasons


def _extra(row: dict, occs: list) -> dict:
    return {"wearers": sorted({c["name"] for c in row["characters"] if c["role"] == "wearer"}),
            "states": [o.details.get("state") for o in sorted(occs, key=lambda o: o.scene_id) if o.details.get("state")],
            "special": row["category"] in SPECIAL}


SPEC = DepartmentSpec(
    key=Department.MAKEUP_HAIR,
    label="Makeup & hair",
    item_noun="makeup/hair look",
    categories=("injury", "blood", "ageing", "prosthetic", "disguise", "hairstyle", "wig_facial_hair",
                "special", "sweat_dirt", "beauty", "tattoo"),
    roles=("wearer",),
    owner_roles=("wearer",),
    default_role="wearer",
    name_with_holder=True,
    details=(
        DetailField("state", "string", "Continuity state of this look in the scene.", STATES),
        DetailField("body_area", "string", "Where (forehead, cheek, hands, hair...)."),
        DetailField("continuity_note", "string", "What must match or change versus other scenes."),
    ),
    prompt="""
Makeup and hair the scene calls for: injuries and blood, bruises and scars, ageing, prosthetics,
disguises (fake beards, wigs), hairstyle changes, powder or dirt on faces, sweat, tattoos.
- Name each look "<CHARACTER> — <LOOK>" ("RAGHAV — FOREHEAD CUT") and reuse the name while it lasts,
  so continuity can be tracked: the same cut is "fresh" when it happens and "bandaged" later.
- An older/younger version of a character played with makeup is an "ageing" look on that character.
""",
    lexicon={
        "injury": ("cut", "wound", "bruise", "bruised", "black eye", "graze", "burn", "bandaged"),
        "blood": ("blood", "bloodied"),
        "prosthetic": ("prosthetic scar", "prosthetic", "scar"),
        "ageing": ("grey-haired", "deeply wrinkled", "wrinkled", "wrinkles", "aged"),
        "disguise": ("fake beard", "disguise"),
        "hairstyle": ("long braid", "braid", "shaved head", "haircut"),
        "wig_facial_hair": ("wig", "moustache", "beard"),
        "special": ("pink powder", "powder"),
        "sweat_dirt": ("sweat", "grime", "mud-streaked"),
    },
    offline_details=lambda s: state_from(s, MAKEUP_STATES),
    review=_review,
    report_extra=_extra,
)
