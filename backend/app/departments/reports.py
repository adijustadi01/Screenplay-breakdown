"""Department reports, computed in code from the reviewed facts (same Cell convention:
every count carries its scene ids)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from ..models.enums import Department
from ..reports.facts import Facts, OccurrenceFact, load_facts
from ..reports.service import cell
from . import registry


def _live_occ(f: Facts, department: str | None = None) -> list[OccurrenceFact]:
    live = {s.id for s in f.scenes if not s.omitted}
    return [o for o in f.occurrences if o.scene_id in live and (department is None or o.department == department)]


def department_report(f: Facts, department: str, time_mode: str = "as_written") -> dict[str, Any]:
    spec = registry.SPECS[Department(department)]
    occs = _live_occ(f, department)
    by_item: dict[int, list[OccurrenceFact]] = defaultdict(list)
    for o in occs:
        by_item[o.item_id].append(o)
    rows = []
    for iid, os_ in by_item.items():
        sids = sorted({o.scene_id for o in os_}, key=lambda i: f.scene_by_id[i].seq)
        by_time, by_ie, chars = defaultdict(list), defaultdict(list), defaultdict(list)
        for o in os_:
            s = f.scene_by_id[o.scene_id]
            by_time[s.time(time_mode)].append(s.id)
            by_ie[s.int_ext].append(s.id)
            for cid, role in o.characters:
                if cid in f.characters:
                    chars[(cid, role)].append(o.scene_id)
        known = [o.quantity_max for o in os_ if o.quantity_max is not None]
        first = f.scene_by_id[sids[0]]
        row = {
            "item_id": iid, "name": os_[0].name, "category": os_[0].category,
            "owner": f.characters[os_[0].owner_id].canonical_name if os_[0].owner_id in f.characters else None,
            "scenes": cell(sids),
            "max_in_one_scene": max(known) if known else None,
            "unknown_quantity": cell(o.scene_id for o in os_ if o.quantity_max is None and o.quantity_min is None
                                     and (spec.quantity_matters or o.quantity_raw)),
            "inferred": cell(o.scene_id for o in os_ if o.inferred),
            "needs_review": cell(o.scene_id for o in os_ if o.needs_review),
            "first_scene": first.number or f"#{first.seq}",
            "by_time": {k: cell(v) for k, v in by_time.items()},
            "by_int_ext": {k: cell(v) for k, v in by_ie.items()},
            "characters": sorted(({"character_id": cid, "name": f.characters[cid].canonical_name, "role": role}
                                  | cell(v) for (cid, role), v in chars.items()),
                                 key=lambda r: (-r["count"], r["name"])),
        }
        if spec.report_extra:
            row.update(spec.report_extra(row, os_))
        rows.append(row)
    rows.sort(key=lambda r: (-r["scenes"]["count"], r["name"]))
    # Peak simultaneous units: per scene, sum the known quantities (1 when an item is listed without a count).
    per_scene: dict[int, int] = defaultdict(int)
    unknown_scenes = set()
    for o in occs:
        if o.quantity_max is not None:
            per_scene[o.scene_id] += o.quantity_max
        elif spec.quantity_matters or o.quantity_raw:
            unknown_scenes.add(o.scene_id)
        else:
            per_scene[o.scene_id] += 1
    peak_scene = max(per_scene, key=per_scene.get) if per_scene else None
    return {
        "department": department, "label": spec.label, "item_noun": spec.item_noun, "time_mode": time_mode,
        "unique_items": len(rows),
        "scenes": cell(o.scene_id for o in occs),
        "by_category": {k: cell(o.scene_id for o in occs if (o.category or "other") == k)
                        for k in sorted({o.category or "other" for o in occs})},
        "peak_units_in_one_scene": (
            {"count": per_scene[peak_scene], "scene_id": peak_scene,
             "scene": f.scene_by_id[peak_scene].number or f"#{f.scene_by_id[peak_scene].seq}"}
            if peak_scene else None),
        "scenes_with_unknown_quantity": cell(unknown_scenes),
        "inferred": cell(o.scene_id for o in occs if o.inferred),
        "needs_review": cell(o.scene_id for o in occs if o.needs_review),
        "items": rows,
    }


def continuity(f: Facts, departments: tuple[str, ...] = ("costumes", "makeup_hair")) -> dict[str, Any]:
    """Per character, in story order: what they wear / how they look in each scene, and where it changes."""
    occs = [o for o in _live_occ(f) if o.department in departments]
    per_char: dict[int, dict[int, list[OccurrenceFact]]] = defaultdict(lambda: defaultdict(list))
    for o in occs:
        holders = {cid for cid, _ in o.characters} | ({o.owner_id} if o.owner_id else set())
        for cid in holders:
            if cid in f.characters:
                per_char[cid][o.scene_id].append(o)
    out = []
    for cid, scenes in per_char.items():
        app_scenes = sorted({a.scene_id for a in f.appearances if a.character_id == cid and a.is_appearance
                             and not f.scene_by_id[a.scene_id].omitted}, key=lambda i: f.scene_by_id[i].seq)
        timeline, prev = [], None
        for sid in app_scenes:
            s = f.scene_by_id[sid]
            entries = sorted([{"department": o.department, "item": o.name, "state": o.details.get("state"),
                               "note": o.description, "occurrence_id": o.id} for o in scenes.get(sid, [])],
                             key=lambda e: (e["department"], e["item"]))
            sig = tuple((e["department"], e["item"], e["state"]) for e in entries)
            timeline.append({"scene_id": sid, "scene": s.number or f"#{s.seq}", "time": s.time_written,
                             "entries": entries, "changed": prev is not None and sig != prev,
                             "nothing_recorded": not entries})
            prev = sig
        # Injuries/looks that appear and then go unrecorded in later scenes with the character:
        # not necessarily wrong (healed, flashback), but worth a check.
        checks = []
        seen_from: dict[str, int] = {}
        for idx, t in enumerate(timeline):
            for e in t["entries"]:
                if e["department"] == "makeup_hair" and e["state"] not in ("healed", "removed"):
                    seen_from.setdefault(e["item"], idx)
            for item, start in seen_from.items():
                if idx > start and not any(e["item"] == item for e in t["entries"]):
                    checks.append({"scene_id": t["scene_id"], "scene": t["scene"], "item": item,
                                   "message": f"{item} not recorded in sc {t['scene']} — healed, hidden, or missed?"})
        out.append({"character_id": cid, "name": f.characters[cid].canonical_name,
                    "changes": sum(1 for t in timeline if t["changed"]), "timeline": timeline,
                    "continuity_checks": checks})
    out.sort(key=lambda r: r["name"])
    return {"departments": list(departments), "characters": out}


def scene_sheet(f: Facts, scene_id: int) -> dict[str, Any]:
    """Everything every department needs for one scene (a breakdown sheet)."""
    out = {}
    for o in _live_occ(f):
        if o.scene_id != scene_id:
            continue
        out.setdefault(o.department, []).append({
            "occurrence_id": o.id, "item_id": o.item_id, "name": o.name, "category": o.category,
            "quantity_min": o.quantity_min, "quantity_max": o.quantity_max, "quantity_raw": o.quantity_raw,
            "details": o.details, "inferred": o.inferred, "needs_review": o.needs_review,
            "description": o.description,
            "characters": [{"name": f.characters[c].canonical_name, "role": r} for c, r in o.characters
                           if c in f.characters]})
    return {k.value: out.get(k.value, []) for k in registry.SPECS}


def run_department_report(session: Session, version_id: int, name: str, time_mode: str = "as_written") -> dict:
    f = load_facts(session, version_id)
    if name == "continuity":
        return continuity(f)
    return department_report(f, name, time_mode)

