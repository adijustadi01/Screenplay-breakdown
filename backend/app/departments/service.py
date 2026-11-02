"""Run department extraction and write verified items (shared by every department).

Same rules as Stage 1: every item keeps its quoted source passage, confidence and
needs_review flag; quotes are checked against the script; characters are matched to
the Stage 1 cast; unknown headcounts stay unknown; inferred items are always reviewed;
AD-confirmed or edited items survive re-extraction.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..extraction.cues import normalize_name
from ..extraction.service import ExtractionAborted, _is_fatal, _norm_text, scene_text
from ..models import (
    BreakdownItem,
    BreakdownOccurrence,
    Character,
    CharacterAlias,
    ExtractionRun,
    OccurrenceCharacter,
    ReviewItem,
    Scene,
    ScriptElement,
    ScriptVersion,
)
from ..models.enums import (
    Department,
    ElementType,
    EntityStatus,
    ExtractorKind,
    ReviewCategory,
    ReviewItemStatus,
    ReviewStatus,
    RunKind,
    RunStatus,
    Severity,
)
from ..reports.facts import load_facts
from . import registry
from .base import DepartmentSpec, ItemOut, SceneContext

REVIEW_CONFIDENCE = 0.7
LOCKED = (ReviewStatus.CONFIRMED, ReviewStatus.EDITED)


@dataclass
class _Ctx:
    session: Session
    version: ScriptVersion
    run: ExtractionRun
    specs: list[DepartmentSpec]
    char_lookup: dict[str, int]
    item_cache: dict[tuple[Department, str], BreakdownItem]
    extractor_kind: ExtractorKind


def build_contexts(session: Session, version_id: int) -> tuple[list[tuple[Scene, list, str, SceneContext]], list[str], list[str]]:
    facts = load_facts(session, version_id)
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id).order_by(Scene.seq)).all()
    els_by_scene: dict[int, list[ScriptElement]] = {}
    for e in session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                             .order_by(ScriptElement.seq)):
        if e.scene_id:
            els_by_scene.setdefault(e.scene_id, []).append(e)
    present: dict[int, set[str]] = {}
    voice: dict[int, set[str]] = {}
    for a in facts.appearances:
        name = facts.characters[a.character_id].canonical_name if a.character_id in facts.characters else None
        if not name:
            continue
        if a.is_appearance:
            present.setdefault(a.scene_id, set()).add(name)
        elif a.presence == "voice_only":
            voice.setdefault(a.scene_id, set()).add(name)
    out = []
    for s in scenes:
        if s.is_omitted:
            continue
        sf = facts.scene_by_id[s.id]
        els = els_by_scene.get(s.id, [])
        text = scene_text(s, els)
        action = " ".join(e.text for e in els if e.element_type in (ElementType.ACTION, ElementType.SCENE_HEADING))
        out.append((s, els, text, SceneContext(
            scene_id=s.id, label=s.scene_number or f"#{s.seq}", int_ext=sf.int_ext, time=sf.time_written,
            time_effective=sf.time_effective, location_label=facts.loc_label(sf.location_id), text=text,
            action_text=action, present=present.get(s.id, set()), voice_only=voice.get(s.id, set()), elements=els)))
    used = {a.character_id for a in facts.appearances}
    cast = sorted(c.canonical_name for cid, c in facts.characters.items() if cid in used)
    locations = sorted({facts.loc_label(lid) for lid in facts.locations})
    return out, cast, locations


def run_department_extraction(
    session: Session, version_id: int, extractor, departments: list[str] | None = None, workers: int = 4,
    scene_ids: list[int] | None = None, progress: Callable[[int, int], None] | None = None,
) -> ExtractionRun:
    specs = extractor.specs if departments is None else registry.get(departments)
    version = session.get(ScriptVersion, version_id)
    jobs, cast, locations = build_contexts(session, version_id)
    if scene_ids is not None:
        jobs = [j for j in jobs if j[0].id in set(scene_ids)]
    extractor.set_script_context(cast, locations)
    run = ExtractionRun(script_version_id=version_id, kind=RunKind.DEPARTMENT_EXTRACTION,
                        model=getattr(extractor, "model", None) or extractor.name,
                        prompt_version=extractor.prompt_version,
                        params={"extractor": extractor.name, "departments": [s.key.value for s in specs]})
    session.add(run)
    session.flush()
    ctx = _Ctx(session, version, run, specs, _char_lookup(session, version.project_id), {},
               ExtractorKind.LLM if extractor.name == "claude" else ExtractorKind.RULES)
    for it in session.scalars(select(BreakdownItem).where(BreakdownItem.project_id == version.project_id)):
        ctx.item_cache.setdefault((it.department, it.name), it)

    failures = done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [(s, sc, pool.submit(extractor.extract, text, sc)) for s, _els, text, sc in jobs]
        for s, sc, fut in futures:
            try:
                result = fut.result()
            except Exception as exc:
                if _is_fatal(exc):
                    for *_, other in futures:
                        other.cancel()
                    run.status, run.error, run.finished_at = RunStatus.FAILED, repr(exc), utcnow()
                    session.flush()
                    raise ExtractionAborted(f"department extraction stopped: {exc}") from exc
                failures += 1
                session.add(ReviewItem(
                    project_id=version.project_id, script_version_id=version.id,
                    category=ReviewCategory.EXTRACTION, reason_code="department_extraction_failed",
                    severity=Severity.ERROR, entity_table="scenes", entity_id=s.id, scene_id=s.id,
                    message=f"Scene {sc.label}: department extraction failed ({type(exc).__name__}: "
                            f"{str(exc)[:200]}). Re-run for this scene or add items by hand."))
            else:
                apply_departments(ctx, s, sc, result)
            done += 1
            if progress:
                progress(done, len(futures))
    run.status = RunStatus.SUCCEEDED if failures == 0 else (
        RunStatus.PARTIAL if failures < len(jobs) else RunStatus.FAILED)
    run.finished_at = utcnow()
    run.stats = {"scenes": len(jobs), "failed": failures, "departments": [s.key.value for s in specs]}
    session.flush()
    return run


def _char_lookup(session: Session, project_id: int) -> dict[str, int]:
    chars = {c.id: c for c in session.scalars(select(Character).where(Character.project_id == project_id))}

    def resolve(cid):
        seen = set()
        while chars[cid].status == EntityStatus.MERGED and chars[cid].merged_into_id and cid not in seen:
            seen.add(cid)
            cid = chars[cid].merged_into_id
        return cid
    out = {}
    for c in chars.values():
        out.setdefault(normalize_name(c.canonical_name), resolve(c.id))
    for a in session.scalars(select(CharacterAlias)):
        if a.character_id in chars:
            out.setdefault(a.alias, resolve(a.character_id))
    return out


def _clear(ctx: _Ctx, scene: Scene) -> set[int]:
    s = ctx.session
    depts = {sp.key for sp in ctx.specs}
    locked_items, removed = set(), set()
    for o in s.scalars(select(BreakdownOccurrence).where(BreakdownOccurrence.scene_id == scene.id,
                                                         BreakdownOccurrence.department.in_(depts))):
        if o.review_status in LOCKED or o.extracted_by == ExtractorKind.HUMAN:
            locked_items.add(o.item_id)
        else:
            removed.add(o.id)
            s.delete(o)
    if removed:
        for item in s.scalars(select(ReviewItem).where(ReviewItem.entity_table == "breakdown_occurrences",
                                                       ReviewItem.entity_id.in_(removed),
                                                       ReviewItem.status == ReviewItemStatus.OPEN)):
            item.status = ReviewItemStatus.DISMISSED
            item.resolution_note = f"superseded by department run {ctx.run.id}"
    s.flush()
    return locked_items


def _coerce_details(spec: DepartmentSpec, raw: dict) -> dict:
    out = {}
    for d in spec.details:
        v = (raw or {}).get(d.name)
        if v is None:
            continue
        if d.type == "boolean" and not isinstance(v, bool):
            v = str(v).lower() in ("true", "yes", "1")
        elif d.type == "integer":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        elif d.type == "string_list" and not isinstance(v, list):
            v = [str(v)]
        if d.choices and v not in d.choices:
            continue
        out[d.name] = v
    return out


def _get_item(ctx: _Ctx, spec: DepartmentSpec, out: ItemOut, owner: int | None, scene: Scene) -> BreakdownItem:
    key = (spec.key, normalize_name(out.name))
    it = ctx.item_cache.get(key)
    while it is not None and it.status == EntityStatus.MERGED and it.merged_into_id:
        it = ctx.session.get(BreakdownItem, it.merged_into_id)
    if it is None:
        it = BreakdownItem(project_id=ctx.version.project_id, department=spec.key, name=key[1],
                           category=out.category if out.category in spec.categories else "other",
                           description=out.description, character_id=owner, source_scene_id=scene.id,
                           source_excerpt=out.evidence[:1000] or None, extracted_by=ctx.extractor_kind,
                           extraction_run_id=ctx.run.id, confidence=min(1.0, max(0.0, out.confidence)))
        ctx.session.add(it)
        ctx.session.flush()
        ctx.item_cache[key] = it
    elif it.character_id is None and owner is not None:
        it.character_id = owner
    return it


def apply_departments(ctx: _Ctx, scene: Scene, sc: SceneContext, result: dict[str, list[ItemOut]]) -> None:
    s = ctx.session
    locked_items = _clear(ctx, scene)
    norm_scene = _norm_text(sc.text)
    by_dept: dict[Department, list[ItemOut]] = {sp.key: list(result.get(sp.key.value, [])) for sp in ctx.specs}
    # Rules may read other departments' facts for this scene: this run's results, else the stored ones.
    view = dict(_stored_items(ctx, scene, exclude={sp.key for sp in ctx.specs}))
    view.update(by_dept)
    for sp in ctx.specs:                       # deterministic items derived in code
        if sp.derive:
            derived = sp.derive(sc, view)
            by_dept[sp.key].extend(derived)
            view[sp.key] = by_dept[sp.key]
    on_set = sc.present | sc.voice_only
    for sp in ctx.specs:
        merged: dict[str, tuple[ItemOut, list[str]]] = {}
        for out in by_dept[sp.key]:
            name = normalize_name(out.name)
            if not name:
                continue
            reasons: list[str] = []
            conf = min(1.0, max(0.0, float(out.confidence)))
            if out.evidence and _norm_text(out.evidence) in norm_scene:
                pass
            else:
                conf *= 0.5
                reasons.append("evidence_not_found")
            if out.inferred:
                reasons.append("inferred")
            if out.category not in sp.categories and out.category != "other":
                out.category = "other"
            qty_unknown = out.quantity_min is None and out.quantity_max is None
            if sp.quantity_matters and qty_unknown:
                reasons.append("unknown_quantity")
            elif qty_unknown and out.quantity_text:
                reasons.append("unknown_quantity")
            out.details = _coerce_details(sp, out.details)
            if sp.review:
                reasons += [r for r in sp.review(out, sc) if r not in reasons]
            out.confidence = conf
            if name in merged:            # same item listed twice in one scene: combine
                prev, prev_r = merged[name]
                prev.characters += [c for c in out.characters if c not in prev.characters]
                if (out.quantity_max or 0) > (prev.quantity_max or 0):
                    prev.quantity_min, prev.quantity_max = out.quantity_min, out.quantity_max
                prev_r += [r for r in reasons if r not in prev_r]
                continue
            merged[name] = (out, reasons)

        for name, (out, reasons) in merged.items():
            links, unresolved = [], []
            for c in out.characters:
                cid = ctx.char_lookup.get(normalize_name(c.get("name", "")))
                role = c.get("role") if c.get("role") in sp.roles else (sp.default_role or sp.roles[0])
                if cid is None:
                    unresolved.append(c.get("name"))
                    continue
                links.append((cid, role))
                cname = normalize_name(c.get("name", ""))
                if cname not in on_set and not any(ctx.char_lookup.get(n) == cid for n in on_set):
                    if "character_not_in_scene" not in reasons:
                        reasons.append("character_not_in_scene")
            if unresolved:
                out.details["unresolved_characters"] = unresolved
                reasons.append("unknown_character")
            if out.confidence < REVIEW_CONFIDENCE and "low_confidence" not in reasons:
                reasons.append("low_confidence")
            owners = {cid for cid, role in links if role in sp.owner_roles}
            item = _get_item(ctx, sp, out, owners.pop() if len(owners) == 1 else None, scene)
            if item.id in locked_items:
                continue
            kind = ExtractorKind(out.extracted_by) if out.extracted_by else ctx.extractor_kind
            occ = BreakdownOccurrence(
                scene_id=scene.id, item_id=item.id, department=sp.key, description=out.description,
                details=out.details or None, is_inferred=out.inferred,
                quantity_min=out.quantity_min, quantity_max=out.quantity_max, quantity_raw=out.quantity_text,
                source_scene_id=scene.id, source_excerpt=(out.evidence or "")[:1000] or None,
                extracted_by=kind, extraction_run_id=ctx.run.id, confidence=round(out.confidence, 3),
                needs_review=bool(reasons), review_reason=",".join(reasons)[:255] or None)
            el = _find(sc.elements, out.evidence)
            if el is not None:
                occ.source_element_start_id = occ.source_element_end_id = el.id
            s.add(occ)
            s.flush()
            for cid, role in dict.fromkeys(links):
                s.add(OccurrenceCharacter(occurrence_id=occ.id, character_id=cid, role=role))
            if reasons:
                label = "INFERRED — review. " if out.inferred else ""
                qty = (f" ×{out.quantity_min}" if out.quantity_min is not None and out.quantity_min == out.quantity_max
                       else (f" (headcount unknown: {out.quantity_text!r})" if out.quantity_text else ""))
                msg = (f"{label}Scene {sc.label} · {sp.label}: {item.name}{qty} ({', '.join(reasons)}). "
                       f"Evidence: {out.evidence!r}" + (f" Note: {out.uncertainty_note}" if out.uncertainty_note else ""))
                s.add(ReviewItem(
                    project_id=ctx.version.project_id, script_version_id=ctx.version.id,
                    category=ReviewCategory.EXTRACTION, reason_code=reasons[0],
                    severity=Severity.WARNING if (out.inferred or "unknown_quantity" in reasons
                                                  or "evidence_not_found" in reasons) else Severity.INFO,
                    message=msg[:2000], entity_table="breakdown_occurrences", entity_id=occ.id,
                    scene_id=scene.id, element_id=occ.source_element_start_id, confidence=occ.confidence))
    s.flush()


def _stored_items(ctx: _Ctx, scene: Scene, exclude: set) -> dict[Department, list[ItemOut]]:
    out: dict[Department, list[ItemOut]] = {}
    occs = ctx.session.scalars(select(BreakdownOccurrence).where(
        BreakdownOccurrence.scene_id == scene.id, BreakdownOccurrence.review_status != ReviewStatus.REJECTED)).all()
    for o in occs:
        if o.department in exclude:
            continue
        it = ctx.session.get(BreakdownItem, o.item_id)
        chars = [{"name": "", "role": oc.role} for oc in o.characters]
        out.setdefault(o.department, []).append(ItemOut(
            name=it.name, category=it.category or "other", description=o.description,
            quantity_min=o.quantity_min, quantity_max=o.quantity_max, quantity_text=o.quantity_raw,
            characters=chars, details=dict(o.details or {}), evidence=o.source_excerpt or "",
            confidence=o.confidence, inferred=o.is_inferred))
    return out


def _find(elements, quote: str):
    q = _norm_text(quote or "")
    if not q:
        return None
    for e in elements:
        if q in _norm_text(e.text):
            return e
    return None
