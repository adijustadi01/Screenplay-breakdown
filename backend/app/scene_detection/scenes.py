"""Scene detection over typed elements. Deterministic; every doubt becomes an issue."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..importers.base import ImportIssue, RawElement
from ..models.enums import (
    RELATIVE_TIMES,
    ElementType,
    HeadingStatus,
    IntExt,
    SceneNumberStatus,
    Severity,
    TimeOfDay,
)
from .headings import ParsedHeading, parse_heading, scene_number_sort_key

LONG_SCENE_EIGHTHS = 6 * 8   # a scene this long may hide a missed heading
NON_STORY = {ElementType.PAGE_HEADER, ElementType.NON_PRINTING, ElementType.TITLE_PAGE}
ABSOLUTE_TIMES = {t for t in TimeOfDay if t not in RELATIVE_TIMES
                  and t not in (TimeOfDay.UNSPECIFIED, TimeOfDay.OTHER)}


@dataclass
class DetectedScene:
    seq: int
    start: int                       # element index of heading (or first element if inferred)
    end: int                         # last element index (inclusive)
    heading: ParsedHeading
    number_status: SceneNumberStatus = SceneNumberStatus.OK
    effective_time: TimeOfDay | None = None
    effective_time_source: int | None = None   # seq of the scene the time was inherited from
    flags: list[str] = field(default_factory=list)


def detect_scenes(elements: list[RawElement]) -> tuple[list[DetectedScene], list[ImportIssue]]:
    issues: list[ImportIssue] = []
    heading_idx = [i for i, e in enumerate(elements) if e.element_type == ElementType.SCENE_HEADING]
    scenes: list[DetectedScene] = []

    # --- Text before the first heading ---------------------------------------------------
    first = heading_idx[0] if heading_idx else len(elements)
    pre = [i for i in range(first) if elements[i].element_type not in NON_STORY]
    story_pre = [i for i in pre if elements[i].element_type != ElementType.TRANSITION]
    if story_pre:
        has_dialogue = any(elements[i].element_type == ElementType.CHARACTER for i in story_pre)
        lines = sum(elements[i].line_count for i in story_pre)
        fade_in = next((i for i in pre if "FADE IN" in elements[i].text.upper()), None)
        if fade_in is not None and all(i < fade_in for i in story_pre):
            for i in story_pre:
                elements[i].element_type = ElementType.TITLE_PAGE
            issues.append(ImportIssue("pre_script_text_as_title",
                                      f"{len(story_pre)} element(s) before FADE IN treated as title page.",
                                      Severity.INFO, elements[story_pre[0]].page_index, story_pre[0]))
        elif not has_dialogue and lines <= 30 and heading_idx:
            for i in story_pre:
                elements[i].element_type = ElementType.TITLE_PAGE
            issues.append(ImportIssue("pre_script_text_as_title",
                                      f"{len(story_pre)} element(s) before the first scene heading treated "
                                      "as title page — confirm none of it is story.",
                                      Severity.WARNING, elements[story_pre[0]].page_index, story_pre[0]))
        else:
            # Story text with no heading: keep it as an INFERRED scene so it is extracted,
            # and flag it — never silently attach it elsewhere or drop it.
            ph = ParsedHeading(text="", status=HeadingStatus.INFERRED, confidence=0.2,
                               flags=["no_heading"])
            ph.int_ext = IntExt.UNKNOWN
            end = (heading_idx[0] - 1) if heading_idx else len(elements) - 1
            scenes.append(DetectedScene(0, story_pre[0], end, ph, SceneNumberStatus.MISSING))
            issues.append(ImportIssue("missing_heading",
                                      "Story text found before the first scene heading. Kept as an "
                                      "inferred scene with no heading — add or confirm the heading.",
                                      Severity.ERROR, elements[story_pre[0]].page_index, story_pre[0]))

    # --- One scene per heading ----------------------------------------------------------
    for n, hi in enumerate(heading_idx):
        el = elements[hi]
        end = heading_idx[n + 1] - 1 if n + 1 < len(heading_idx) else len(elements) - 1
        ph = parse_heading(el.raw_text if el.scene_number_left is None else el.text,
                           el.scene_number_left, el.scene_number_right)
        if el.classification_confidence < 1.0:
            ph.confidence = min(ph.confidence, el.classification_confidence)
            if ph.status == HeadingStatus.OK and el.classification_confidence < 0.7:
                ph.status = HeadingStatus.AMBIGUOUS
                ph.flags.append("weak_heading_detection")
        scenes.append(DetectedScene(0, hi, end, ph))

    for i, s in enumerate(scenes, start=1):
        s.seq = i

    _check_numbers(scenes, elements, issues)
    _inherit_times(scenes)

    for s in scenes:
        h = s.heading
        el = elements[s.start]
        if h.status != HeadingStatus.INFERRED and el.line_count > 1:
            issues.append(ImportIssue("multiline_heading",
                                      f"Scene {h.scene_number or f'#{s.seq}'}: heading spans {el.line_count} "
                                      f"lines ({el.raw_text!r}) — check it is not two headings merged.",
                                      Severity.WARNING, el.page_index, s.start))
        if h.status == HeadingStatus.AMBIGUOUS:
            issues.append(ImportIssue("ambiguous_heading",
                                      f"Scene {h.scene_number or f'#{s.seq}'}: heading {h.text!r} did not "
                                      f"parse cleanly ({', '.join(h.flags)}).",
                                      Severity.WARNING, el.page_index, s.start))
        elif not h.is_omitted and h.time_of_day in (TimeOfDay.UNSPECIFIED, TimeOfDay.OTHER):
            issues.append(ImportIssue("time_of_day_unclear",
                                      f"Scene {h.scene_number or f'#{s.seq}'}: time of day "
                                      f"{h.time_of_day_raw or 'missing'!r} — stored as {h.time_of_day.value}.",
                                      Severity.WARNING, el.page_index, s.start))
        if s.effective_time_source is not None or h.time_of_day in RELATIVE_TIMES:
            src_scene = scenes[s.effective_time_source - 1] if s.effective_time_source else None
            src = (f"scene {src_scene.heading.scene_number or f'#{src_scene.seq}'}" if src_scene
                   else "no earlier scene (unresolved)")
            eff = s.effective_time.value if s.effective_time else "unknown"
            issues.append(ImportIssue("relative_time_inherited",
                                      f"Scene {h.scene_number or f'#{s.seq}'}: {h.time_of_day.value} — "
                                      f"lighting inherited as {eff} from {src}.",
                                      Severity.INFO, el.page_index, s.start))
    return scenes, issues


def _check_numbers(scenes: list[DetectedScene], elements, issues: list[ImportIssue]) -> None:
    numbered = [s for s in scenes if s.heading.scene_number]
    real = [s for s in scenes if s.heading.status != HeadingStatus.INFERRED]
    if real and not numbered:
        for s in scenes:
            s.number_status = SceneNumberStatus.MISSING
        issues.append(ImportIssue("script_unnumbered",
                                  "The script has no scene numbers. Scenes are identified by order "
                                  "only; numbers were NOT generated.", Severity.INFO))
        return
    counts = Counter(s.heading.scene_number for s in numbered)
    prev_key = None
    for s in scenes:
        h = s.heading
        page = elements[s.start].page_index
        if h.status == HeadingStatus.INFERRED:
            continue
        if not h.scene_number:
            s.number_status = SceneNumberStatus.MISSING
            issues.append(ImportIssue("missing_scene_number",
                                      f"Scene at seq {s.seq} ({h.text!r}) has no scene number.",
                                      Severity.WARNING, page, s.start))
            continue
        if h.scene_number_left and h.scene_number_right and h.scene_number_left != h.scene_number_right:
            s.number_status = SceneNumberStatus.MISMATCH
            issues.append(ImportIssue("scene_number_mismatch",
                                      f"Scene numbers differ in the margins: {h.scene_number_left} / "
                                      f"{h.scene_number_right}.", Severity.WARNING, page, s.start))
        if counts[h.scene_number] > 1:
            s.number_status = SceneNumberStatus.DUPLICATE
            issues.append(ImportIssue("duplicate_scene_number",
                                      f"Scene number {h.scene_number} is used {counts[h.scene_number]} times.",
                                      Severity.WARNING, page, s.start))
        key = scene_number_sort_key(h.scene_number)
        if key is not None and prev_key is not None and key < prev_key and \
                s.number_status == SceneNumberStatus.OK:
            s.number_status = SceneNumberStatus.OUT_OF_ORDER
            issues.append(ImportIssue("scene_number_out_of_order",
                                      f"Scene {h.scene_number} comes after a higher number.",
                                      Severity.WARNING, page, s.start))
        if key is not None:
            prev_key = key


def _inherit_times(scenes: list[DetectedScene]) -> None:
    """CONTINUOUS / SAME / LATER: walk back to the nearest scene with an absolute time."""
    for i, s in enumerate(scenes):
        if s.heading.time_of_day not in RELATIVE_TIMES:
            continue
        for j in range(i - 1, -1, -1):
            prev = scenes[j]
            if prev.heading.is_omitted:
                continue
            t = prev.heading.time_of_day
            if t in ABSOLUTE_TIMES:
                s.effective_time, s.effective_time_source = t, prev.seq
                break
            if t in RELATIVE_TIMES and prev.effective_time is not None:
                s.effective_time, s.effective_time_source = prev.effective_time, prev.effective_time_source
                break
            if t in (TimeOfDay.UNSPECIFIED, TimeOfDay.OTHER):
                break  # chain broken by an unknown time: leave unresolved
