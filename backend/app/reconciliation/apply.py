"""Apply (and undo) accepted reconciliation proposals.

Each apply_* returns an `undo` record that is stored on the review item, so a
merge can be reversed exactly. Every change is written to the audit log.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..extraction.cues import normalize_name
from ..models import (
    Appearance,
    CastingRole,
    Character,
    CharacterAlias,
    Location,
    LocationAlias,
    Scene,
    SceneLocation,
)
from ..models.enums import (
    APPEARANCE_PRESENCES,
    CastingRoleKind,
    EntityStatus,
    ExtractorKind,
    IntExt,
    LocationKind,
)
from ..review.audit import log_event, snapshot


class ProposalError(ValueError):
    pass


def _resolve_char(session: Session, cid: int) -> Character:
    c = session.get(Character, cid)
    if c is None:
        raise ProposalError(f"character {cid} not found")
    while c.status == EntityStatus.MERGED and c.merged_into_id:
        c = session.get(Character, c.merged_into_id)
    return c


def _ensure_alias(session: Session, c: Character, alias: str, actor_kind=ExtractorKind.RECONCILIATION) -> None:
    a = normalize_name(alias)
    if not session.scalars(select(CharacterAlias).where(CharacterAlias.character_id == c.id,
                                                        CharacterAlias.alias == a)).first():
        session.add(CharacterAlias(character_id=c.id, alias=a, alias_raw=alias, extracted_by=actor_kind))


def merge_character(session: Session, from_id: int, into_id: int, actor: str,
                    casting_role_id: int | None = None) -> dict[str, Any]:
    src, dst = session.get(Character, from_id), _resolve_char(session, into_id)
    if src is None or src.id == dst.id:
        raise ProposalError("nothing to merge")
    if src.status == EntityStatus.MERGED:
        raise ProposalError(f"{src.canonical_name} is already merged")
    before = snapshot(src)
    moved, removed = [], []
    dst_by_scene = {}
    for a in session.scalars(select(Appearance).where(Appearance.character_id == dst.id)):
        dst_by_scene.setdefault((a.scene_id, a.casting_role_id), a)
    for a in session.scalars(select(Appearance).where(Appearance.character_id == src.id)).all():
        role = casting_role_id if casting_role_id is not None else a.casting_role_id
        other = dst_by_scene.get((a.scene_id, role))
        if other is not None:
            # Same person twice in one scene: keep one row, combining the evidence.
            removed.append({"snap": snapshot(a), "other_id": other.id,
                            "other_before": {"presence": other.presence.value, "speaking": other.speaking.value,
                                             "dialogue_block_count": other.dialogue_block_count}})
            if a.presence in APPEARANCE_PRESENCES and other.presence not in APPEARANCE_PRESENCES:
                other.presence = a.presence
            other.dialogue_block_count += a.dialogue_block_count
            if a.dialogue_block_count:
                other.speaking = a.speaking
            session.delete(a)
            continue
        moved.append({"id": a.id, "casting_role_id": a.casting_role_id})
        a.character_id = dst.id
        a.casting_role_id = role
    for al in session.scalars(select(CharacterAlias).where(CharacterAlias.character_id == src.id)):
        _ensure_alias(session, dst, al.alias)
    _ensure_alias(session, dst, src.canonical_name)
    src.status, src.merged_into_id = EntityStatus.MERGED, dst.id
    session.flush()
    log_event(session, src.project_id, src, "merge", actor, before, note=f"merged into {dst.canonical_name}")
    return {"type": "merge_character", "from_id": src.id, "into_id": dst.id, "moved": moved, "removed": removed}


def casting_variant(session: Session, variant_id: int, character_id: int, actor: str) -> dict[str, Any]:
    """YOUNG ARJUN -> character ARJUN with casting role 'YOUNG ARJUN'; ARJUN keeps his own role."""
    variant, base = session.get(Character, variant_id), _resolve_char(session, character_id)
    notes = {a.version_note for a in session.scalars(select(Appearance).where(Appearance.character_id == variant.id))
             if a.version_note}
    role = CastingRole(project_id=base.project_id, character_id=base.id, name=variant.canonical_name,
                       kind=CastingRoleKind.ACTOR, age_note=", ".join(sorted(notes)) or None,
                       extracted_by=ExtractorKind.RECONCILIATION)
    session.add(role)
    created_roles = [role]
    main = session.scalars(select(CastingRole).where(CastingRole.character_id == base.id,
                                                     CastingRole.name == base.canonical_name)).first()
    assigned = []
    if main is None:
        main = CastingRole(project_id=base.project_id, character_id=base.id, name=base.canonical_name,
                           kind=CastingRoleKind.ACTOR, extracted_by=ExtractorKind.RECONCILIATION)
        session.add(main)
        created_roles.append(main)
    session.flush()
    for a in session.scalars(select(Appearance).where(Appearance.character_id == base.id,
                                                      Appearance.casting_role_id.is_(None))):
        a.casting_role_id = main.id
        assigned.append(a.id)
    undo = merge_character(session, variant.id, base.id, actor, casting_role_id=role.id)
    log_event(session, base.project_id, role, "create", actor, note=f"casting role for {base.canonical_name}")
    undo.update({"type": "casting_variant", "created_roles": [r.id for r in created_roles],
                 "assigned_main_role": assigned})
    return undo


def merge_location(session: Session, from_id: int, into_id: int, actor: str) -> dict[str, Any]:
    src, dst = session.get(Location, from_id), session.get(Location, into_id)
    if src is None or dst is None or src.id == dst.id or src.status == EntityStatus.MERGED:
        raise ProposalError("nothing to merge")
    before = snapshot(src)
    undo = {"type": "merge_location", "from_id": src.id, "into_id": dst.id, "scene_locations": [],
            "aliases": [], "children": [], "child_merges": []}
    for sl in session.scalars(select(SceneLocation).where(SceneLocation.location_id == src.id)).all():
        undo["scene_locations"].append(sl.id)
        sl.location_id = dst.id
    for al in session.scalars(select(LocationAlias).where(LocationAlias.location_id == src.id)).all():
        undo["aliases"].append(al.id)
        al.location_id = dst.id
    dst_children = {normalize_name(c.name): c for c in session.scalars(select(Location).where(
        Location.parent_id == dst.id, Location.status == EntityStatus.ACTIVE))}
    for ch in session.scalars(select(Location).where(Location.parent_id == src.id,
                                                     Location.status == EntityStatus.ACTIVE)).all():
        twin = dst_children.get(normalize_name(ch.name))
        if twin is not None:
            undo["child_merges"].append(merge_location(session, ch.id, twin.id, actor))
        else:
            undo["children"].append(ch.id)
            ch.parent_id = dst.id
    session.add(LocationAlias(location_id=dst.id, alias=src.name, extracted_by=ExtractorKind.RECONCILIATION))
    src.status, src.merged_into_id = EntityStatus.MERGED, dst.id
    session.flush()
    log_event(session, src.project_id, src, "merge", actor, before, note=f"merged into {dst.name}")
    return undo


def reparent_location(session: Session, location_id: int, parent_id: int, new_name: str, actor: str) -> dict:
    loc, parent = session.get(Location, location_id), session.get(Location, parent_id)
    before = snapshot(loc)
    twin = session.scalars(select(Location).where(Location.parent_id == parent.id,
                                                  Location.name == normalize_name(new_name),
                                                  Location.status == EntityStatus.ACTIVE)).first()
    if twin is not None:
        return merge_location(session, loc.id, twin.id, actor) | {"type": "merge_location"}
    loc.parent_id, loc.name, loc.kind = parent.id, normalize_name(new_name), LocationKind.SET
    session.flush()
    log_event(session, loc.project_id, loc, "reparent", actor, before)
    return {"type": "reparent_location", "location_id": loc.id, "before": before}


def set_int_ext(session: Session, scene_id: int, value: str, actor: str) -> dict:
    sc = session.get(Scene, scene_id)
    before = snapshot(sc)
    sc.int_ext = IntExt(value)
    session.flush()
    project_id = sc_project(session, sc)
    log_event(session, project_id, sc, "edit", actor, before, note="INT/EXT set from suggestion")
    return {"type": "set_int_ext", "scene_id": sc.id, "before": before["int_ext"]}


def sc_project(session: Session, sc: Scene) -> int:
    from ..models import ScriptVersion
    return session.get(ScriptVersion, sc.script_version_id).project_id


def apply_proposal(session: Session, proposal: dict, actor: str) -> dict[str, Any]:
    act = proposal.get("action")
    if act == "merge_character":
        return merge_character(session, proposal["from_id"], proposal["into_id"], actor)
    if act == "casting_variant":
        return casting_variant(session, proposal["variant_id"], proposal["character_id"], actor)
    if act == "merge_location":
        return merge_location(session, proposal["from_id"], proposal["into_id"], actor)
    if act == "reparent_location":
        return reparent_location(session, proposal["location_id"], proposal["parent_id"], proposal["new_name"], actor)
    if act == "set_int_ext":
        return set_int_ext(session, proposal["scene_id"], proposal["value"], actor)
    raise ProposalError(f"unknown proposal action {act!r}")


def undo(session: Session, record: dict[str, Any], actor: str) -> None:
    t = record["type"]
    if t in ("merge_character", "casting_variant"):
        src = session.get(Character, record["from_id"])
        for m in record["moved"]:
            a = session.get(Appearance, m["id"])
            if a is not None:
                a.character_id, a.casting_role_id = src.id, m["casting_role_id"]
        from ..models.enums import Presence, Speaking
        for r in record["removed"]:
            other = session.get(Appearance, r["other_id"])
            if other is not None:
                ob = r["other_before"]
                other.presence, other.speaking = Presence(ob["presence"]), Speaking(ob["speaking"])
                other.dialogue_block_count = ob["dialogue_block_count"]
            session.add(_restore_appearance(dict(r["snap"])))
        src.status, src.merged_into_id = EntityStatus.ACTIVE, None
        if t == "casting_variant":
            for aid in record.get("assigned_main_role", []):
                a = session.get(Appearance, aid)
                if a is not None:
                    a.casting_role_id = None
            session.flush()
            for rid in record.get("created_roles", []):
                r = session.get(CastingRole, rid)
                if r is not None and not session.scalars(select(Appearance).where(
                        Appearance.casting_role_id == rid)).first():
                    session.delete(r)
        session.flush()
        log_event(session, src.project_id, src, "unmerge", actor)
    elif t == "merge_location":
        for child in record.get("child_merges", []):
            undo(session, child, actor)
        src = session.get(Location, record["from_id"])
        for sid in record["scene_locations"]:
            sl = session.get(SceneLocation, sid)
            if sl is not None:
                sl.location_id = src.id
        for aid in record["aliases"]:
            al = session.get(LocationAlias, aid)
            if al is not None:
                al.location_id = src.id
        for cid in record["children"]:
            ch = session.get(Location, cid)
            if ch is not None:
                ch.parent_id = src.id
        src.status, src.merged_into_id = EntityStatus.ACTIVE, None
        session.flush()
        log_event(session, src.project_id, src, "unmerge", actor)
    elif t == "reparent_location":
        loc = session.get(Location, record["location_id"])
        b = record["before"]
        loc.parent_id, loc.name, loc.kind = b["parent_id"], b["name"], LocationKind(b["kind"])
        session.flush()
        log_event(session, loc.project_id, loc, "undo_reparent", actor)
    elif t == "set_int_ext":
        sc = session.get(Scene, record["scene_id"])
        sc.int_ext = IntExt(record["before"])
        session.flush()
        log_event(session, sc_project(session, sc), sc, "undo_edit", actor)


def _restore_appearance(snap: dict) -> Appearance:
    from ..models.enums import Presence, ReviewStatus, Speaking
    snap = {k: v for k, v in snap.items() if k not in ("reviewed_at",)}
    snap["presence"] = Presence(snap["presence"])
    snap["speaking"] = Speaking(snap["speaking"])
    snap["extracted_by"] = ExtractorKind(snap["extracted_by"])
    snap["review_status"] = ReviewStatus(snap["review_status"])
    return Appearance(**snap)
