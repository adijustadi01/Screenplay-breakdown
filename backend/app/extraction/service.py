"""Run per-scene extraction and write verified facts to the database.

Division of labour:
  * dialogue cues (code)  -> who speaks, V.O./O.S., dialogue counts   [certain]
  * extractor (LLM)       -> silent characters, mentions, groups, location split, synopsis
  * this module (code)    -> checks every LLM claim against the scene text and the cues,
                             sets confidence / needs_review, writes provenance

Facts the AD has confirmed or edited are never overwritten by a re-run.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..models import (
    Appearance,
    Character,
    CharacterAlias,
    ExtractionRun,
    Location,
    LocationAlias,
    ReviewItem,
    Scene,
    SceneLocation,
    ScriptElement,
    ScriptVersion,
)
from ..models.enums import (
    CharacterKind,
    ElementType,
    EntityStatus,
    ExtractorKind,
    IntExt,
    LocationKind,
    Presence,
    ReviewCategory,
    ReviewItemStatus,
    ReviewStatus,
    RunKind,
    RunStatus,
    SceneLocationRole,
    ScriptVersionStatus,
    Severity,
    Speaking,
)
from .cues import normalize_name, parse_cue, scene_cues
from .heuristic_extractor import intro_names
from .schema import CharacterOut, SceneExtraction

REVIEW_CONFIDENCE = 0.7
LOCKED = (ReviewStatus.CONFIRMED, ReviewStatus.EDITED)
SKIP_TYPES = {ElementType.PAGE_HEADER, ElementType.NON_PRINTING, ElementType.TITLE_PAGE}


def _norm_text(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("…", "...")
    return re.sub(r"\s+", " ", s).strip().lower()


def scene_text(scene: Scene, elements: list[ScriptElement]) -> str:
    """Screenplay-like plain text sent to the extractor."""
    lines = [f"SCENE {scene.scene_number or '(unnumbered)'} — HEADING: {scene.heading_raw or '(no heading)'}", ""]
    for e in elements:
        t = e.element_type
        if t in SKIP_TYPES or t == ElementType.SCENE_HEADING:
            continue
        if t == ElementType.CHARACTER:
            lines += ["", " " * 20 + e.text]
        elif t == ElementType.PARENTHETICAL:
            lines.append(" " * 15 + e.text)
        elif t == ElementType.DIALOGUE:
            lines.append(" " * 10 + e.text)
        else:
            lines += ["", e.text]
    return "\n".join(lines).strip()


@dataclass
class _Ctx:
    session: Session
    version: ScriptVersion
    run: ExtractionRun
    script_cue_names: set[str]
    char_cache: dict[str, Character]
    loc_cache: dict[tuple[int | None, str], Location]


def run_extraction(
    session: Session,
    version_id: int,
    extractor,
    workers: int = 4,
    scene_ids: list[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ExtractionRun:
    version = session.get(ScriptVersion, version_id)
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id)
                             .order_by(Scene.seq)).all()
    if scene_ids is not None:
        wanted = set(scene_ids)
        scenes = [s for s in scenes if s.id in wanted]
    all_els = session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                              .order_by(ScriptElement.seq)).all()
    by_scene: dict[int, list[ScriptElement]] = {}
    for e in all_els:
        if e.scene_id:
            by_scene.setdefault(e.scene_id, []).append(e)

    cue_names = {parse_cue(e.text)[0] for e in all_els if e.element_type == ElementType.CHARACTER} - {""}
    intros = intro_names([e.text for e in all_els if e.element_type == ElementType.ACTION])
    loc_names = sorted({s.location_raw for s in session.scalars(
        select(Scene).where(Scene.script_version_id == version_id)) if s.location_raw})
    extractor.set_script_context(sorted(cue_names), loc_names, sorted(intros))

    run = ExtractionRun(script_version_id=version_id, kind=RunKind.SCENE_EXTRACTION,
                        model=getattr(extractor, "model", None) or extractor.name,
                        prompt_version=extractor.prompt_version,
                        params={"extractor": extractor.name, "effort": getattr(extractor, "effort", None),
                                "scene_ids": sorted(scene_ids) if scene_ids is not None else None})
    session.add(run)
    session.flush()
    ctx = _Ctx(session, version, run, cue_names, {}, {})
    for c in session.scalars(select(Character).where(Character.project_id == version.project_id)):
        ctx.char_cache.setdefault(normalize_name(c.canonical_name), c)
    for loc in session.scalars(select(Location).where(Location.project_id == version.project_id)):
        ctx.loc_cache.setdefault((loc.parent_id, normalize_name(loc.name)), loc)

    todo = [s for s in scenes if not s.is_omitted]
    jobs = []
    for s in todo:
        els = by_scene.get(s.id, [])
        jobs.append((s, els, scene_text(s, els),
                     {"elements": els, "cue_names": set(scene_cues(els)), "location_raw": s.location_raw}))

    failures = 0
    done = 0
    # The LLM calls run in threads; all database work stays on this thread.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [(s, els, text, pool.submit(extractor.extract, text, c)) for s, els, text, c in jobs]
        for s, els, text, fut in futures:
            try:
                result = fut.result()
            except Exception as exc:  # refusal, API error, invalid output
                if _is_fatal(exc):
                    # Bad credentials / no access: stop instead of flooding the queue with failures.
                    for *_, other in futures:
                        other.cancel()
                    run.status, run.error, run.finished_at = RunStatus.FAILED, repr(exc), utcnow()
                    session.flush()
                    raise ExtractionAborted(f"extraction stopped: {exc}") from exc
                failures += 1
                _fail(ctx, s, exc)
            else:
                apply_extraction(ctx, s, els, text, result)
            done += 1
            if progress:
                progress(done, len(futures))
            if done % 25 == 0:
                session.flush()

    run.status = RunStatus.SUCCEEDED if failures == 0 else (
        RunStatus.PARTIAL if failures < len(jobs) else RunStatus.FAILED)
    run.finished_at = utcnow()
    run.stats = {"scenes": len(jobs), "failed": failures}
    if failures < len(jobs):
        version.status = ScriptVersionStatus.EXTRACTED
    session.flush()
    return run


class ExtractionAborted(RuntimeError):
    pass


def _is_fatal(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                            anthropic.NotFoundError))


def _fail(ctx: _Ctx, scene: Scene, exc: Exception) -> None:
    ctx.session.add(ReviewItem(
        project_id=ctx.version.project_id, script_version_id=ctx.version.id,
        category=ReviewCategory.EXTRACTION, reason_code="extraction_failed", severity=Severity.ERROR,
        message=f"Scene {scene.scene_number or f'#{scene.seq}'}: extraction failed ({type(exc).__name__}: "
                f"{str(exc)[:200]}). Re-run extraction for this scene or enter its cast manually.",
        entity_table="scenes", entity_id=scene.id, scene_id=scene.id))


def _get_character(ctx: _Ctx, name: str, kind: CharacterKind, conf: float, evidence: str | None,
                   scene: Scene) -> Character:
    key = normalize_name(name)
    c = ctx.char_cache.get(key)
    while c is not None and c.status == EntityStatus.MERGED and c.merged_into_id:
        c = ctx.session.get(Character, c.merged_into_id)
    if c is None:
        c = Character(project_id=ctx.version.project_id, canonical_name=key, kind=kind,
                      extracted_by=ExtractorKind.LLM if ctx.run.model != "heuristic" else ExtractorKind.RULES,
                      extraction_run_id=ctx.run.id, confidence=min(1.0, max(0.0, conf)),
                      source_scene_id=scene.id, source_excerpt=evidence)
        ctx.session.add(c)
        ctx.session.flush()
        ctx.char_cache[key] = c
    elif kind == CharacterKind.GROUP and c.kind != CharacterKind.GROUP:
        c.kind = CharacterKind.GROUP
    return c


def _add_alias(ctx: _Ctx, c: Character, alias: str, is_cue: bool, scene: Scene) -> None:
    a = normalize_name(alias)
    if not a:
        return
    existing = ctx.session.scalars(select(CharacterAlias).where(
        CharacterAlias.character_id == c.id, CharacterAlias.alias == a)).first()
    if existing:
        existing.is_cue_name = existing.is_cue_name or is_cue
        return
    ctx.session.add(CharacterAlias(character_id=c.id, alias=a, alias_raw=alias, is_cue_name=is_cue,
                                   extracted_by=ExtractorKind.PARSER if is_cue else ExtractorKind.LLM,
                                   extraction_run_id=ctx.run.id, source_scene_id=scene.id))
    ctx.session.flush()


def _find_element(els: list[ScriptElement], quote: str) -> ScriptElement | None:
    q = _norm_text(quote)
    if not q:
        return None
    for e in els:
        if q in _norm_text(e.text):
            return e
    return None


def _clear_unlocked(ctx: _Ctx, scene: Scene) -> set[int]:
    """Remove this scene's previous machine facts; return character ids that are locked."""
    s = ctx.session
    locked_chars = set()
    for a in s.scalars(select(Appearance).where(Appearance.scene_id == scene.id)):
        if a.review_status in LOCKED or a.extracted_by == ExtractorKind.HUMAN:
            locked_chars.add(a.character_id)
        else:
            s.delete(a)
    for sl in s.scalars(select(SceneLocation).where(SceneLocation.scene_id == scene.id)):
        if not (sl.review_status in LOCKED or sl.extracted_by == ExtractorKind.HUMAN):
            s.delete(sl)
    for item in s.scalars(select(ReviewItem).where(
            ReviewItem.scene_id == scene.id, ReviewItem.category == ReviewCategory.EXTRACTION,
            ReviewItem.status == ReviewItemStatus.OPEN)):
        item.status = ReviewItemStatus.DISMISSED
        item.resolution_note = f"superseded by extraction run {ctx.run.id}"
    s.flush()
    return locked_chars


