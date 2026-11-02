"""Composable structured queries over the breakdown.

    "every exterior night scene featuring the hero"
        -> int_ext=[EXT], times=[NIGHT], character_ids=[hero]
    "at the Hospital location, scenes per character split day/night"
        -> location_ids=[hospital], rows=character, columns=time

Filters AND together; values inside one filter OR together. A location filter
includes all of its sub-locations. Every cell carries its scene ids.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..reports.facts import Facts, load_facts
from ..reports.service import IE_ORDER, TIME_ORDER, _order, cell, scene_ref


class QueryFilters(BaseModel):
    location_ids: list[int] = Field(default_factory=list)
    int_ext: list[str] = Field(default_factory=list)
    times: list[str] = Field(default_factory=list)
    time_mode: Literal["as_written", "effective"] = "as_written"
    character_ids: list[int] = Field(default_factory=list)
    character_match: Literal["all", "any"] = "all"
    include_off_screen: bool = True
    include_voice_only: bool = False
    include_omitted: bool = False
    rows: Literal["none", "character", "location", "story_location", "int_ext", "time"] = "none"
    columns: Literal["none", "time", "int_ext"] = "none"


def _presences(q: QueryFilters) -> set[str]:
    p = {"present"}
    if q.include_off_screen:
        p.add("off_screen")
    if q.include_voice_only:
        p.add("voice_only")
    return p


def describe(q: QueryFilters, f: Facts) -> str:
    parts = []
    parts.append(" or ".join(q.int_ext) + " scenes" if q.int_ext else "Scenes")
    if q.times:
        parts.append("at " + " or ".join(q.times)
                     + (" (inherited lighting included)" if q.time_mode == "effective" else " (as written)"))
    if q.location_ids:
        parts.append("at " + " or ".join(f.loc_label(i) for i in q.location_ids) + " (incl. sub-locations)")
    if q.character_ids:
        names = [f.characters[c].canonical_name for c in q.character_ids if c in f.characters]
        joiner = " and " if q.character_match == "all" else " or "
        parts.append("featuring " + joiner.join(names))
    s = ", ".join(parts)
    if q.rows != "none" or q.columns != "none":
        s += f"; counted by {q.rows if q.rows != 'none' else 'total'}" + (
            f" × {q.columns}" if q.columns != "none" else "")
    return s


def run_query(session: Session, version_id: int, q: QueryFilters) -> dict[str, Any]:
    f = load_facts(session, version_id)
    pres = _presences(q)
    cast: dict[int, set[int]] = defaultdict(set)
    for a in f.appearances:
        if a.presence in pres and a.character_id in f.characters:
            cast[a.scene_id].add(a.character_id)

    want_locs = set(q.location_ids)
    matched = []
    for s in f.scenes:
        if s.omitted and not q.include_omitted:
            continue
        if q.int_ext and s.int_ext not in q.int_ext:
            continue
        if q.times and s.time(q.time_mode) not in q.times:
            continue
        if want_locs and not (want_locs & set(s.location_path)):
            continue
        if q.character_ids:
            have = cast.get(s.id, set())
            ok = set(q.character_ids) <= have if q.character_match == "all" else bool(set(q.character_ids) & have)
            if not ok:
                continue
        matched.append(s)

    def row_keys(s) -> list[str]:
        if q.rows == "none":
            return ["All matching scenes"]
        if q.rows == "character":
            ids = cast.get(s.id, set())
            if q.character_ids:
                ids = ids & set(q.character_ids) if q.character_match == "any" else ids
            return [f.characters[c].canonical_name for c in ids] or []
        if q.rows == "location":
            return [f.loc_label(s.location_id)]
        if q.rows == "story_location":
            return [f.loc_label(s.location_path[0]) if s.location_path else "(no location)"]
        if q.rows == "int_ext":
            return [s.int_ext]
        return [s.time(q.time_mode)]

    def col_key(s) -> str:
        if q.columns == "time":
            return s.time(q.time_mode)
        if q.columns == "int_ext":
            return s.int_ext
        return "Scenes"

    table: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    cols = set()
    for s in matched:
        c = col_key(s)
        cols.add(c)
        for r in row_keys(s):
            table[r][c].append(s.id)
    columns = _order(cols, TIME_ORDER if q.columns == "time" else IE_ORDER)
    rows = sorted(table, key=lambda r: (-sum(len(v) for v in table[r].values()), r))
    return {
        "description": describe(q, f),
        "filters": q.model_dump(),
        "total": cell(s.id for s in matched),
        "eighths": sum(s.eighths for s in matched),
        "table": {
            "rows": rows, "columns": columns,
            "cells": {r: {c: cell(table[r].get(c, [])) for c in columns} for r in rows},
            "row_totals": {r: cell(x for v in table[r].values() for x in v) for r in rows},
            "column_totals": {c: cell(x for r in rows for x in table[r].get(c, [])) for c in columns},
        },
        "scenes": [scene_ref(s, f) for s in matched],
    }


def query_options(session: Session, version_id: int) -> dict[str, Any]:
    """What the filter dropdowns should offer for this script."""
    f = load_facts(session, version_id)
    used_chars = {a.character_id for a in f.appearances}
    return {
        "characters": sorted(({"id": c.id, "name": c.canonical_name} for cid, c in f.characters.items()
                              if cid in used_chars), key=lambda x: x["name"]),
        "locations": sorted(({"id": lid, "label": f.loc_label(lid), "depth": len(f.loc_label(lid).split(" > ")) - 1}
                             for lid in f.locations), key=lambda x: x["label"]),
        "int_ext": _order({s.int_ext for s in f.scenes}, IE_ORDER),
        "times_as_written": _order({s.time_written for s in f.scenes}, TIME_ORDER),
        "times_effective": _order({s.time_effective for s in f.scenes}, TIME_ORDER),
    }
