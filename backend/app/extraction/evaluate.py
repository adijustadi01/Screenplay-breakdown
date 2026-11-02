"""Score stored extraction results against a hand-written ground truth JSON.

    python -m app.cli accuracy <version_id> ../samples/monsoon_ledger.ground_truth.json

Scores what is in the database right now — so it measures extraction, then
reconciliation, then the AD's corrections, as each is applied.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appearance, Character, CharacterAlias, CastingRole, Location, Scene, SceneLocation
from ..models.enums import APPEARANCE_PRESENCES, EntityStatus, ReviewStatus, SceneLocationRole
from .cues import normalize_name


def _names_for(session: Session, a: Appearance) -> set[str]:
    c = session.get(Character, a.character_id)
    while c.status == EntityStatus.MERGED and c.merged_into_id:
        c = session.get(Character, c.merged_into_id)
    names = {normalize_name(c.canonical_name)}
    names |= {x.alias for x in session.scalars(select(CharacterAlias).where(CharacterAlias.character_id == c.id))}
    if a.casting_role_id:
        r = session.get(CastingRole, a.casting_role_id)
        names.add(normalize_name(r.name))
    if a.name_as_written:
        names.add(normalize_name(a.name_as_written))
    return names


def _loc_path(session: Session, scene_id: int) -> list[str] | None:
    sl = session.scalars(select(SceneLocation).where(
        SceneLocation.scene_id == scene_id, SceneLocation.role == SceneLocationRole.PRIMARY,
        SceneLocation.review_status != ReviewStatus.REJECTED)).first()
    if sl is None:
        return None
    loc = session.get(Location, sl.location_id)
    while loc.status == EntityStatus.MERGED and loc.merged_into_id:
        loc = session.get(Location, loc.merged_into_id)
    path = [loc.name]
    while loc.parent_id:
        loc = session.get(Location, loc.parent_id)
        path.insert(0, loc.name)
    return path


def evaluate(session: Session, version_id: int, truth_path: str | Path) -> dict[str, Any]:
    truth = json.loads(Path(truth_path).read_text())
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)).all()
    by_seq = {s.seq: s for s in scenes}
    tp = fp = fn = 0
    presence_ok = presence_n = speaking_ok = speaking_n = dialogue_ok = dialogue_n = 0
    mention_errors = 0
    loc_ok = loc_n = 0
    problems: list[str] = []

    for ts in truth["scenes"]:
        sc = by_seq.get(ts["seq"])
        if sc is None or ts.get("omitted"):
            continue
        label = ts.get("number") or f"#{ts['seq']}"
        apps = [a for a in session.scalars(select(Appearance).where(Appearance.scene_id == sc.id))
                if a.review_status != ReviewStatus.REJECTED]
        app_names = [(a, _names_for(session, a)) for a in apps]
        matched_ids = set()
        for tc in ts["characters"]:
            want = {normalize_name(tc["character"])} | {normalize_name(x) for x in tc.get("aliases", [])}
            if tc.get("casting"):
                want.add(normalize_name(tc["casting"]))
            hit = next((a for a, names in app_names if names & want and a.id not in matched_ids), None)
            t_app = tc["presence"] in ("present", "off_screen")
            if hit is None:
                if t_app:
                    fn += 1
                    problems.append(f"sc {label}: MISSED {tc['character']} ({tc['presence']})")
                continue
            matched_ids.add(hit.id)
            d_app = hit.presence in APPEARANCE_PRESENCES
            if t_app and d_app:
                tp += 1
            elif t_app and not d_app:
                fn += 1
                problems.append(f"sc {label}: {tc['character']} should be {tc['presence']}, got {hit.presence.value}")
            elif d_app and not t_app:
                fp += 1
                if tc["presence"] == "mentioned":
                    mention_errors += 1
                problems.append(f"sc {label}: {tc['character']} is {tc['presence']}, wrongly {hit.presence.value}")
            presence_n += 1
            if hit.presence.value == tc["presence"]:
                presence_ok += 1
            elif not (t_app != d_app):
                problems.append(f"sc {label}: {tc['character']} presence {hit.presence.value} vs truth {tc['presence']}")
            if tc["presence"] != "mentioned":
                speaking_n += 1
                speaking_ok += hit.speaking.value == tc["speaking"]
                dialogue_n += 1
                if hit.dialogue_block_count == tc.get("dialogue", 0):
                    dialogue_ok += 1
                else:
                    problems.append(f"sc {label}: {tc['character']} dialogue {hit.dialogue_block_count} "
                                    f"vs truth {tc.get('dialogue', 0)}")
        for a, names in app_names:
            if a.id not in matched_ids and a.presence in APPEARANCE_PRESENCES:
                fp += 1
                problems.append(f"sc {label}: EXTRA {sorted(names)[0]} ({a.presence.value})")
        if ts.get("location"):
            loc_n += 1
            got = _loc_path(session, sc.id)
            want = [normalize_name(x) for x in ts["location"]]
            if got == want:
                loc_ok += 1
            else:
                problems.append(f"sc {label}: location {got} vs truth {want}")

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    return {
        "appearances": {"true_positive": tp, "false_positive": fp, "false_negative": fn,
                        "precision": pct(tp, tp + fp), "recall": pct(tp, tp + fn)},
        "presence_accuracy": pct(presence_ok, presence_n),
        "speaking_accuracy": pct(speaking_ok, speaking_n),
        "dialogue_count_accuracy": pct(dialogue_ok, dialogue_n),
        "mentioned_counted_as_present": mention_errors,
        "location_accuracy": pct(loc_ok, loc_n),
        "problems": problems,
    }


def format_accuracy(r: dict[str, Any]) -> str:
    a = r["appearances"]
    lines = [
        "EXTRACTION ACCURACY vs ground truth",
        f"  appearances: precision {a['precision']}%  recall {a['recall']}%  "
        f"(TP {a['true_positive']}, FP {a['false_positive']}, FN {a['false_negative']})",
        f"  presence value correct: {r['presence_accuracy']}%",
        f"  speaking/silent correct: {r['speaking_accuracy']}%",
        f"  dialogue counts correct: {r['dialogue_count_accuracy']}%",
        f"  mentioned characters wrongly counted as on set: {r['mentioned_counted_as_present']}",
        f"  location (parent > set) correct: {r['location_accuracy']}%",
        "",
        f"  {len(r['problems'])} discrepancies:" if r["problems"] else "  no discrepancies",
        *[f"    - {p}" for p in r["problems"]],
    ]
    return "\n".join(lines)