def apply_extraction(ctx: _Ctx, scene: Scene, els: list[ScriptElement], text: str,
                     result: SceneExtraction) -> None:
    s = ctx.session
    locked_chars = _clear_unlocked(ctx, scene)
    norm_scene = _norm_text(text)
    cues = scene_cues(els)
    label = scene.scene_number or f"#{scene.seq}"
    planned: dict[int, dict] = {}   # character_id -> appearance fields

    # --- 1. Characters the LLM reported ---------------------------------------------------
    for c in result.characters:
        written = normalize_name(c.name_as_written)
        canon = normalize_name(c.canonical_name) or written
        known = ctx.script_cue_names | set(ctx.char_cache)
        inferred = False
        if written in known or written in cues:
            key = written
        elif canon in known:
            key, inferred = canon, written != canon
        else:
            key = canon
        if not key:
            continue
        reasons: list[str] = []
        conf = min(1.0, max(0.0, float(c.confidence)))
        evidence_ok = bool(c.evidence) and _norm_text(c.evidence) in norm_scene
        if not evidence_ok:
            conf *= 0.5
            reasons.append("evidence_not_found")
        presence = Presence(c.presence)
        speaking = Speaking(c.speaking)
        cue = cues.get(key)
        dialogue = 0
        if cue is not None:
            dialogue = cue.dialogue_blocks
            if presence != cue.presence and not (presence == Presence.PRESENT and cue.presence == Presence.OFF_SCREEN):
                reasons.append("llm_disagrees_with_cue")
            presence, speaking, conf = cue.presence, Speaking.SPEAKING, max(conf, 0.95)
        elif speaking == Speaking.SPEAKING:
            reasons.append("speaking_without_cue")
        elif presence in (Presence.MENTIONED,):
            speaking = Speaking.SILENT
        if presence == Presence.UNCERTAIN:
            reasons.append("uncertain_presence")
        if inferred and presence != Presence.MENTIONED:
            reasons.append("linked_by_inference")
        qmin, qmax = c.quantity_min, c.quantity_max
        if c.is_group and qmin is None and qmax is None and presence != Presence.MENTIONED:
            reasons.append("unknown_headcount")
        if conf < REVIEW_CONFIDENCE and "low_confidence" not in reasons:
            reasons.append("low_confidence")
        ch = _get_character(ctx, key, CharacterKind.GROUP if c.is_group else CharacterKind.INDIVIDUAL,
                            conf, c.evidence, scene)
        if not inferred:
            _add_alias(ctx, ch, c.name_as_written, written in cues, scene)
        if ch.id in locked_chars:
            continue
        el = _find_element(els, c.evidence) if evidence_ok else None
        prev = planned.get(ch.id)
        if prev and _rank(prev["presence"]) <= _rank(presence):
            continue  # keep the stronger presence if the LLM listed someone twice
        planned[ch.id] = dict(
            presence=presence, speaking=speaking, dialogue_block_count=dialogue, name_as_written=c.name_as_written,
            version_note=c.age_or_version, quantity_min=qmin, quantity_max=qmax, quantity_raw=c.quantity_text,
            source_excerpt=c.evidence[:1000] if c.evidence else None,
            source_element_start_id=el.id if el else None, source_element_end_id=el.id if el else None,
            confidence=round(conf, 3), reasons=reasons, note=c.uncertainty_note,
            extracted_by=ExtractorKind.LLM if ctx.run.model != "heuristic" else ExtractorKind.RULES,
        )

    # --- 2. Speakers the LLM missed: cues are certain, add them ---------------------------
    for name, cue in cues.items():
        ch = _get_character(ctx, name, CharacterKind.INDIVIDUAL, 1.0, cue.cue_texts[0], scene)
        _add_alias(ctx, ch, name, True, scene)
        if ch.id in planned or ch.id in locked_chars:
            continue
        planned[ch.id] = dict(
            presence=cue.presence, speaking=Speaking.SPEAKING, dialogue_block_count=cue.dialogue_blocks,
            name_as_written=name, version_note=None, quantity_min=None, quantity_max=None, quantity_raw=None,
            source_excerpt=cue.cue_texts[0], source_element_start_id=cue.first_element_id,
            source_element_end_id=cue.first_element_id, confidence=1.0, reasons=[], note=None,
            extracted_by=ExtractorKind.PARSER,
        )

    from ..models import CastingRole
    for char_id, f in planned.items():
        reasons, note = f.pop("reasons"), f.pop("note")
        # A character with casting roles (ARJUN / YOUNG ARJUN): the name used in the scene picks the role.
        roles = {normalize_name(r.name): r.id for r in s.scalars(select(CastingRole).where(
            CastingRole.character_id == char_id))}
        if roles and f.get("name_as_written"):
            f["casting_role_id"] = roles.get(normalize_name(f["name_as_written"]))
        a = Appearance(scene_id=scene.id, character_id=char_id, source_scene_id=scene.id,
                       extraction_run_id=ctx.run.id, needs_review=bool(reasons),
                       review_reason=",".join(reasons) or None, **f)
        s.add(a)
        s.flush()
        if reasons:
            ch = s.get(Character, char_id)
            msg = (f"Scene {label}: {ch.canonical_name} — {a.presence.value}, {a.speaking.value} "
                   f"({', '.join(reasons)}). Evidence: {a.source_excerpt!r}")
            if note:
                msg += f" Note: {note}"
            s.add(ReviewItem(
                project_id=ctx.version.project_id, script_version_id=ctx.version.id,
                category=ReviewCategory.EXTRACTION, reason_code=reasons[0],
                severity=Severity.WARNING if a.presence in (Presence.PRESENT, Presence.OFF_SCREEN,
                                                            Presence.UNCERTAIN) else Severity.INFO,
                message=msg[:2000], entity_table="appearances", entity_id=a.id, scene_id=scene.id,
                element_id=a.source_element_start_id, confidence=a.confidence))

    # --- 3. Location ------------------------------------------------------------------------
    if result.location and result.location.story_location:
        _apply_location(ctx, scene, result, label)

    # --- 4. Synopsis and INT/EXT suggestion ------------------------------------------------
    if result.synopsis:
        scene.synopsis = result.synopsis.strip()
        scene.synopsis_run_id = ctx.run.id
    if scene.int_ext == IntExt.UNKNOWN and result.int_ext_suggestion != "UNKNOWN" and not scene.is_omitted:
        s.add(ReviewItem(
            project_id=ctx.version.project_id, script_version_id=ctx.version.id,
            category=ReviewCategory.EXTRACTION, reason_code="int_ext_suggestion", severity=Severity.WARNING,
            message=f"Scene {label}: heading has no INT/EXT. The scene text suggests "
                    f"{result.int_ext_suggestion}. Not applied — accept to set it.",
            entity_table="scenes", entity_id=scene.id, scene_id=scene.id,
            proposal={"action": "set_int_ext", "scene_id": scene.id, "value": result.int_ext_suggestion}))
    s.flush()


