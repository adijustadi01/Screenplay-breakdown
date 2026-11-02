"""Format-independent intermediate representation produced by every importer.

Importers only turn a file into pages + typed elements. Scene detection, page
lengths and persistence are format-independent and live elsewhere, so every
format goes through exactly the same rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models.enums import ElementType, PageTextSource, Severity, SourceFormat


@dataclass
class RawElement:
    element_type: ElementType
    text: str                       # normalised (whitespace collapsed, markup stripped)
    raw_text: str                   # as found in the source
    page_index: int | None = None   # 1-based; None until paginated (structured formats)
    y_top: float | None = None      # points (PDF) or line number (structured)
    y_bottom: float | None = None
    line_count: int = 1
    scene_number_left: str | None = None   # margin numbers found by geometry/markup
    scene_number_right: str | None = None
    ocr_confidence: float | None = None    # 0–100
    classification_confidence: float = 1.0
    is_dual_dialogue: bool = False


@dataclass
class RawPage:
    page_index: int                 # 1-based
    text_source: PageTextSource
    raw_text: str = ""
    page_label: str | None = None
    ocr_mean_confidence: float | None = None
    is_title_page: bool = False
    body_top: float = 0.0           # vertical extent used for 1/8-page maths
    body_bottom: float = 0.0


@dataclass
class ImportIssue:
    code: str
    message: str
    severity: Severity = Severity.WARNING
    page_index: int | None = None
    element_index: int | None = None  # index into ImportResult.elements


@dataclass
class SceneLengthHint:
    """A scene length/page stated by the source file itself (Final Draft)."""

    length_eighths: int | None
    page_label: str | None


@dataclass
class ImportResult:
    source_format: SourceFormat
    elements: list[RawElement]
    pages: list[RawPage] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    # Non-whitespace characters found in the source vs. placed in elements.
    # Any difference means text was dropped -> coverage ERROR.
    source_char_count: int = 0
    # element index of a scene heading -> length stated in the file
    length_hints: dict[int, SceneLengthHint] = field(default_factory=dict)
    needs_pagination: bool = False  # True for DOCX/FDX/Fountain
    title: str | None = None


def nonspace_len(s: str) -> int:
    return sum(1 for ch in s if not ch.isspace())
