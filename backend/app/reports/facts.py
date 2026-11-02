"""Load the reviewed facts of one script version into plain Python objects.

Every report and query is computed from this one view, so they all apply the
same rules:
  * rejected facts are excluded everywhere
  * merged characters/locations resolve to the survivor
  * PRESENT and OFF_SCREEN count as appearances; VOICE_ONLY / MENTIONED / UNCERTAIN do not
  * time of day is the value AS WRITTEN unless the caller asks for 'effective'
    (inherited lighting for CONTINUOUS/LATER, which is marked as inferred)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..scene_detection.headings import parse_heading
from ..models import Appearance, CastingRole, Character, Location, Scene, SceneLocation
from ..models.enums import APPEARANCE_PRESENCES, EntityStatus, ReviewStatus, SceneLocationRole


@dataclass
class SceneFact:
    id: int
    seq: int
    number: str | None
    heading: str | None
    int_ext: str
    time_written: str
    time_effective: str
    time_inferred: bool
    eighths: int
    omitted: bool
    location_id: int | None = None          # most specific (set)
    location_path: list[int] = field(default_factory=list)  # root .. set
    needs_review: bool = False

    def time(self, mode: str) -> str:
        return self.time_effective if mode == "effective" else self.time_written


@dataclass
class AppearanceFact:
    id: int
    scene_id: int
    character_id: int
    casting_role_id: int | None
    presence: str
    speaking: str
    dialogue: int
    unreviewed: bool
    needs_review: bool

    @property
    def is_appearance(self) -> bool:
        return self.presence in {p.value for p in APPEARANCE_PRESENCES}


@dataclass
class OccurrenceFact:
    """A department item in one scene (Stage 2)."""

    id: int
    scene_id: int
    item_id: int
    department: str
    category: str | None
    name: str
    owner_id: int | None
    quantity_min: int | None
    quantity_max: int | None
    quantity_raw: str | None
    details: dict
    inferred: bool
    characters: list[tuple[int, str]]   # (character_id, role)
    needs_review: bool
    description: str | None


@dataclass
class Facts:
    scenes: list[SceneFact]
    appearances: list[AppearanceFact]
    characters: dict[int, Character]
    locations: dict[int, Location]
    casting_roles: dict[int, CastingRole]
    occurrences: list[OccurrenceFact] = field(default_factory=list)
    scene_by_id: dict[int, SceneFact] = field(default_factory=dict)

    def __post_init__(self):
        self.scene_by_id = {s.id: s for s in self.scenes}

    def loc_name(self, lid: int | None) -> str:
        return self.locations[lid].name if lid in self.locations else "—"

    def loc_label(self, lid: int | None) -> str:
        if lid not in self.locations:
            return "(no location)"
        parts = []
        cur = self.locations.get(lid)
        while cur is not None:
            parts.insert(0, cur.name)
            cur = self.locations.get(cur.parent_id) if cur.parent_id else None
        return " > ".join(parts)


def load_facts(session: Session, version_id: int) -> Facts:
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)).all()
    scene_ids = [s.id for s in scenes]
    project_id = None
    if scenes:
        from ..models import ScriptVersion
        project_id = session.get(ScriptVersion, version_id).project_id

    chars_all = {c.id: c for c in session.scalars(select(Character).where(Character.project_id == project_id))}
    locs_all = {loc.id: loc for loc in session.scalars(select(Location).where(Location.project_id == project_id))}
    roles = {r.id: r for r in session.scalars(select(CastingRole).where(CastingRole.project_id == project_id))}

    def resolve_char(cid: int) -> int:
        seen = set()
        while chars_all[cid].status == EntityStatus.MERGED and chars_all[cid].merged_into_id and cid not in seen:
            seen.add(cid)
            cid = chars_all[cid].merged_into_id
        return cid

    def resolve_loc(lid: int) -> int:
        seen = set()
        while locs_all[lid].status == EntityStatus.MERGED and locs_all[lid].merged_into_id and lid not in seen:
            seen.add(lid)
            lid = locs_all[lid].merged_into_id
        return lid

    def path(lid: int) -> list[int]:
        out = []
        cur = locs_all.get(lid)
        while cur is not None and cur.id not in out:
            out.insert(0, cur.id)
            cur = locs_all.get(resolve_loc(cur.parent_id)) if cur.parent_id else None
        return out

    primary: dict[int, int] = {}
    if scene_ids:
        for sl in session.scalars(select(SceneLocation).where(
                SceneLocation.scene_id.in_(scene_ids), SceneLocation.review_status != ReviewStatus.REJECTED)
                .order_by(SceneLocation.id)):
            if sl.role == SceneLocationRole.PRIMARY or sl.scene_id not in primary:
                primary[sl.scene_id] = resolve_loc(sl.location_id)

    sfacts = []
    for s in scenes:
        eff = s.effective_time_of_day.value if s.effective_time_of_day else s.time_of_day.value
        lid = primary.get(s.id)
        sfacts.append(SceneFact(
            id=s.id, seq=s.seq, number=s.scene_number,
            # Display text without margin numbers; the verbatim heading stays in scenes.heading_raw.
            heading=parse_heading(s.heading_raw.split("\n")[0]).text if s.heading_raw else None, int_ext=s.int_ext.value,
            time_written=s.time_of_day.value, time_effective=eff,
            time_inferred=s.effective_time_of_day is not None, eighths=s.length_eighths or 0,
            omitted=s.is_omitted, location_id=lid, location_path=path(lid) if lid else [],
            needs_review=s.needs_review and s.review_status == ReviewStatus.UNREVIEWED,
        ))

    afacts = []
    if scene_ids:
        for a in session.scalars(select(Appearance).where(
                Appearance.scene_id.in_(scene_ids), Appearance.review_status != ReviewStatus.REJECTED)):
            afacts.append(AppearanceFact(
                id=a.id, scene_id=a.scene_id, character_id=resolve_char(a.character_id),
                casting_role_id=a.casting_role_id, presence=a.presence.value, speaking=a.speaking.value,
                dialogue=a.dialogue_block_count, unreviewed=a.review_status == ReviewStatus.UNREVIEWED,
                needs_review=a.needs_review and a.review_status == ReviewStatus.UNREVIEWED,
            ))
    active_chars = {cid: c for cid, c in chars_all.items() if c.status == EntityStatus.ACTIVE}
    active_locs = {lid: loc for lid, loc in locs_all.items() if loc.status == EntityStatus.ACTIVE}
    return Facts(sfacts, afacts, active_chars, active_locs, roles,
                 _load_occurrences(session, project_id, scene_ids, resolve_char))


def _load_occurrences(session, project_id, scene_ids, resolve_char) -> list[OccurrenceFact]:
    from ..models import BreakdownItem, BreakdownOccurrence, OccurrenceCharacter
    if not scene_ids:
        return []
    items = {i.id: i for i in session.scalars(select(BreakdownItem).where(BreakdownItem.project_id == project_id))}

    def resolve_item(iid):
        seen = set()
        while items[iid].status == EntityStatus.MERGED and items[iid].merged_into_id and iid not in seen:
            seen.add(iid)
            iid = items[iid].merged_into_id
        return iid
    occs = session.scalars(select(BreakdownOccurrence).where(
        BreakdownOccurrence.scene_id.in_(scene_ids), BreakdownOccurrence.review_status != ReviewStatus.REJECTED)).all()
    links: dict[int, list] = {}
    if occs:
        for oc in session.scalars(select(OccurrenceCharacter).where(
                OccurrenceCharacter.occurrence_id.in_([o.id for o in occs]))):
            links.setdefault(oc.occurrence_id, []).append((resolve_char(oc.character_id), oc.role))
    out = []
    for o in occs:
        it = items[resolve_item(o.item_id)]
        out.append(OccurrenceFact(
            id=o.id, scene_id=o.scene_id, item_id=it.id, department=o.department.value, category=it.category,
            name=it.name, owner_id=resolve_char(it.character_id) if it.character_id else None,
            quantity_min=o.quantity_min, quantity_max=o.quantity_max, quantity_raw=o.quantity_raw,
            details=o.details or {}, inferred=o.is_inferred, characters=links.get(o.id, []),
            needs_review=o.needs_review and o.review_status == ReviewStatus.UNREVIEWED, description=o.description))
    return out