def _rank(p: Presence) -> int:
    return [Presence.PRESENT, Presence.OFF_SCREEN, Presence.VOICE_ONLY, Presence.UNCERTAIN,
            Presence.MENTIONED].index(p)


def _get_location(ctx: _Ctx, name: str, parent: Location | None, kind: LocationKind, conf: float,
                  scene: Scene, evidence: str) -> Location:
    key = (parent.id if parent else None, normalize_name(name))
    loc = ctx.loc_cache.get(key)
    while loc is not None and loc.status == EntityStatus.MERGED and loc.merged_into_id:
        loc = ctx.session.get(Location, loc.merged_into_id)
    if loc is None:
        loc = Location(project_id=ctx.version.project_id, parent_id=parent.id if parent else None,
                       name=normalize_name(name), kind=kind, source_scene_id=scene.id, source_excerpt=evidence,
                       extracted_by=ExtractorKind.LLM if ctx.run.model != "heuristic" else ExtractorKind.RULES,
                       extraction_run_id=ctx.run.id, confidence=conf)
        ctx.session.add(loc)
        ctx.session.flush()
        ctx.loc_cache[key] = loc
    return loc


def _apply_location(ctx: _Ctx, scene: Scene, result: SceneExtraction, label: str) -> None:
    s = ctx.session
    lo = result.location
    conf = min(1.0, max(0.0, float(lo.confidence)))
    heading = normalize_name(scene.location_raw or "")
    reasons = []
    parent_name = normalize_name(lo.story_location)
    sub_name = normalize_name(lo.sub_location) if lo.sub_location else None
    if heading and (parent_name not in heading or (sub_name and sub_name not in heading)):
        reasons.append("location_inferred")
        conf = min(conf, 0.65)
    if not heading:
        reasons.append("location_from_text")
    if conf < REVIEW_CONFIDENCE and "low_confidence" not in reasons:
        reasons.append("low_confidence")
    parent = _get_location(ctx, parent_name, None, LocationKind.STORY_LOCATION, conf, scene, lo.evidence)
    target = parent
    if sub_name:
        target = _get_location(ctx, sub_name, parent, LocationKind.SET, conf, scene, lo.evidence)
    if scene.location_raw:
        alias = normalize_name(scene.location_raw)
        if not s.scalars(select(LocationAlias).where(LocationAlias.location_id == target.id,
                                                     LocationAlias.alias == alias)).first():
            s.add(LocationAlias(location_id=target.id, alias=alias, extracted_by=ExtractorKind.PARSER,
                                source_scene_id=scene.id, extraction_run_id=ctx.run.id))
    if s.scalars(select(SceneLocation).where(SceneLocation.scene_id == scene.id)).first():
        return  # a locked (AD-confirmed) location already exists
    sl = SceneLocation(scene_id=scene.id, location_id=target.id, role=SceneLocationRole.PRIMARY,
                       source_scene_id=scene.id, source_excerpt=scene.heading_raw or lo.evidence,
                       extracted_by=ExtractorKind.LLM if ctx.run.model != "heuristic" else ExtractorKind.RULES,
                       extraction_run_id=ctx.run.id, confidence=round(conf, 3), needs_review=bool(reasons),
                       review_reason=",".join(reasons) or None)
    s.add(sl)
    s.flush()
    if reasons:
        path = parent.name + (f" > {target.name}" if target is not parent else "")
        s.add(ReviewItem(
            project_id=ctx.version.project_id, script_version_id=ctx.version.id,
            category=ReviewCategory.EXTRACTION, reason_code=reasons[0], severity=Severity.WARNING,
            message=f"Scene {label}: location recorded as {path} from heading "
                    f"{scene.location_raw!r} ({', '.join(reasons)}).",
            entity_table="scene_locations", entity_id=sl.id, scene_id=scene.id, confidence=sl.confidence))


__all__ = ["run_extraction", "scene_text", "apply_extraction", "CharacterOut"]
