"""Production requirements: script-triggered needs — special equipment, set construction,
complex crowds, safety, permits.

Two layers:
  * stated in the script (crane, drone, a built replica set)      -> from the extractor
  * inferred from the other departments' facts for the scene      -> derived here, in code,
    always marked is_inferred and sent to review as "INFERRED — review".
"""
from __future__ import annotations

import re

from ..models.enums import Department
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext

_AGE_RE = re.compile(r"\b([A-Z][A-Z .'’-]{1,40}?)\s*\((\d{1,2})\)")
_HEIGHT_RE = re.compile(r"\b(?:rooftop|roof edge|edge of the (?:roof|building|cliff|terrace|parapet)|cliff|ledge|"
                        r"parapet|scaffold\w*)\b", re.I)
CROWD_CATS = {"crowd", "villagers", "dancers", "passersby", "vendors", "guests", "students", "military"}

# Canonical names for inferred requirements (stable across scenes so reports group them).
RAIN_NIGHT = "RAIN TOWERS FOR NIGHT EXTERIOR"
RAIN = "RAIN EFFECTS RIG"
VEHICLE_RIG = "VEHICLE CAMERA RIG / TRACKING VEHICLE"
CROWD = "CROWD MANAGEMENT"
ANIMAL = "ANIMAL HANDLER / WELFARE OFFICER"
ARMOURER = "ARMOURER"
STUNTS = "STUNT COORDINATOR"
SFX = "SFX / PYROTECHNICS SUPERVISOR AND FIRE SAFETY"
SONG = "CHOREOGRAPHER AND PLAYBACK SYSTEM"
MINOR = "MINOR ON SET — PERMITS AND CHAPERONE"
HEIGHT = "SAFETY RIGGING AT HEIGHT"
WATER = "WATER SAFETY"


def _inferred(name, category, why, evidence) -> ItemOut:
    return ItemOut(name=name, category=category, description=why, details={"reason": why},
                   evidence=evidence, confidence=0.5, inferred=True, uncertainty_note=why, extracted_by="rules")


def _derive(ctx: SceneContext, by_dept) -> list[ItemOut]:
    g = lambda d: by_dept.get(d, [])  # noqa: E731
    stated = " ".join(i.name for i in g(Department.PRODUCTION) if not i.inferred).upper()
    out: list[ItemOut] = []

    def add(name, category, why, evidence, skip_if=()):
        if any(k in stated for k in skip_if):
            return
        if all(o.name != name for o in out):
            out.append(_inferred(name, category, why, evidence))

    for e in g(Department.ACTION_EFFECTS):
        if e.category == "rain":
            night_ext = ctx.int_ext in ("EXT", "INT/EXT") and "NIGHT" in (ctx.time, ctx.time_effective)
            add(RAIN_NIGHT if night_ext else RAIN, "weather_effects",
                "Rain in the scene" + (" at night, exterior" if night_ext else ""), e.evidence, ("RAIN",))
        if e.category in ("fight", "fall", "chase", "crash", "stunt"):
            add(STUNTS, "safety", f"{e.category} in the scene", e.evidence, ("STUNT",))
        if e.category in ("fire", "explosion"):
            add(SFX, "safety", f"{e.category} in the scene", e.evidence, ("PYRO", "SFX"))
        if e.category == "gunfire":
            add(ARMOURER, "safety", "gunfire in the scene", e.evidence, ("ARMOURER",))
        if e.category == "water":
            add(WATER, "water_work", "water/flooding in the scene", e.evidence, ("WATER",))
    for p in g(Department.PROPS):
        if p.category == "weapon":
            add(ARMOURER, "safety", f"weapon on set: {p.name}", p.evidence, ("ARMOURER",))
    for v in g(Department.VEHICLES):
        has_actor = any(c.get("role") in ("driver", "passenger") for c in v.characters)
        if v.details.get("moving") and has_actor:
            add(VEHICLE_RIG, "vehicle_rig", f"actors in a moving vehicle ({v.name})", v.evidence,
                ("RIG", "TRACKING", "PROCESS"))
    for b in g(Department.BACKGROUND):
        if b.category in CROWD_CATS and (b.quantity_max is None or b.quantity_max >= 50):
            add(CROWD, "crowd_management",
                f"{b.name}: {'headcount unknown' if b.quantity_max is None else b.quantity_max}", b.evidence,
                ("CROWD",))
    if g(Department.ANIMALS):
        a = g(Department.ANIMALS)[0]
        add(ANIMAL, "animal_welfare", "animals: " + ", ".join(x.name for x in g(Department.ANIMALS)), a.evidence,
            ("ANIMAL", "WRANGLER"))
    for s in g(Department.SOUND_MUSIC):
        if s.category == "song" and s.details.get("playback"):
            add(SONG, "special_equipment", f"song sequence with playback ({s.name})", s.evidence,
                ("CHOREOGRAPH", "PLAYBACK"))
    for m in _AGE_RE.finditer(ctx.action_text):
        if int(m.group(2)) < 18:
            add(MINOR, "child_performer", f"{m.group(1).strip()} is {m.group(2)}", m.group(0), ("MINOR", "CHILD"))
    hm = _HEIGHT_RE.search(ctx.action_text) or _HEIGHT_RE.search(ctx.location_label)
    if hm:
        add(HEIGHT, "safety", f"working at height ({hm.group(0)})", hm.group(0), ("HEIGHT", "RIGGING"))
    return out


def _extra(row: dict, occs: list) -> dict:
    return {"inferred_only": all(o.inferred for o in occs),
            "reasons": sorted({o.details.get("reason") for o in occs if o.details.get("reason")})[:5]}


SPEC = DepartmentSpec(
    key=Department.PRODUCTION,
    label="Production requirements",
    item_noun="requirement",
    categories=("special_equipment", "camera_rig", "set_construction", "crowd_management", "safety", "permits",
                "weather_effects", "animal_welfare", "child_performer", "vehicle_rig", "water_work", "location_lock",
                "night_shoot"),
    roles=("affected",),
    details=(
        DetailField("reason", "string", "What in the scene triggers the requirement."),
        DetailField("triggered_by", "string", "Department or script words that trigger it."),
    ),
    prompt="""
Production needs the scene triggers beyond the other departments: special camera equipment the
script names (crane, drone/aerial, underwater, Steadicam), set construction or alteration described
in the text (a built replica, a burnt set), road/location closures, permits, complex crowd logistics.
- Stated in the script → `inferred: false`.
- Not stated but likely (e.g. rain towers for a night rain scene) → `inferred: true` with the reason in
  `uncertainty_note`. These are reviewed by the AD; do not overstate them.
""",
    lexicon={
        "camera_rig": ("crane up", "crane", "drone", "aerial", "steadicam", "underwater", "helicopter shot"),
        "set_construction": ("full replica", "replica", "built on the backlot", "backlot"),
    },
    derive=_derive,
    report_extra=_extra,
)
