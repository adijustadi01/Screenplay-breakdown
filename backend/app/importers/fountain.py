"""Fountain (.fountain) importer. https://fountain.io/syntax

Fountain is plain text with typed elements, so element typing here is
near-certain. Pages are computed later by standard pagination.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models.enums import ElementType, Severity, SourceFormat
from ..scene_detection.headings import has_heading_prefix
from .base import ImportIssue, ImportResult, RawElement, nonspace_len
from .layout import is_transition_text

_TITLE_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s*(.*)$")
_BONEYARD_RE = re.compile(r"/\*.*?\*/", re.S)
_NOTE_RE = re.compile(r"\[\[.*?\]\]", re.S)
_EMPHASIS_RE = re.compile(r"(\*{1,3}|_)(?=\S)(.+?)(?<=\S)\1")
_CUE_RE = re.compile(r"^[^a-z]*[A-Z][^a-z]*$")  # no lowercase outside the extension
_TRANSITION_RE = re.compile(r"^[A-Z0-9 .'\-]+TO:$")


def _strip_markup(s: str) -> str:
    s = _NOTE_RE.sub("", s)
    s = _EMPHASIS_RE.sub(r"\2", s)
    return " ".join(s.replace("\\*", "*").split())


def _is_cue(line: str) -> bool:
    if line.startswith("@"):
        return True
    core = re.sub(r"\(.*?\)", "", line).replace("^", "").strip()
    return bool(core) and bool(_CUE_RE.match(core)) and any(c.isalpha() for c in core)


def parse_fountain_text(text: str) -> ImportResult:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    issues: list[ImportIssue] = []
    elements: list[RawElement] = []
    title = None

    # Title page: key: value block at the very top, ended by a blank line.
    lines = text.split("\n")
    i = 0
    if lines and _TITLE_KEY_RE.match(lines[0]) and not has_heading_prefix(lines[0]):
        block: list[str] = []
        while i < len(lines) and lines[i].strip():
            block.append(lines[i])
            i += 1
        for b in block:
            m = _TITLE_KEY_RE.match(b)
            if m and m.group(1).lower() == "title":
                title = _strip_markup(m.group(2)) or title
            elements.append(RawElement(ElementType.TITLE_PAGE, _strip_markup(b), b))
    body = "\n".join(lines[i:])

    # Boneyard (/* */) is commented-out text: keep it as non-printing so it's counted.
    def _boneyard(m: re.Match) -> str:
        elements_boneyard.append(m.group(0))
        return ""

    elements_boneyard: list[str] = []
    body = _BONEYARD_RE.sub(_boneyard, body)
    blines = body.split("\n")

    def prev_blank(k: int) -> bool:
        return k == 0 or not blines[k - 1].strip()

    def next_blank(k: int) -> bool:
        return k + 1 >= len(blines) or not blines[k + 1].strip()

    k = 0
    in_dialogue = False
    action_buf: list[str] = []

    def flush_action():
        if action_buf:
            raw = "\n".join(action_buf)
            cleaned = [re.sub(r"^\s*[!~]", "", ln) for ln in action_buf]
            cleaned = [re.sub(r"^\s*>\s*(.*?)\s*<\s*$", r"\1", ln) for ln in cleaned]
            t = _strip_markup(" ".join(cleaned))
            if t:
                elements.append(RawElement(ElementType.ACTION, t, raw, line_count=len(action_buf)))
            elif nonspace_len(raw):
                elements.append(RawElement(ElementType.NON_PRINTING, raw.strip(), raw))
            action_buf.clear()

    while k < len(blines):
        line = blines[k]
        s = line.strip()
        if not s:
            flush_action()
            in_dialogue = False
            k += 1
            continue

        if in_dialogue:
            if s.startswith("(") and s.endswith(")") or (s.startswith("(") and ")" not in s):
                elements.append(RawElement(ElementType.PARENTHETICAL, _strip_markup(s), line))
            else:
                last = elements[-1]
                if last.element_type == ElementType.DIALOGUE:
                    last.raw_text += "\n" + line
                    last.text = _strip_markup(last.text + " " + s)
                    last.line_count += 1
                else:
                    elements.append(RawElement(ElementType.DIALOGUE, _strip_markup(s), line))
            k += 1
            continue

        # Non-printing: sections (#), synopses (=), page breaks (===), standalone notes.
        if s.startswith("#") or (s.startswith("=") and not s.startswith("===")) or (
            s.startswith("[[") and s.endswith("]]")
        ):
            flush_action()
            elements.append(RawElement(ElementType.NON_PRINTING, s, line))
            k += 1
            continue
        if re.fullmatch(r"={3,}", s):
            flush_action()
            elements.append(RawElement(ElementType.NON_PRINTING, "===", line))  # forced page break
            k += 1
            continue

        # Scene heading: forced ".X" (not "..") or INT/EXT/EST/I/E line after a blank line.
        if (s.startswith(".") and not s.startswith("..")) or (prev_blank(k) and has_heading_prefix(s)):
            flush_action()
            heading = s[1:] if s.startswith(".") and not has_heading_prefix(s) else s
            elements.append(
                RawElement(ElementType.SCENE_HEADING, " ".join(heading.split()), line)
            )
            k += 1
            continue

        # Transition: ALL CAPS ending in TO: (or forced ">"), surrounded by blanks.
        if (s.startswith(">") and not s.endswith("<")) or (
            (_TRANSITION_RE.match(s) or is_transition_text(s)) and prev_blank(k) and next_blank(k)
            and s.upper() not in ("THE END",)
        ):
            flush_action()
            elements.append(RawElement(ElementType.TRANSITION, s.lstrip("> ").strip(), line))
            k += 1
            continue

        # Character cue: caps line after a blank, followed by non-blank text.
        if prev_blank(k) and not next_blank(k) and _is_cue(s) and not action_buf:
            flush_action()
            cue = s.lstrip("@").strip()
            dual = cue.endswith("^")
            el = RawElement(ElementType.CHARACTER, cue.rstrip("^ ").strip(), line, is_dual_dialogue=dual)
            if dual:
                # mark the previous dialogue block as dual too
                for prev in reversed(elements):
                    if prev.element_type == ElementType.CHARACTER:
                        prev.is_dual_dialogue = True
                        break
            elements.append(el)
            in_dialogue = True
            k += 1
            continue

        # Centered text ">THE END<", lyrics "~", forced action "!" -> action.
        action_buf.append(line)
        k += 1
    flush_action()

    for b in elements_boneyard:
        elements.append(RawElement(ElementType.NON_PRINTING, b.strip(), b))

    if not any(e.element_type == ElementType.SCENE_HEADING for e in elements):
        issues.append(ImportIssue("no_scene_headings", "No scene headings found in the file.",
                                  Severity.ERROR))

    return ImportResult(
        source_format=SourceFormat.FOUNTAIN,
        elements=elements,
        issues=issues,
        source_char_count=nonspace_len(text),
        needs_pagination=True,
        title=title,
    )


def import_fountain(path: str | Path) -> ImportResult:
    return parse_fountain_text(Path(path).read_text(encoding="utf-8", errors="replace"))
