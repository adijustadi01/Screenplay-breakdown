"""Controlled vocabularies.

Rule: values are never force-collapsed. If the script says DUSK, we store DUSK,
not NIGHT. Any grouping (e.g. "treat DUSK as NIGHT for scheduling") is an explicit,
visible report option — never a stored transformation. The raw text is always
kept alongside the enum.
"""
from __future__ import annotations

import enum


class SourceFormat(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    FDX = "fdx"
    FOUNTAIN = "fountain"


class ScriptVersionStatus(str, enum.Enum):
    IMPORTING = "importing"
    IMPORTED = "imported"          # pages + elements + scenes detected
    EXTRACTED = "extracted"        # per-scene LLM extraction done
    RECONCILED = "reconciled"      # whole-script reconciliation done
    FAILED = "failed"


class PageTextSource(str, enum.Enum):
    TEXT_LAYER = "text_layer"      # PDF with embedded text
    OCR = "ocr"                    # scanned PDF, text recovered by OCR
    STRUCTURED = "structured"      # DOCX/FDX/Fountain — no physical pages,
                                   # pages computed by standard pagination


class ElementType(str, enum.Enum):
    SCENE_HEADING = "scene_heading"
    ACTION = "action"
    CHARACTER = "character"        # dialogue cue, e.g. "ARJUN (V.O.)"
    PARENTHETICAL = "parenthetical"
    DIALOGUE = "dialogue"
    TRANSITION = "transition"
    SHOT = "shot"
    PAGE_HEADER = "page_header"    # page numbers, (CONTINUED), MORE, revision marks
    TITLE_PAGE = "title_page"
    NON_PRINTING = "non_printing"  # Fountain notes/sections/synopses — not part of the shot script
    UNKNOWN = "unknown"            # could not classify -> surfaces in coverage


class IntExt(str, enum.Enum):
    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT/EXT"            # INT./EXT., EXT./INT., I/E — raw kept in int_ext_raw
    UNKNOWN = "UNKNOWN"            # heading missing/unparseable -> needs_review


class TimeOfDay(str, enum.Enum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    DAWN = "DAWN"
    DUSK = "DUSK"
    SUNRISE = "SUNRISE"
    SUNSET = "SUNSET"
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"
    # Relative times: lighting must be inherited from an earlier scene.
    CONTINUOUS = "CONTINUOUS"
    SAME = "SAME"
    LATER = "LATER"
    MOMENTS_LATER = "MOMENTS LATER"
    # No usable value.
    UNSPECIFIED = "UNSPECIFIED"    # heading has no time at all
    OTHER = "OTHER"                # something we don't recognise; raw kept, flagged


RELATIVE_TIMES = frozenset(
    {TimeOfDay.CONTINUOUS, TimeOfDay.SAME, TimeOfDay.LATER, TimeOfDay.MOMENTS_LATER}
)


class SceneNumberStatus(str, enum.Enum):
    OK = "ok"                      # number printed in script, preserved verbatim
    MISSING = "missing"            # script has no number for this scene
    MISMATCH = "mismatch"          # left/right margin numbers disagree
    DUPLICATE = "duplicate"        # same number used twice
    OUT_OF_ORDER = "out_of_order"


class HeadingStatus(str, enum.Enum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"        # looked like a heading but didn't parse cleanly
    INFERRED = "inferred"          # no heading; scene boundary inferred -> always review


class LengthMethod(str, enum.Enum):
    PDF_GEOMETRY = "pdf_geometry"  # vertical extent on the physical page
    LINE_COUNT = "line_count"      # computed pagination (DOCX/FDX/Fountain)
    SOURCE_DOCUMENT = "source_document"  # taken from the file itself (Final Draft SceneProperties)


class CharacterKind(str, enum.Enum):
    INDIVIDUAL = "individual"
    GROUP = "group"                # "GOONS", "VILLAGERS" — headcount may be unknown


class EntityStatus(str, enum.Enum):
    ACTIVE = "active"
    MERGED = "merged"              # merged_into_id points at the survivor


class CastingRoleKind(str, enum.Enum):
    ACTOR = "actor"                # on-camera performer for the character (or an age/look variant)
    VOICE = "voice"                # voice-only performer
    STUNT_DOUBLE = "stunt_double"
    PHOTO_DOUBLE = "photo_double"
    BODY_DOUBLE = "body_double"


class Presence(str, enum.Enum):
    """How a character relates to a scene. Only PRESENT and OFF_SCREEN are appearances."""

    PRESENT = "present"            # physically in the scene, speaking or silent
    OFF_SCREEN = "off_screen"      # (O.S.)/(O.C.) — in the scene's space, unseen; actor usually needed
    VOICE_ONLY = "voice_only"      # (V.O.), phone/radio voice — not physically there
    MENTIONED = "mentioned"        # talked about only. NEVER an appearance.
    UNCERTAIN = "uncertain"        # cannot tell from the text -> always needs_review


APPEARANCE_PRESENCES = frozenset({Presence.PRESENT, Presence.OFF_SCREEN})


class Speaking(str, enum.Enum):
    SPEAKING = "speaking"
    SILENT = "silent"
    UNKNOWN = "unknown"


class LocationKind(str, enum.Enum):
    STORY_LOCATION = "story_location"  # "ARJUN'S HOUSE", "CITY HOSPITAL"
    SET = "set"                        # sub-location: "KITCHEN", "ICU CORRIDOR"


class SceneLocationRole(str, enum.Enum):
    PRIMARY = "primary"
    INTERCUT = "intercut"          # INTERCUT / phone-call cross-cutting
    SECONDARY = "secondary"        # scene moves through a second set


class ExtractorKind(str, enum.Enum):
    PARSER = "parser"              # deterministic code (headings, cues)
    OCR = "ocr"
    LLM = "llm"
    RULES = "rules"                # offline rule-based extractor (no LLM)
    RECONCILIATION = "reconciliation"
    HUMAN = "human"                # added by the AD


class ReviewStatus(str, enum.Enum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    EDITED = "edited"
    REJECTED = "rejected"          # kept for audit, excluded from every report


class RunKind(str, enum.Enum):
    IMPORT = "import"
    SCENE_EXTRACTION = "scene_extraction"
    DEPARTMENT_EXTRACTION = "department_extraction"
    RECONCILIATION = "reconciliation"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"            # some scenes failed; failures are review items
    FAILED = "failed"


class ReviewCategory(str, enum.Enum):
    IMPORT = "import"              # coverage, OCR, headings, numbering
    EXTRACTION = "extraction"      # low-confidence / uncertain extracted facts
    RECONCILIATION = "reconciliation"  # proposed merges / links


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"                # e.g. text not assigned to any scene


class ReviewItemStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Resolution(str, enum.Enum):
    CONFIRMED = "confirmed"
    EDITED = "edited"
    REJECTED = "rejected"
    PROPOSAL_ACCEPTED = "proposal_accepted"
    PROPOSAL_DECLINED = "proposal_declined"


class Department(str, enum.Enum):
    """Stage 2 departmental breakdowns. Each has its own module in app/departments/."""

    VEHICLES = "vehicles"
    PROPS = "props"
    COSTUMES = "costumes"
    MAKEUP_HAIR = "makeup_hair"
    BACKGROUND = "background"
    ANIMALS = "animals"
    ACTION_EFFECTS = "action_effects"
    SOUND_MUSIC = "sound_music"
    PRODUCTION = "production"
