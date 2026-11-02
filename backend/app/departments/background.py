"""Background performers: crowds, police, staff, guests, dancers... Headcounts only when the
script states them; "a huge crowd" stays unknown and is flagged."""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext

LARGE = 50


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if (item.quantity_max or 0) >= LARGE:
        reasons.append("large_crowd")              # crowd management, holding area, catering
    if item.details.get("featured"):
        reasons.append("featured_background")      # may need casting / a special agreement
    return reasons


def _extra(row: dict, occs: list) -> dict:
    known = [o.quantity_max for o in occs if o.quantity_max is not None]
    return {"headcount": {"max_known": max(known) if known else None,
                          "scenes_unknown": sum(1 for o in occs if o.quantity_max is None)},
            "featured": any(o.details.get("featured") for o in occs),
            "wardrobe": next((o.details.get("wardrobe_note") for o in occs if o.details.get("wardrobe_note")), None)}


SPEC = DepartmentSpec(
    key=Department.BACKGROUND,
    label="Background performers",
    item_noun="background group",
    categories=("crowd", "villagers", "police", "medical_staff", "staff", "guests", "dancers", "musicians",
                "passersby", "passengers", "patients", "students", "vendors", "military", "henchmen"),
    roles=("interacts_with",),
    quantity_matters=True,
    details=(
        DetailField("featured", "boolean", "Singled out / does specific business on camera."),
        DetailField("action", "string", "What they do (cheer, dance, sleep on the floor...)."),
        DetailField("wardrobe_note", "string", "Wardrobe stated for the group (white kurtas, uniforms...)."),
        DetailField("age_gender_note", "string", "Age/gender mix if stated (children, old men...)."),
    ),
    prompt="""
Non-speaking people the scene needs who are not named characters: crowds, shoppers, villagers,
police, nurses, patients, guests, dancers, musicians, passers-by, henchmen ("his men").
- One item per distinct group ("DANCERS", "DHOL PLAYERS", "VILLAGERS"); keep names consistent.
- Headcount: only when stated ("two hundred dancers" → 200). "A crowd", "families", "several of his
  men" → leave quantities null and copy the words into `quantity_text`. Never estimate a crowd.
- Named or speaking people are characters, not background — don't list them here.
""",
    lexicon={
        "crowd": ("huge crowd", "crowd", "onlookers", "spectators", "mob"),
        "villagers": ("villagers",),
        "dancers": ("dancers",),
        "musicians": ("dhol players", "musicians", "band"),
        "police": ("constables", "policemen", "police officers", "cops"),
        "medical_staff": ("nurses", "doctors", "ward boys"),
        "patients": ("patients",),
        "vendors": ("hawkers", "vendors", "shoppers"),
        "passersby": ("families", "passers-by", "pedestrians", "commuters"),
        "guests": ("guests", "wedding guests"),
        "henchmen": ("his men", "henchmen", "goons", "thugs"),
    },
    review=_review,
    report_extra=_extra,
)
