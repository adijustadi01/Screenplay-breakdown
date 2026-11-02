"""Vehicles: picture vehicles, who drives / rides, what the vehicle has to do.

Identity vs quantity: the item is the vehicle's identity ("KHAN'S POLICE JEEP");
the occurrence quantity is how many units are in that scene ("THREE POLICE JEEPS"
is one item with quantity 3). The report's "peak units in one scene" is what the
transport department must have on set at once.
"""
from __future__ import annotations

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext

ACTIONS = ("parked", "driving", "chase", "crash", "stunt", "towed", "process", "arrives", "departs")


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.details.get("action") in ("chase", "crash", "stunt"):
        reasons.append("vehicle_action")       # stunt driving / precision driver / rigging
    if item.details.get("moving") and not any(c.get("role") == "driver" for c in item.characters) \
            and item.category not in ("aircraft", "boat"):
        if item.quantity_max in (None, 1):
            reasons.append("driver_not_identified")
    return reasons


def _extra(row: dict, occs: list) -> dict:
    drivers = sorted({c["name"] for c in row["characters"] if c["role"] == "driver"})
    moving = [o.scene_id for o in occs if o.details.get("moving")]
    actions = sorted({o.details["action"] for o in occs if o.details.get("action")})
    return {"drivers": drivers, "moving_scenes": len(set(moving)), "actions": actions,
            "condition": next((o.details.get("condition") for o in occs if o.details.get("condition")), None),
            "period": next((o.details.get("period") for o in occs if o.details.get("period")), None)}


SPEC = DepartmentSpec(
    key=Department.VEHICLES,
    label="Vehicles",
    item_noun="vehicle",
    categories=("car", "motorcycle", "jeep", "police_vehicle", "truck", "bus", "van", "auto_rickshaw", "taxi",
                "ambulance", "bicycle", "boat", "aircraft", "animal_drawn", "train"),
    roles=("driver", "passenger", "owner"),
    owner_roles=("owner", "driver"),
    default_role="driver",
    details=(
        DetailField("moving", "boolean", "Is the vehicle moving in this scene?"),
        DetailField("action", "string", "What the vehicle does in this scene.", ACTIONS),
        DetailField("make_model", "string", "Make/model if stated (e.g. 'Royal Enfield', 'Ambassador')."),
        DetailField("condition", "string", "Condition as written (battered, rusting, burnt...)."),
        DetailField("period", "string", "Period/year if the scene is set in another era."),
    ),
    prompt="""
Every vehicle the scene shows or uses: picture vehicles, vehicles characters drive or ride, vehicles
parked in frame that the action refers to. Not vehicles only mentioned in dialogue.
- Name the vehicle by identity, not by scene: the hero's motorbike is the same item in every scene
  ("RAGHAV'S ROYAL ENFIELD"); a generic group is one item with a quantity ("POLICE JEEPS", 3).
- `characters`: who drives (driver), who rides (passenger), whose it is (owner) when stated.
- `details.moving` and `details.action` drive camera-rig and stunt-driving needs, so set them when
  the scene says so (chase, crash, parked...).
""",
    lexicon={
        "motorcycle": ("motorcycle", "motorbike", "royal enfield", "bike", "scooter"),
        "police_vehicle": ("police jeep", "police jeeps", "police car", "police van"),
        "jeep": ("jeep", "jeeps"),
        "car": ("car", "ambassador", "maruti", "sedan", "taxi"),
        "truck": ("truck", "lorry", "tanker"),
        "bus": ("bus",),
        "auto_rickshaw": ("auto-rickshaw", "autorickshaw", "rickshaw"),
        "ambulance": ("ambulance", "ambulances"),
        "bicycle": ("bicycle", "cycle"),
        "boat": ("boat",),
    },
    review=_review,
    report_extra=_extra,
)
