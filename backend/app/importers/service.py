"""Import orchestration: file -> pages/elements -> scenes -> lengths -> database."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..models import ExtractionRun, Page, ReviewItem, Scene, ScriptElement, ScriptVersion
from ..models.enums import (
    RELATIVE_TIMES,
    ElementType,
    ExtractorKind,
    HeadingStatus,
    LengthMethod,
    PageTextSource,
    ReviewCategory,
    RunKind,
    RunStatus,
    SceneNumberStatus,
    ScriptVersionStatus,
    Severity,
    SourceFormat,
)
from ..scene_detection.pagination import compute_eighths, paginate_structured
from ..scene_detection.scenes import LONG_SCENE_EIGHTHS, NON_STORY, detect_scenes
from .base import ImportIssue, ImportResult, nonspace_len

PARSER_VERSION = "import-1.0"
LOW_CLASSIFICATION = 0.7
LOW_OCR_PAGE = 75.0

EXTENSIONS = {
    ".pdf": SourceFormat.PDF,
    ".docx": SourceFormat.DOCX,
    ".fdx": SourceFormat.FDX,
    ".fountain": SourceFormat.FOUNTAIN,
    ".spmd": SourceFormat.FOUNTAIN,
    ".txt": SourceFormat.FOUNTAIN,
}


class UnsupportedFormat(ValueError):
    pass


def detect_format(path: Path) -> SourceFormat:
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF"):
        return SourceFormat.PDF
    fmt = EXTENSIONS.get(path.suffix.lower())
    if fmt is None:
        raise UnsupportedFormat(f"Unsupported file type: {path.suffix or path.name}")
    return fmt


def run_importer(path: Path, fmt: SourceFormat, ocr_lang: str = "eng") -> ImportResult:
    if fmt == SourceFormat.PDF:
        from .pdf import import_pdf
        return import_pdf(path, ocr_lang=ocr_lang)
    if fmt == SourceFormat.DOCX:
        from .docx_importer import import_docx
        return import_docx(path)
    if fmt == SourceFormat.FDX:
        from .fdx import import_fdx
        return import_fdx(path)
    from .fountain import import_fountain
    return import_fountain(path)


def import_script(
    session: Session,
    project_id: int,
    path: str | Path,
    label: str | None = None,
    ocr_lang: str = "eng",
    original_filename: str | None = None,
) -> ScriptVersion:
    path = Path(path)
    fmt = detect_format(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    version = ScriptVersion(
        project_id=project_id, label=label, source_filename=original_filename or path.name,
        source_format=fmt, source_sha256=sha, status=ScriptVersionStatus.IMPORTING,
    )
    session.add(version)
    session.flush()
    run = ExtractionRun(script_version_id=version.id, kind=RunKind.IMPORT, prompt_version=PARSER_VERSION,
                        params={"ocr_lang": ocr_lang})
    session.add(run)
    session.flush()

    try:
        result = run_importer(path, fmt, ocr_lang)
        _persist(session, version, run, result)
    except Exception as exc:
        version.status = ScriptVersionStatus.FAILED
        run.status = RunStatus.FAILED
        run.error = repr(exc)
        run.finished_at = utcnow()
        session.flush()
        raise
    return version


def _persist(session: Session, version: ScriptVersion, run: ExtractionRun, result: ImportResult) -> None:
    elements = result.elements
    issues: list[ImportIssue] = list(result.issues)

    scenes, scene_issues = detect_scenes(elements)  # may retype pre-heading text as TITLE_PAGE
    issues.extend(scene_issues)
    pages = result.pages if not result.needs_pagination else paginate_structured(elements)

    # --- Lengths --------------------------------------------------------------------------
    live = [s for s in scenes if not s.heading.is_omitted]
    spans = compute_eighths(pages, elements, [s.start for s in live], [s.end for s in live])
    span_by_seq = {s.seq: sp for s, sp in zip(live, spans)}
    method = LengthMethod.PDF_GEOMETRY if result.source_format == SourceFormat.PDF else LengthMethod.LINE_COUNT
    hints = result.length_hints
    real_heads = [s.start for s in scenes if s.heading.status != HeadingStatus.INFERRED]
    use_hints = bool(real_heads) and all(
        h in hints and hints[h].length_eighths is not None for h in real_heads
        if not elements[h].text.upper().startswith("OMITTED")
    )
    if use_hints:
        method = LengthMethod.SOURCE_DOCUMENT

    # --- Pages ----------------------------------------------------------------------------
    page_rows: dict[int, Page] = {}
    for rp in pages:
        low = rp.text_source == PageTextSource.OCR and (rp.ocr_mean_confidence or 0) < LOW_OCR_PAGE
        row = Page(script_version_id=version.id, page_index=rp.page_index, page_label=rp.page_label,
                   text_source=rp.text_source, raw_text=rp.raw_text,
                   ocr_mean_confidence=rp.ocr_mean_confidence, is_title_page=rp.is_title_page,
                   needs_review=low)
        page_rows[rp.page_index] = row
    session.add_all(page_rows.values())
    session.flush()
    version.page_count = len(pages)

    # --- Scenes ---------------------------------------------------------------------------
    scene_rows = []
    for s in scenes:
        h = s.heading
        sp = span_by_seq.get(s.seq)
        start_el, end_el = elements[s.start], elements[s.end]
        sp_page = sp.start_page if sp else start_el.page_index
        ep_page = sp.end_page if sp else end_el.page_index
        eighths = 0 if h.is_omitted else (sp.eighths if sp else None)
        start_label = page_rows[sp_page].page_label if sp_page in page_rows else None
        if use_hints and s.start in hints:
            eighths = 0 if h.is_omitted else hints[s.start].length_eighths
            start_label = hints[s.start].page_label or start_label
        reasons = list(h.flags)
        if s.number_status != SceneNumberStatus.OK:
            reasons.append(f"number_{s.number_status.value}")
        if h.time_of_day in RELATIVE_TIMES:
            reasons.append("relative_time")
        needs = bool(reasons) and not (h.is_omitted and not h.flags)
        row = Scene(
            script_version_id=version.id, seq=s.seq,
            scene_number=h.scene_number, scene_number_right=(
                h.scene_number_right if h.scene_number_right != h.scene_number_left else None),
            number_status=s.number_status, is_omitted=h.is_omitted,
            heading_raw=start_el.raw_text if h.status != HeadingStatus.INFERRED else None,
            heading_status=h.status, int_ext=h.int_ext, int_ext_raw=h.int_ext_raw,
            location_raw=h.location_raw, time_of_day=h.time_of_day, time_of_day_raw=h.time_of_day_raw,
            effective_time_of_day=s.effective_time,
            effective_time_needs_review=h.time_of_day in RELATIVE_TIMES,
            start_page_id=page_rows[sp_page].id if sp_page in page_rows else None,
            end_page_id=page_rows[ep_page].id if ep_page in page_rows else None,
            start_page_label=start_label,
            end_page_label=page_rows[ep_page].page_label if ep_page in page_rows else None,
            length_eighths=eighths, length_method=method if eighths is not None else None,
            source_excerpt=start_el.raw_text[:500], extracted_by=ExtractorKind.PARSER,
            extraction_run_id=run.id, confidence=round(h.confidence, 3), needs_review=needs,
            review_reason=",".join(reasons)[:255] or None,
        )
        scene_rows.append(row)
    session.add_all(scene_rows)
    session.flush()
    seq_to_id = {r.seq: r.id for r in scene_rows}
    for s, row in zip(scenes, scene_rows):
        if s.effective_time_source is not None:
            row.effective_time_source_scene_id = seq_to_id[s.effective_time_source]
        row.source_scene_id = row.id

    # --- Elements -------------------------------------------------------------------------
    owner: list[int | None] = [None] * len(elements)
    for s, row in zip(scenes, scene_rows):
        for i in range(s.start, s.end + 1):
            if elements[i].element_type != ElementType.TITLE_PAGE:
                owner[i] = row.id
    el_rows = []
    for i, el in enumerate(elements):
        el_rows.append(ScriptElement(
            script_version_id=version.id, page_id=page_rows[el.page_index].id, scene_id=owner[i],
            seq=i + 1, element_type=el.element_type, text=el.text, raw_text=el.raw_text,
            line_count=el.line_count, y_top=el.y_top, y_bottom=el.y_bottom,
            is_dual_dialogue=el.is_dual_dialogue, ocr_confidence=el.ocr_confidence,
            classification_confidence=el.classification_confidence,
        ))
    session.add_all(el_rows)
    session.flush()
    for s, row in zip(scenes, scene_rows):
        row.source_element_start_id = el_rows[s.start].id
        row.source_element_end_id = el_rows[s.end].id

    # --- Extra checks -----------------------------------------------------------------------
    for s, row in zip(scenes, scene_rows):
        if row.length_eighths and row.length_eighths > LONG_SCENE_EIGHTHS:
            issues.append(ImportIssue(
                "long_scene", f"Scene {row.scene_number or f'#{row.seq}'} runs {row.length_eighths / 8:.1f} "
                "pages — check for a missed scene heading.", Severity.INFO, elements[s.start].page_index, s.start))
    for i, el in enumerate(elements):
        if el.element_type == ElementType.UNKNOWN:
            issues.append(ImportIssue("unclassified_text", f"Could not classify: {el.text[:80]!r}",
                                      Severity.WARNING, el.page_index, i))
        elif el.element_type not in NON_STORY and el.classification_confidence < LOW_CLASSIFICATION:
            issues.append(ImportIssue(
                "uncertain_element_type",
                f"Typed as {el.element_type.value} with low confidence: {el.text[:80]!r}",
                Severity.INFO, el.page_index, i))
        if owner[i] is None and el.element_type not in NON_STORY and el.element_type != ElementType.TRANSITION:
            issues.append(ImportIssue("unassigned_text", f"Text not assigned to any scene: {el.text[:80]!r}",
                                      Severity.ERROR, el.page_index, i))

    element_chars = sum(nonspace_len(e.raw_text) for e in elements)
    diff = result.source_char_count - element_chars
    if diff != 0:
        issues.append(ImportIssue(
            "text_not_accounted",
            f"{result.source_char_count} characters in the source but {element_chars} placed in "
            f"elements (difference {diff:+d}). Some text may have been dropped or duplicated.",
            Severity.ERROR))
    if result.source_format == SourceFormat.PDF:
        by_page: dict[int, int] = {}
        for e in elements:
            by_page[e.page_index] = by_page.get(e.page_index, 0) + nonspace_len(e.raw_text)
        for rp in pages:
            got, want = by_page.get(rp.page_index, 0), nonspace_len(rp.raw_text)
            if got != want:
                issues.append(ImportIssue(
                    "page_text_not_accounted",
                    f"Page {rp.page_label or rp.page_index}: {want} characters on the page, {got} in elements.",
                    Severity.ERROR, rp.page_index))

    # --- Review items -----------------------------------------------------------------------
    page_by_id = {p.id: p for p in page_rows.values()}
    for iss in issues:
        el_row = el_rows[iss.element_index] if iss.element_index is not None else None
        page_row = page_rows.get(iss.page_index) if iss.page_index is not None else None
        if page_row is None and el_row is not None:
            page_row = page_by_id.get(el_row.page_id)
        if el_row is not None and el_row.element_type == ElementType.SCENE_HEADING and el_row.scene_id:
            entity = ("scenes", el_row.scene_id)
        elif el_row is not None:
            entity = ("script_elements", el_row.id)
        elif page_row is not None:
            entity = ("pages", page_row.id)
        else:
            entity = ("script_versions", version.id)
        session.add(ReviewItem(
            project_id=version.project_id, script_version_id=version.id, category=ReviewCategory.IMPORT,
            reason_code=iss.code, severity=iss.severity, message=iss.message,
            entity_table=entity[0], entity_id=entity[1],
            scene_id=el_row.scene_id if el_row is not None else None,
            page_id=page_row.id if page_row else None,
            element_id=el_row.id if el_row is not None else None,
        ))

    version.status = ScriptVersionStatus.IMPORTED
    if result.title and not version.label:
        version.label = result.title[:120]
    run.status = RunStatus.SUCCEEDED
    run.finished_at = utcnow()
    run.stats = {
        "pages": len(pages),
        "elements": len(elements),
        "scenes": len(scenes),
        "source_chars": result.source_char_count,
        "element_chars": element_chars,
        "length_method": method.value,
        "issues": len(issues),
        "time_values": sorted({s.heading.time_of_day.value for s in scenes}),
    }
    session.flush()
