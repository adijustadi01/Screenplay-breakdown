"""Deterministic reports and cross-counts.

Every count is returned as a Cell: {"count": n, "scene_ids": [...]} so the UI
can open the exact scenes behind any number. Omitted scenes are excluded from
all counts (they are listed separately in the scene report).
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

from sqlalchemy.orm import Session

from .facts import Facts, SceneFact, load_facts

TIME_ORDER = ["DAY", "MORNING", "AFTERNOON", "EVENING", "DAWN", "SUNRISE", "DUSK", "SUNSET", "NIGHT",
              "CONTINUOUS", "SAME", "LATER", "MOMENTS LATER", "OTHER", "UNSPECIFIED"]
IE_ORDER = ["INT", "EXT", "INT/EXT", "UNKNOWN"]


def cell(scene_ids) -> dict[str, Any]:
    ids = sorted(set(scene_ids))
    return {"count": len(ids), "scene_ids": ids}


def _order(keys, order):
    return sorted(keys, key=lambda k: (order.index(k) if k in order else len(order), k))


def _live(f: Facts) -> list[SceneFact]:
    return [s for s in f.scenes if not s.omitted]


def scene_ref(s: SceneFact, f: Facts) -> dict[str, Any]:
    return {"id": s.id, "seq": s.seq, "number": s.number, "heading": s.heading, "int_ext": s.int_ext,
            "time": s.time_written, "time_effective": s.time_effective, "time_inferred": s.time_inferred,
            "eighths": s.eighths, "location": f.loc_label(s.location_id), "omitted": s.omitted}


# ------------------------------------------------------------------------------------ scenes
def scene_report(f: Facts, time_mode: str = "as_written") -> dict[str, Any]:
    live = _live(f)
    by_ie, by_time, by_both = defaultdict(list), defaultdict(list), defaultdict(list)
    eighths_ie, eighths_time = defaultdict(int), defaultdict(int)
    for s in live:
        t = s.time(time_mode)
        by_ie[s.int_ext].append(s.id)
        by_time[t].append(s.id)
        by_both[(s.int_ext, t)].append(s.id)
        eighths_ie[s.int_ext] += s.eighths
        eighths_time[t] += s.eighths
    times = _order(by_time, TIME_ORDER)
    ies = _order(by_ie, IE_ORDER)
    return {
        "time_mode": time_mode,
        "total": cell(s.id for s in live),
        "omitted": cell(s.id for s in f.scenes if s.omitted),
        "total_eighths": sum(s.eighths for s in live),
        "needs_review": cell(s.id for s in live if s.needs_review),
        "inferred_time": cell(s.id for s in live if s.time_inferred),
        "by_int_ext": {k: cell(by_ie[k]) | {"eighths": eighths_ie[k]} for k in ies},
        "by_time": {k: cell(by_time[k]) | {"eighths": eighths_time[k]} for k in times},
        "int_ext_by_time": {"rows": ies, "columns": times,
                            "cells": {ie: {t: cell(by_both[(ie, t)]) for t in times} for ie in ies}},
        "scenes": [scene_ref(s, f) for s in f.scenes],
    }


# -------------------------------------------------------------------------------- characters
def character_report(f: Facts, time_mode: str = "as_written") -> dict[str, Any]:
    live_ids = {s.id for s in _live(f)}
    rows = []
    by_char: dict[int, list] = defaultdict(list)
    for a in f.appearances:
        if a.scene_id in live_ids:
            by_char[a.character_id].append(a)
    for cid, apps in by_char.items():
        c = f.characters.get(cid)
        if c is None:
            continue
        app = [a for a in apps if a.is_appearance]
        app_scenes = sorted({a.scene_id for a in app}, key=lambda i: f.scene_by_id[i].seq)
        first = f.scene_by_id[app_scenes[0]] if app_scenes else None
        last = f.scene_by_id[app_scenes[-1]] if app_scenes else None
        by_time = defaultdict(list)
        for sid in app_scenes:
            by_time[f.scene_by_id[sid].time(time_mode)].append(sid)
        roles = sorted({f.casting_roles[a.casting_role_id].name for a in apps
                        if a.casting_role_id in f.casting_roles})
        rows.append({
            "by_role": _by_role(f, apps) if roles else [],
            "character_id": cid, "name": c.canonical_name, "kind": c.kind.value, "casting_roles": roles,
            "appearances": cell(app_scenes),
            "on_screen": cell(a.scene_id for a in app if a.presence == "present"),
            "off_screen": cell(a.scene_id for a in app if a.presence == "off_screen"),
            "voice_only": cell(a.scene_id for a in apps if a.presence == "voice_only"),
            "mentioned_only": cell(a.scene_id for a in apps if a.presence == "mentioned"),
            "uncertain": cell(a.scene_id for a in apps if a.presence == "uncertain"),
            "speaking": cell(a.scene_id for a in apps if a.speaking == "speaking"),
            "silent": cell(a.scene_id for a in app if a.speaking == "silent"),
            "dialogue_blocks": sum(a.dialogue for a in apps),
            "eighths": sum(f.scene_by_id[s].eighths for s in app_scenes),
            "first_appearance": {"scene_id": first.id, "number": first.number, "seq": first.seq} if first else None,
            "last_appearance": {"scene_id": last.id, "number": last.number, "seq": last.seq} if last else None,
            "by_time": {k: cell(v) for k, v in by_time.items()},
            "unreviewed": cell(a.scene_id for a in apps if a.needs_review),
        })
    rows.sort(key=lambda r: (-r["appearances"]["count"], -r["dialogue_blocks"], r["name"]))
    speaking_chars = [r for r in rows if r["speaking"]["count"] > 0]
    return {
        "time_mode": time_mode,
        "unique_characters": len([r for r in rows if r["appearances"]["count"] or r["voice_only"]["count"]]),
        "speaking_characters": len(speaking_chars),
        "silent_only_characters": len([r for r in rows if r["appearances"]["count"] and not r["speaking"]["count"]]),
        "mentioned_only_characters": len([r for r in rows if not r["appearances"]["count"]
                                          and not r["voice_only"]["count"] and r["mentioned_only"]["count"]]),
        "characters": rows,
    }


def _by_role(f: Facts, apps: list) -> list[dict[str, Any]]:
    """One character's scenes split by casting role (CHILD / YOUNG / MIDDLE-AGED actor...).

    The character's own row still counts every scene once; these rows show how many scenes
    each actor has. Scenes not yet given a role are listed as "Role not chosen yet"."""
    groups: dict[int | None, list] = defaultdict(list)
    for a in apps:
        if a.is_appearance or (a.casting_role_id in f.casting_roles and a.presence == "voice_only"):
            groups[a.casting_role_id if a.casting_role_id in f.casting_roles else None].append(a)
    out = []
    for rid, group in groups.items():
        sids = sorted({a.scene_id for a in group}, key=lambda i: f.scene_by_id[i].seq)
        first, last = f.scene_by_id[sids[0]], f.scene_by_id[sids[-1]]
        role = f.casting_roles.get(rid) if rid is not None else None
        out.append({
            "casting_role_id": rid, "name": role.name if role else "Role not chosen yet",
            "kind": role.kind.value if role else None, "age_note": role.age_note if role else None,
            "appearances": cell(sids), "eighths": sum(f.scene_by_id[s].eighths for s in sids),
            "speaking": cell(a.scene_id for a in group if a.speaking == "speaking"),
            "dialogue_blocks": sum(a.dialogue for a in group),
            "first_appearance": {"scene_id": first.id, "number": first.number, "seq": first.seq},
            "last_appearance": {"scene_id": last.id, "number": last.number, "seq": last.seq},
        })
    out.sort(key=lambda r: (r["casting_role_id"] is None, f.scene_by_id[r["first_appearance"]["scene_id"]].seq))
    return out


# --------------------------------------------------------------------------------- locations
def location_report(f: Facts, time_mode: str = "as_written") -> dict[str, Any]:
    live = _live(f)
    nodes: dict[int, dict] = {}

    def node(lid: int) -> dict:
        if lid not in nodes:
            loc = f.locations[lid]
            nodes[lid] = {"location_id": lid, "name": loc.name, "label": f.loc_label(lid), "kind": loc.kind.value,
                          "parent_id": loc.parent_id, "_direct": [], "_all": []}
        return nodes[lid]

    no_loc = []
    for s in live:
        if not s.location_path:
            no_loc.append(s.id)
            continue
        node(s.location_id)["_direct"].append(s.id)
        for lid in s.location_path:          # roll up to every ancestor
            node(lid)["_all"].append(s.id)
    apps_by_scene = defaultdict(list)
    for a in f.appearances:
        if a.is_appearance:
            apps_by_scene[a.scene_id].append(a.character_id)
    out = []
    for lid, n in nodes.items():
        sids = n.pop("_all")
        direct = n.pop("_direct")
        by_ie, by_time, chars = defaultdict(list), defaultdict(list), defaultdict(list)
        for sid in sids:
            s = f.scene_by_id[sid]
            by_ie[s.int_ext].append(sid)
            by_time[s.time(time_mode)].append(sid)
            for cid in apps_by_scene.get(sid, []):
                chars[cid].append(sid)
        n.update({
            "scenes": cell(sids), "scenes_here_only": cell(direct),
            "eighths": sum(f.scene_by_id[s].eighths for s in set(sids)),
            "by_int_ext": {k: cell(by_ie[k]) for k in _order(by_ie, IE_ORDER)},
            "by_time": {k: cell(by_time[k]) for k in _order(by_time, TIME_ORDER)},
            "characters": sorted(
                [{"character_id": cid, "name": f.characters[cid].canonical_name} | cell(v)
                 for cid, v in chars.items() if cid in f.characters],
                key=lambda r: (-r["count"], r["name"])),
        })
        out.append(n)
    out.sort(key=lambda n: n["label"])
    return {
        "time_mode": time_mode,
        "story_locations": len([n for n in out if n["parent_id"] is None]),
        "sets": len([n for n in out if n["parent_id"] is not None]),
        "locations": out,
        "scenes_without_location": cell(no_loc),
    }


# ------------------------------------------------------------------------------ combinations
def combination_report(f: Facts, size: int = 2, min_scenes: int = 1, time_mode: str = "as_written",
                       character_ids: list[int] | None = None) -> dict[str, Any]:
    live_ids = {s.id for s in _live(f)}
    cast: dict[int, set[int]] = defaultdict(set)
    for a in f.appearances:
        if a.is_appearance and a.scene_id in live_ids and a.character_id in f.characters:
            cast[a.scene_id].add(a.character_id)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for sid, cids in cast.items():
        pool = sorted(cids)
        if character_ids:
            if not set(character_ids) <= cids:
                continue
        for combo in combinations(pool, size):
            if character_ids and not set(character_ids) <= set(combo):
                continue
            groups[combo].append(sid)
    rows = []
    for combo, sids in groups.items():
        if len(sids) < min_scenes:
            continue
        by_time, by_loc = defaultdict(list), defaultdict(list)
        for sid in sids:
            s = f.scene_by_id[sid]
            by_time[s.time(time_mode)].append(sid)
            by_loc[f.loc_label(s.location_id)].append(sid)
        rows.append({
            "character_ids": list(combo), "names": [f.characters[c].canonical_name for c in combo],
            "together": cell(sids),
            "by_time": {k: cell(v) for k, v in by_time.items()},
            "by_location": {k: cell(v) for k, v in sorted(by_loc.items())},
        })
    rows.sort(key=lambda r: (-r["together"]["count"], r["names"]))
    return {"size": size, "time_mode": time_mode, "combinations": rows}


# -------------------------------------------------------------------------------- cross-tabs
def crosstab(f: Facts, kind: str, time_mode: str = "as_written") -> dict[str, Any]:
    """kind: character_time | location_time | character_location_time | location_int_ext."""
    live = {s.id: s for s in _live(f)}
    cells: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    cols = set()
    if kind in ("character_time", "character_location_time"):
        for a in f.appearances:
            if not a.is_appearance or a.scene_id not in live or a.character_id not in f.characters:
                continue
            s = live[a.scene_id]
            t = s.time(time_mode)
            name = f.characters[a.character_id].canonical_name
            row = name if kind == "character_time" else f"{name} @ {f.loc_label(s.location_id)}"
            cells[row][t].append(s.id)
            cols.add(t)
    elif kind in ("location_time", "location_int_ext"):
        for s in live.values():
            col = s.time(time_mode) if kind == "location_time" else s.int_ext
            for lid in s.location_path or [None]:
                cells[f.loc_label(lid)][col].append(s.id)
            cols.add(col)
    else:
        raise ValueError(f"unknown crosstab {kind!r}")
    order = IE_ORDER if kind == "location_int_ext" else TIME_ORDER
    columns = _order(cols, order)
    rows = sorted(cells)
    return {
        "kind": kind, "time_mode": time_mode, "rows": rows, "columns": columns,
        "cells": {r: {c: cell(cells[r].get(c, [])) for c in columns} for r in rows},
        "row_totals": {r: cell(sid for v in cells[r].values() for sid in v) for r in rows},
    }


REPORTS = {"scenes": scene_report, "characters": character_report, "locations": location_report}


def run_report(session: Session, version_id: int, name: str, **kw) -> dict[str, Any]:
    f = load_facts(session, version_id)
    if name in REPORTS:
        return REPORTS[name](f, **kw)
    if name == "combinations":
        return combination_report(f, **kw)
    if name.startswith("crosstab:"):
        return crosstab(f, name.split(":", 1)[1], **kw)
    raise ValueError(f"unknown report {name!r}")


def scenes_detail(session: Session, version_id: int, scene_ids: list[int]) -> list[dict[str, Any]]:
    f = load_facts(session, version_id)
    return [scene_ref(f.scene_by_id[i], f) for i in sorted(scene_ids, key=lambda i: f.scene_by_id[i].seq)
            if i in f.scene_by_id]
