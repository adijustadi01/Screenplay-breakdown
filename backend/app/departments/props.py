"""Props: objects characters handle — phones, documents, weapons, food, jewellery..."""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.category == "weapon":
        reasons.append("weapon")                      # armourer / safety sign-off
    if item.details.get("consumable") and item.quantity_max is None:
        reasons.append("consumable_quantity")         # how many takes' worth?
    return reasons


def _extra(row: dict, occs: list) -> dict:
    users = sorted({c["name"] for c in row["characters"] if c["role"] in ("user", "holder")})
    return {"users": users,
            "hero_prop": any(o.details.get("hero_prop") for o in occs),
            "consumable": any(o.details.get("consumable") for o in occs),
            "actions": sorted({o.details["action"] for o in occs if o.details.get("action")})}


SPEC = DepartmentSpec(
    key=Department.PROPS,
    label="Props",
    item_noun="prop",
    categories=("phone", "document", "weapon", "food_drink", "jewellery", "bag", "money", "key", "photograph",
                "tool", "medical", "religious", "stationery", "electronics", "restraint"),
    roles=("user", "holder", "owner"),
    owner_roles=("owner",),
    default_role="user",
    details=(
        DetailField("action", "string", "What is done with it (answers, reads, fires, eats, hands over...)."),
        DetailField("hero_prop", "boolean", "Featured / seen in close-up or plot-important."),
        DetailField("consumable", "boolean", "Used up on each take (food, drink, cigarettes, breakaways)."),
        DetailField("condition", "string", "Condition as written (bloodied, broken, old...)."),
    ),
    prompt="""
Objects characters handle or that the action depends on: phones, letters and documents, weapons,
food and drink, jewellery, bags, money, keys, photographs, tools, medical equipment, religious items.
Not costume (worn clothing → costumes), not vehicles, not set dressing nobody touches.
- `characters`: who uses or holds it (user / holder), whose it is (owner) when stated.
- Weapons must be listed even if only held, and food/drink marked `consumable`.
- Keep one name per object across scenes ("VIKRAM'S LEDGER", "BRASS KEY").
""",
    lexicon={
        "phone": ("phone", "mobile", "cellphone", "telephone"),
        "document": ("ledger", "file", "files", "letter", "newspaper", "chart", "document", "documents", "notebook",
                     "diary", "map"),
        "weapon": ("revolver", "pistol", "gun", "rifle", "knife", "sword", "lathi", "dagger", "axe"),
        "food_drink": ("chai", "tea", "guava", "mango", "biscuits", "peas", "tiffin", "plate", "cups", "bottle",
                       "glass"),
        "jewellery": ("gold chain", "necklace", "bangles", "earrings", "mangalsutra", "wedding ring"),
        "bag": ("bag", "school bag", "suitcase", "briefcase"),
        "money": ("money", "cash", "notes", "wallet"),
        "key": ("key",),
        "photograph": ("photograph", "photo", "framed photograph", "picture"),
        "tool": ("scissors", "hammer", "rope", "torch", "pebble"),
        "medical": ("iv drip", "drip", "heart monitor", "monitor", "monitors", "stretcher", "syringe"),
        "religious": ("brass lamp", "lamp", "diya", "rosary"),
        "restraint": ("handcuffed", "handcuffs"),
        "other": ("umbrella", "mirror", "fuel drum", "coloured powder", "steel bowl", "bowl"),
    },
    review=_review,
    report_extra=_extra,
)
