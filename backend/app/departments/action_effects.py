"""Action & effects: fights, falls, chases, crashes, fire, rain, explosions...
Each item carries separate practical / potential-VFX flags; undecided hazards are flagged."""
from __future__ import annotations

import re

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext

HAZARD = {"fight", "fall", "chase", "crash", "stunt", "fire", "explosion", "gunfire", "water"}
DECIDE = {"fire", "explosion", "crash", "gunfire", "rain", "weather", "smoke", "wind", "water"}
STUNT = {"fight", "fall", "chase", "crash", "stunt"}


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.category in HAZARD:
        reasons.append("safety_review")
    if item.category in DECIDE and item.details.get("practical") is None and item.details.get("vfx_candidate") is None:
        reasons.append("practical_or_vfx_undecided")
    if item.category in STUNT and item.characters and item.details.get("stunt_double_needed") is None:
        reasons.append("stunt_double_undecided")
    return reasons


def _offline(sentence: str) -> dict:
    d = {}
    if re.search(r"\b(?:CGI|VFX|digital|green ?screen)\b", sentence, re.I):
        d["vfx_candidate"] = True
    return d


def _extra(row: dict, occs: list) -> dict:
    return {"practical": sum(1 for o in occs if o.details.get("practical")),
            "vfx_candidate": sum(1 for o in occs if o.details.get("vfx_candidate")),
            "undecided": sum(1 for o in occs if o.details.get("practical") is None
                             and o.details.get("vfx_candidate") is None),
            "stunt_double_needed": any(o.details.get("stunt_double_needed") for o in occs),
            "performers": sorted({c["name"] for c in row["characters"] if c["role"] == "performer"})}


SPEC = DepartmentSpec(
    key=Department.ACTION_EFFECTS,
    label="Action & effects",
    item_noun="action/effect",
    categories=("fight", "fall", "chase", "crash", "stunt", "fire", "explosion", "gunfire", "rain", "wind",
                "smoke", "water", "weather", "lighting_effect", "blood_effect", "breakaway"),
    roles=("performer", "victim"),
    default_role="performer",
    details=(
        DetailField("practical", "boolean", "Can/should be done for real on set (rain towers, real fire)."),
        DetailField("vfx_candidate", "boolean", "Likely needs or benefits from VFX (CG explosion, digital rain)."),
        DetailField("stunt_double_needed", "boolean", "A stunt performer/double is needed for a character."),
        DetailField("safety_note", "string", "Hazard as written (height, fire, water, moving vehicle)."),
    ),
    prompt="""
Physical action and effects: fights, falls, chases, crashes, stunts, gunfire, fire, explosions, smoke,
rain, wind, storms, floods, lighting effects (flickering lights, lightning), blood effects, breakaways.
- One item per distinct action or effect ("RAIN", "FIGHT ON SLOPE", "FUEL DRUM EXPLOSION").
- `characters`: who performs it or is hit by it.
- Set `practical` and `vfx_candidate` separately and only from the script; leave null when the
  script gives no indication — the AD and VFX supervisor will decide.
""",
    lexicon={
        "rain": ("rain pours", "pouring rain", "rain", "drizzle", "downpour", "new rain"),
        "weather": ("thunder", "lightning", "storm"),
        "wind": ("wind", "breeze", "gale"),
        "fire": ("ball of fire", "fire", "flames", "blaze"),
        "explosion": ("explodes", "explosion", "blast", "blows up"),
        "smoke": ("smoke",),
        "fight": ("fight", "fights", "punches", "kicks", "brawl"),
        "fall": ("falls", "fall", "tumbles", "rolls to a stop"),
        "chase": ("gives chase", "chase", "chases"),
        "crash": ("crash", "crashes", "collides"),
        "gunfire": ("fires at", "shoots", "gunfire"),
        "water": ("flooded", "flood", "ankle-deep"),
        "lighting_effect": ("flickering", "flicker"),
    },
    offline_details=_offline,
    offline_category_names=("fight", "fall", "chase", "crash", "fire", "explosion", "gunfire", "rain", "wind",
                            "smoke", "water", "weather", "lighting_effect"),
    review=_review,
    report_extra=_extra,
)
