"""Export a script's breakdown — all of it, or only the parts the user picks — to one Excel workbook.

The tables come from `sections.py`, shared with the PDF export, so every number matches the
screens. Anything the app is not sure about is marked "CHECK" (yellow) rather than hidden.
"""
from __future__ import annotations

import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from . import sections as sec
from .sections import CHECK, Table

HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
CHECK_FILL = PatternFill("solid", fgColor="FFF2B3")
NIGHT_FILL = PatternFill("solid", fgColor="DCE3F0")
TITLE_FONT = Font(bold=True, size=14)
WRAP = Alignment(wrap_text=True, vertical="top")
NIGHT = ("NIGHT", "EVENING", "DUSK")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def is_night(v) -> bool:
    return str(v or "").upper().split(" (")[0] in NIGHT


def _sheet_title(wb: Workbook, name: str) -> str:
    base = _BAD_SHEET_CHARS.sub("-", name)[:31] or "Sheet"
    title, n = base, 2
    while title in wb.sheetnames:
        suffix = f" ({n})"
        title, n = base[: 31 - len(suffix)] + suffix, n + 1
    return title


def _table(wb: Workbook, t: Table) -> Worksheet:
    """A sheet with an optional note line, a frozen styled header row and filters."""
    ws = wb.create_sheet(_sheet_title(wb, t.title))
    start = 1
    if t.note:
        ws.cell(row=1, column=1, value=t.note).font = Font(italic=True, color="555555")
        start = 3
    for c, h in enumerate(t.headers, start=1):
        cell = ws.cell(row=start, column=c, value=h)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    idx = {h: i for i, h in enumerate(t.headers)}
    check_idx, night_idx, bold_idx = idx.get(t.check_col), idx.get(t.night_col), idx.get(t.bold_col)
    r = start
    for r, row in enumerate(t.rows, start=start + 1):
        flagged = check_idx is not None and row[check_idx] == CHECK
        night = night_idx is not None and is_night(row[night_idx])
        bold = bold_idx is not None and bool(row[bold_idx])
        for c, v in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = WRAP
            if flagged:
                cell.fill = CHECK_FILL
            elif night:
                cell.fill = NIGHT_FILL
            if bold:
                cell.font = Font(bold=True)
    ws.freeze_panes = ws.cell(row=start + 1, column=2)
    if r > start:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(t.headers))}{r}"
    for c, h in enumerate(t.headers, start=1):
        ws.column_dimensions[get_column_letter(c)].width = t.widths.get(h, max(10, min(40, len(h) + 4)))
    return ws


def _readme(wb: Workbook, t: Table) -> Worksheet:
    ws = wb.create_sheet("Read me")
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 80
    ws.cell(row=1, column=1, value=t.note).font = TITLE_FONT
    for r, (a, b) in enumerate(t.rows, start=3):
        ws.cell(row=r, column=1, value=a).font = Font(bold=True)
        ws.cell(row=r, column=2, value=b).alignment = WRAP
    return ws


def build_workbook(session: Session, version_id: int, keys: list[str] | None = None) -> tuple[bytes, str]:
    """keys: which sections to include (see sections.catalog); None = everything that has content."""
    ctx = sec.Context(session, version_id)
    wb = Workbook()
    wb.remove(wb.active)
    readme = None
    for key in sec.resolve(ctx, keys):
        t = sec.build(ctx, key)
        if key == "summary":
            readme = _readme(wb, t)
        else:
            _table(wb, t)
    if not wb.sheetnames:
        wb.create_sheet("Empty").cell(row=1, column=1, value="Nothing was selected to export.")
    if readme is not None and len(wb.sheetnames) > 1:   # contents list with links
        row = readme.max_row + 2
        readme.cell(row=row, column=1, value="Sheets in this file").font = Font(bold=True, size=12)
        for i, name in enumerate(wb.sheetnames[1:], start=row + 1):
            cell = readme.cell(row=i, column=1, value=name)
            cell.hyperlink = f"#'{name}'!A1"
            cell.font = Font(color="1F4E99", underline="single")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"{sec.file_stem(ctx)} - breakdown.xlsx"
