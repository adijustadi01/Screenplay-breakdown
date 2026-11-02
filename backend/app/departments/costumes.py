"""Costumes: what each character wears, changes, uniforms, period and dance wear,
and continuity states (wet / torn / bloodied...) that need multiples."""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext
from .continuity_words import COSTUME_STATES, state_from

STATES = ("clean", "wet", "damp", "torn", "bloodied", "dirty", "burnt", "powdered", "dishevelled")
NEEDS_MULTIPLES = {"wet", "torn", "bloodied", "dirty", "burnt", "powdered"}


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.details.get("state") in NEEDS_MULTIPLES:
        reasons.append("continuity_state")      # multiples / breakdown work
    if not item.characters and not item.quantity_max:
        reasons.append("wearer_not_identified")
    return reasons


def _extra(row: dict, occs: list) -> dict:
    states = [o.details.get("state") for o in occs if o.details.get("state")]
    return {"wearers": sorted({c["name"] for c in row["characters"] if c["role"] == "wearer"}),
            "states": sorted(set(states)), "needs_multiples": any(s in NEEDS_MULTIPLES for s in states),
            "period": next((o.details.get("period") for o in occs if o.details.get("period")), None)}


SPEC = DepartmentSpec(
    key=Department.COSTUMES,
    label="Costumes",
    item_noun="costume",
    categories=("everyday", "uniform", "period", "traditional", "formal", "disguise", "dance", "accessory",
                "nightwear", "sportswear"),
    roles=("wearer",),
    owner_roles=("wearer",),
    default_role="wearer",
    name_with_holder=True,
    details=(
        DetailField("state", "string", "Continuity state in this scene.", STATES),
        DetailField("change_note", "string", "What changed since the character was last seen, if stated."),
        DetailField("period", "string", "Period/era if not contemporary (e.g. '1970s')."),
        DetailField("multiples_needed", "boolean", "Damage, wetting, blood or stunts mean several copies."),
    ),
    prompt="""
What each character wears when the scene describes it, plus group wardrobe for background
(e.g. "DANCERS — WHITE KURTAS", with the quantity). Include uniforms, period clothing, dance wear,
disguises and accessories worn (dupatta, gold chain worn as jewellery, mask).
- Name costumes "<CHARACTER> — <GARMENT>" ("TARA — RED SALWAR KAMEEZ") and keep the same name for
  the same costume in later scenes; a different outfit is a different item (a costume change).
- `details.state` records continuity: wet, damp, torn, bloodied, dirty, burnt, powdered... A garment
  carried over from an earlier scene in a new state ("still in his damp jacket") is the same item
  with a new state.
""",
    lexicon={
        "everyday": ("leather jacket", "jacket", "shirt", "t-shirt", "trousers", "jeans", "sweater", "coat"),
        "uniform": ("khaki uniform", "uniform", "khaki", "police uniform", "school uniform", "scrubs"),
        "period": ("bell-bottoms", "wide-collared shirt", "dhoti"),
        "traditional": ("salwar kameez", "salwar", "saree", "sari", "kurta", "kurtas", "lehenga", "sherwani"),
        "dance": ("ghagra", "white kurtas"),
        "disguise": ("saffron robes", "robes", "priest's robes"),
        "accessory": ("dupatta", "scarf", "turban", "cap", "mask", "gold chain"),
    },
    offline_details=lambda s: state_from(s, COSTUME_STATES),
    review=_review,
    report_extra=_extra,
)
