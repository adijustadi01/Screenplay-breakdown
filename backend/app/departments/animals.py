"""Animals: every animal on screen, what it must do, and a handler/welfare assessment flag."""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    # Every animal needs a handler and welfare assessment before it can be scheduled.
    reasons = ["handler_assessment"]
    if item.details.get("trained_behaviour"):
        reasons.append("trained_behaviour")
    return reasons


def _extra(row: dict, occs: list) -> dict:
    return {"species": row["category"],
            "trained_behaviour": any(o.details.get("trained_behaviour") for o in occs),
            "actions": sorted({o.details["action"] for o in occs if o.details.get("action")}),
            "handler_assessment_required": True}


SPEC = DepartmentSpec(
    key=Department.ANIMALS,
    label="Animals",
    item_noun="animal",
    categories=("dog", "horse", "cow", "buffalo", "goat", "cat", "bird", "snake", "elephant", "monkey",
                "camel", "fish", "insect"),
    roles=("handler", "owner", "rider"),
    owner_roles=("owner",),
    quantity_matters=False,
    details=(
        DetailField("action", "string", "What the animal must do (stand, bark, gallop, wander past...)."),
        DetailField("trained_behaviour", "boolean", "Needs a trained action beyond being present."),
        DetailField("breed", "string", "Breed/colour if stated (golden retriever, white horse)."),
        DetailField("decoration", "string", "Costume/decoration on the animal, if stated."),
    ),
    prompt="""
Every animal that appears: pets, livestock, strays, wildlife, ridden or decorated animals.
- One item per animal or group ("DECORATED WHITE HORSE", "STRAY DOGS"); quantities only when stated.
- `details.action` and `trained_behaviour` tell the wrangler what must be trained.
- `characters`: the owner or rider if stated.
""",
    lexicon={
        "dog": ("golden retriever", "retriever", "stray dogs", "dogs", "dog", "puppy"),
        "horse": ("white horse", "horse", "horses", "pony"),
        "cow": ("cow", "cows", "bull"),
        "buffalo": ("buffaloes", "buffalo"),
        "goat": ("goat", "goats"),
        "cat": ("cat", "cats"),
        "bird": ("pigeons", "pigeon", "parrot", "crow", "crows", "birds"),
        "snake": ("snake", "cobra"),
        "elephant": ("elephant",),
        "monkey": ("monkey", "monkeys"),
    },
    review=_review,
    report_extra=_extra,
)
