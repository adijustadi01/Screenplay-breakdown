"""Coverage check: prove every page and every line of text was accounted for.

Computed from the database (not from import-time memory), so it can be re-run
at any time and always reflects the stored truth.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExtractionRun, Page, ReviewItem, Scene, ScriptElement, ScriptVersion
from ..models.enums import (
    RELATIVE_TIMES,
    ElementType,
    HeadingStatus,
    PageTextSource,
    ReviewCategory,
    ReviewItemStatus,
    RunKind,
    SceneNumberStatus,
    Severity,
    TimeOfDay,
)

NON_SCENE_TYPES = {ElementType.TITLE_PAGE, ElementType.PAGE_HEADER, ElementType.NON_PRINTING}


def coverage_report(session: Session, version_id: int) -> dict[str, Any]:
    v = session.get(ScriptVersion, version_id)
    if v is None:
        raise KeyError(version_id)
    pages = session.scalars(select(Page).where(Page.script_version_id == version_id)
                            .order_by(Page.page_index)).all()
    elements = session.scalars(select(ScriptElement).where(ScriptElement.script_version_id == version_id)
                               .order_by(ScriptElement.seq)).all()
    scenes = session.scalars(select(Scene).where(Scene.script_version_id == version_id)
                             .order_by(Scene.seq)).all()
    items = session.scalars(select(ReviewItem).where(ReviewItem.script_version_id == version_id,
                                                     ReviewItem.category == ReviewCategory.IMPORT)).all()
    run = session.scalars(select(ExtractionRun).where(ExtractionRun.script_version_id == version_id,
                                                      ExtractionRun.kind == RunKind.IMPORT)
                          .order_by(ExtractionRun.id.desc())).first()
    page_label = {p.id: (p.page_label or f"[{p.page_index}]") for p in pages}

    # --- pages -------------------------------------------------------------------------
    els_by_page: dict[int, list[ScriptElement]] = {}
    for e in elements:
        els_by_page.setdefault(e.page_id, []).append(e)
    pages_no_scene_text = []
    empty_pages = []
    for p in pages:
        if p.is_title_page:
            continue
        pe = els_by_page.get(p.id, [])
        if not pe:
            empty_pages.append(page_label[p.id])
        elif not any(e.scene_id for e in pe if e.element_type not in NON_SCENE_TYPES):
            # A page with only furniture (e.g. blank page with a number) or only pre-scene text.
            pages_no_scene_text.append(page_label[p.id])
    low_ocr = [{"page": page_label[p.id], "confidence": round(p.ocr_mean_confidence or 0, 1)}
               for p in pages if p.needs_review]

    # --- elements ------------------------------------------------------------------------
    by_type = Counter(e.element_type.value for e in elements)
    in_scenes = sum(1 for e in elements if e.scene_id and e.element_type not in NON_SCENE_TYPES)
    non_scene = sum(1 for e in elements if e.element_type in NON_SCENE_TYPES)
    pre_scene_transitions = [e for e in elements if e.scene_id is None
                             and e.element_type == ElementType.TRANSITION]
    unassigned = [e for e in elements if e.scene_id is None and e.element_type not in NON_SCENE_TYPES
                  and e.element_type != ElementType.TRANSITION]
    unknown = [e for e in elements if e.element_type == ElementType.UNKNOWN]

    # --- scenes ---------------------------------------------------------------------------
    number_status = Counter(s.number_status.value for s in scenes)
    heading_status = Counter(s.heading_status.value for s in scenes)
    times = Counter(s.time_of_day.value for s in scenes)

    stats = run.stats if run and run.stats else {}
    src, got = stats.get("source_chars"), stats.get("element_chars")

    open_items = [i for i in items if i.status == ReviewItemStatus.OPEN]
    sev = Counter(i.severity.value for i in open_items)
    codes = Counter(i.reason_code for i in open_items)

    text_ok = src is not None and src == got
    complete = text_ok and not unassigned and sev.get(Severity.ERROR.value, 0) == 0

    return {
        "script_version_id": v.id,
        "file": v.source_filename,
        "format": v.source_format.value,
        "verdict": "COMPLETE" if complete else "ATTENTION NEEDED",
        "pages": {
            "total": len(pages),
            "title_pages": sum(1 for p in pages if p.is_title_page),
            "text_layer": sum(1 for p in pages if p.text_source == PageTextSource.TEXT_LAYER),
            "ocr": sum(1 for p in pages if p.text_source == PageTextSource.OCR),
            "computed": sum(1 for p in pages if p.text_source == PageTextSource.STRUCTURED),
            "low_ocr_confidence": low_ocr,
            "empty": empty_pages,
            "without_scene_text": pages_no_scene_text,
        },
        "text_accounting": {
            "source_characters": src,
            "characters_in_elements": got,
            "difference": (src - got) if src is not None and got is not None else None,
            "ok": text_ok,
        },
        "elements": {
            "total": len(elements),
            "by_type": dict(sorted(by_type.items())),
            "in_scenes": in_scenes,
            "non_scene": non_scene,
            "transitions_before_first_scene": len(pre_scene_transitions),
            "unassigned": [_el(e, page_label) for e in unassigned],
            "unclassified": [_el(e, page_label) for e in unknown],
        },
        "scenes": {
            "total": len(scenes),
            "omitted": sum(1 for s in scenes if s.is_omitted),
            "numbered": sum(1 for s in scenes if s.scene_number),
            "number_status": dict(number_status),
            "heading_status": dict(heading_status),
            "time_of_day": dict(sorted(times.items())),
            "relative_time_scenes": sum(1 for s in scenes if s.time_of_day in RELATIVE_TIMES),
            "unclear_time": sum(1 for s in scenes if not s.is_omitted and s.time_of_day in
                                (TimeOfDay.UNSPECIFIED, TimeOfDay.OTHER)),
            "inferred_headings": heading_status.get(HeadingStatus.INFERRED.value, 0),
            "number_problems": [
                {"seq": s.seq, "number": s.scene_number, "status": s.number_status.value,
                 "heading": s.heading_raw, "page": s.start_page_label}
                for s in scenes if s.number_status != SceneNumberStatus.OK
                and number_status.get("missing", 0) != len(scenes)
            ],
            "total_eighths": sum(s.length_eighths or 0 for s in scenes),
        },
        "review_queue": {
            "open": len(open_items),
            "by_severity": dict(sev),
            "by_reason": dict(codes.most_common()),
            "items": [
                {"id": i.id, "severity": i.severity.value, "reason": i.reason_code, "message": i.message,
                 "page": page_label.get(i.page_id) if i.page_id else None, "scene_id": i.scene_id}
                for i in sorted(open_items, key=lambda i: (_SEV_ORDER[i.severity], i.id))
            ],
        },
    }


_SEV_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def _el(e: ScriptElement, page_label: dict[int, str]) -> dict[str, Any]:
    return {"element_id": e.id, "page": page_label.get(e.page_id), "type": e.element_type.value,
            "text": e.text[:120]}


def format_coverage(r: dict[str, Any]) -> str:
    """Plain-text rendering for the CLI."""
    from ..scene_detection.pagination import format_eighths

    p, t, e, s, q = r["pages"], r["text_accounting"], r["elements"], r["scenes"], r["review_queue"]
    ok = lambda b: "OK " if b else "!! "  # noqa: E731
    lines = [
        f"COVERAGE CHECK — {r['file']} ({r['format'].upper()})",
        f"Verdict: {r['verdict']}",
        "",
        "PAGES",
        f"  {p['total']} pages ({p['title_pages']} title, {p['text_layer']} text layer, {p['ocr']} OCR, "
        f"{p['computed']} computed)",
        f"  {ok(not p['low_ocr_confidence'])}low OCR confidence: "
        + (", ".join(f"p{x['page']} ({x['confidence']})" for x in p['low_ocr_confidence']) or "none"),
        f"  {ok(not p['empty'])}empty pages: {', '.join(p['empty']) or 'none'}",
        f"  {ok(not p['without_scene_text'])}pages with no scene text: {', '.join(p['without_scene_text']) or 'none'}",
        "",
        "TEXT ACCOUNTING (non-space characters)",
        f"  {ok(t['ok'])}source {t['source_characters']}  ->  in elements {t['characters_in_elements']}  "
        f"(difference {t['difference']})",
        "",
        "ELEMENTS",
        f"  {e['total']} total: {e['in_scenes']} in scenes, {e['non_scene']} non-scene (title/page furniture/notes), "
        f"{e['transitions_before_first_scene']} transition(s) before first scene",
        f"  {ok(not e['unassigned'])}unassigned to any scene: {len(e['unassigned'])}",
        *[f"      p{x['page']}: {x['text']!r}" for x in e["unassigned"][:20]],
        f"  {ok(not e['unclassified'])}unclassified text: {len(e['unclassified'])}",
        *[f"      p{x['page']}: {x['text']!r}" for x in e["unclassified"][:20]],
        "",
        "SCENES",
        f"  {s['total']} scenes ({s['omitted']} omitted), {s['numbered']} numbered, "
        f"total length {format_eighths(s['total_eighths'])} pages",
        f"  numbering: {s['number_status']}",
        f"  headings:  {s['heading_status']}",
        f"  time of day (as written): {s['time_of_day']}",
        f"  relative times (inherited, flagged): {s['relative_time_scenes']}   unclear: {s['unclear_time']}",
        *[f"      seq {x['seq']} #{x['number'] or '—'} {x['status']}: {x['heading']!r} (p{x['page']})"
          for x in s["number_problems"]],
        "",
        f"REVIEW QUEUE — {q['open']} open {q['by_severity']}",
        *[f"  [{i['severity'].upper():7}] {i['reason']}: {i['message']}" for i in q["items"][:40]],
    ]
    if len(q["items"]) > 40:
        lines.append(f"  ... and {len(q['items']) - 40} more")
    return "\n".join(lines)
