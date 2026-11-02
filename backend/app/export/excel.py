"""Export a script's breakdown to one Excel workbook.

Every number is taken from the same reports the app shows (computed in code from the
reviewed facts), so the workbook always matches the screens. Anything the app is not
sure about is marked "CHECK" (yellow) rather than hidden or filled in.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..departments import registry
from ..departments.reports import continuity, department_report, scene_sheet
from ..models import ReviewItem, Scene, Schedule, ScriptVersion
from ..models.enums import ReviewItemStatus
from ..reports.facts import load_facts
from ..reports.service import character_report, location_report
from ..scene_detection.pagination import format_eighths
from ..scheduling.reports import _story_days, schedule_report

HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
CHECK_FILL = PatternFill("solid", fgColor="FFF2B3")
NIGHT_FILL = PatternFill("solid", fgColor="DCE3F0")
TITLE_FONT = Font(bold=True, size=14)
WRAP = Alignment(wrap_text=True, vertical="top")
CHECK = "CHECK"
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _eighths(n: int | None) -> str:
    return format_eighths(n) if n is not None else "unknown"


def _qty(qmin: int | None, qmax: int | None, raw: str | None) -> str:
    if qmin is None and qmax is None:
        return f"unknown ({raw})" if raw else ""
    if qmin is not None and qmax is not None and qmin != qmax:
        return f"{qmin}–{qmax}"
    return str(qmax if qmax is not None else qmin)


def _scene_list(cell: dict, f) -> str:
    out = []
    for sid in cell.get("scene_ids", []):
        s = f.scene_by_id.get(sid)
        if s:
            out.append(s.number or f"#{s.seq}")
    return ", ".join(out)


def _sheet_title(wb: Workbook, name: str) -> str:
    base = _BAD_SHEET_CHARS.sub("-", name)[:31] or "Sheet"
    title, n = base, 2
    while title in wb.sheetnames:
        suffix = f" ({n})"
        title, n = base[: 31 - len(suffix)] + suffix, n + 1
    return title


def _table(wb: Workbook, name: str, headers: list[str], rows: Iterable[list[Any]],
           widths: dict[str, int] | None = None, note: str | None = None,
           check_col: str | None = None, night_col: str | None = None) -> Worksheet:
    """A sheet with an optional note line, a frozen styled header row and filters."""
    ws = wb.create_sheet(_sheet_title(wb, name))
    start = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="555555")
        start = 3
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=start, column=c, value=h)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    check_idx = headers.index(check_col) if check_col in headers else None
    night_idx = headers.index(night_col) if night_col in headers else None
    r = start
    for r, row in enumerate(rows, start=start + 1):
        flagged = check_idx is not None and row[check_idx] == CHECK
        night = night_idx is not None and str(row[night_idx] or "").upper() in ("NIGHT", "EVENING", "DUSK")
        for c, v in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = WRAP
            if flagged:
                cell.fill = CHECK_FILL
            elif night:
                cell.fill = NIGHT_FILL
    ws.freeze_panes = ws.cell(row=start + 1, column=2)
    if r > start:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{r}"
    for c, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(c)].width = (widths or {}).get(h, max(10, min(40, len(h) + 4)))
    return ws


def build_workbook(session: Session, version_id: int) -> tuple[bytes, str]:
    v = session.get(ScriptVersion, version_id)
    f = load_facts(session, version_id)
    sdays = _story_days(session, f)
    scenes_db = {s.id: s for s in session.scalars(select(Scene).where(Scene.script_version_id == version_id))}
    chars = character_report(f)
    locs = location_report(f)
    specs = list(registry.SPECS.values())
    dept_reports = {spec.key.value: department_report(f, spec.key.value) for spec in specs}
    open_reviews = session.scalars(
        select(ReviewItem).where(ReviewItem.script_version_id == version_id,
                                 ReviewItem.status == ReviewItemStatus.OPEN)
        .order_by(ReviewItem.scene_id, ReviewItem.id)).all()

    wb = Workbook()
    wb.remove(wb.active)
    live = [s for s in f.scenes if not s.omitted]

    # ------------------------------------------------------------------ 1. Read me
    ws = wb.create_sheet("Read me")
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 80
    title = v.project.title if v.project else "Script"
    lines: list[tuple[str, Any]] = [
        (f"{title} — production breakdown", None),
        ("Script file", v.source_filename),
        ("Draft", " ".join(x for x in (v.label, v.revision_color, v.revision_date) if x) or "—"),
        ("Exported", datetime.now().strftime("%d %b %Y, %H:%M")),
        ("", None),
        ("Scenes", len(live)),
        ("Omitted scenes", sum(1 for s in f.scenes if s.omitted)),
        ("Total length (pages)", _eighths(sum(s.eighths for s in live))),
        ("Characters who appear", chars["unique_characters"]),
        ("Speaking characters", chars["speaking_characters"]),
        ("Locations / sets", f"{locs['story_locations']} locations, {locs['sets']} sets"),
        ("Items still to check", len(open_reviews)),
        ("", None),
        ("How to read this file", None),
        ("Yellow rows / CHECK", "The app was not sure about something here — please check it against the "
                                "script (see the 'To check' sheet). Nothing has been guessed or filled in."),
        ("Blue rows", "Night / evening / dusk scenes."),
        ("unknown", "The script does not say (for example 'a crowd' has no headcount). It is left unknown "
                    "on purpose."),
        ("Pages", "Lengths are in eighths of a page, e.g. 1 3/8."),
        ("Story day vs shooting day", "'Story day' is when the scene happens in the plot (worked out by the "
                                      "app — check it). Shooting days only exist in a schedule; they are on "
                                      "the Schedule and DOOD sheets."),
        ("Character vs casting", "A character is a person in the story. A casting role is a hire (e.g. ARJUN "
                                 "and YOUNG ARJUN are one character but two actors). The DOOD lists hires."),
        ("Filters", "Every sheet has filter arrows in the header row — use them to show, for example, only "
                    "night exteriors."),
        ("", None),
        ("Sheets in this file", None),
    ]
    for r, (a, b) in enumerate(lines, start=1):
        ws.cell(row=r, column=1, value=a)
        if b is not None:
            ws.cell(row=r, column=2, value=b).alignment = WRAP
    ws["A1"].font = TITLE_FONT
    for r, (a, b) in enumerate(lines, start=1):
        if b is None and a:
            ws.cell(row=r, column=1).font = Font(bold=True, size=12 if r > 1 else 14)
    contents_row = len(lines) + 1

    # ------------------------------------------------------------------ 2. Scene breakdown
    apps_by_scene: dict[int, list] = {}
    for a in f.appearances:
        apps_by_scene.setdefault(a.scene_id, []).append(a)
    review_scenes = {ri.scene_id for ri in open_reviews if ri.scene_id}
    dept_cols = [spec.label for spec in specs]
    headers = ["Sc", "Page", "INT/EXT", "Location", "Time", "Pages", "Story day", "Cast (on screen)",
               "Speaking", "Off-screen / V.O.", "Synopsis", *dept_cols, "Check?"]
    rows = []
    for s in f.scenes:
        db_s = scenes_db.get(s.id)
        if s.omitted:
            rows.append([s.number or f"#{s.seq}", db_s.start_page_label if db_s else None, "", "OMITTED",
                         "", "", "", "", "", "", "", *[""] * len(dept_cols), ""])
            continue
        apps = apps_by_scene.get(s.id, [])
        name = lambda a: f.characters[a.character_id].canonical_name if a.character_id in f.characters else "?"
        on = sorted({name(a) for a in apps if a.presence == "present"})
        speaking = sorted({name(a) for a in apps if a.speaking == "speaking" and a.presence == "present"})
        off = sorted({f"{name(a)} ({'O.S.' if a.presence == 'off_screen' else 'V.O.'})" for a in apps
                      if a.presence in ("off_screen", "voice_only")})
        sheet = scene_sheet(f, s.id)
        dept_vals = []
        for spec in specs:
            items = []
            for it in sheet.get(spec.key.value, []):
                q = _qty(it["quantity_min"], it["quantity_max"], it["quantity_raw"])
                label = it["name"] + (f" ×{q}" if q and q != "1" and not q.startswith("unknown") else
                                      f" ({q})" if q.startswith("unknown") else "")
                if it["inferred"]:
                    label += " [inferred]"
                items.append(label)
            dept_vals.append("\n".join(items))
        sd = sdays.get(s.id)
        story = (sd.label + (" (check)" if sd.needs_review else "")) if sd else ""
        flagged = s.needs_review or s.id in review_scenes or any(a.needs_review for a in apps)
        rows.append([s.number or f"#{s.seq}", db_s.start_page_label if db_s else None, s.int_ext,
                     f.loc_label(s.location_id), s.time_written + (" (inherited)" if s.time_inferred else ""),
                     _eighths(s.eighths), story, ", ".join(on), ", ".join(speaking), ", ".join(off),
                     db_s.synopsis if db_s else "", *dept_vals, CHECK if flagged else ""])
    _table(wb, "Scene breakdown", headers, rows,
           widths={"Sc": 6, "Page": 6, "INT/EXT": 9, "Location": 30, "Time": 12, "Pages": 8, "Story day": 12,
                   "Cast (on screen)": 30, "Speaking": 24, "Off-screen / V.O.": 22, "Synopsis": 50,
                   **{c: 26 for c in dept_cols}, "Check?": 9},
           note="One row per scene, in script order — the master breakdown. Department columns list what "
                "each scene needs.", check_col="Check?", night_col="Time")

    # ------------------------------------------------------------------ 3. Characters
    rows = []
    for c in chars["characters"]:
        fa, la = c["first_appearance"], c["last_appearance"]
        rows.append([c["name"], ", ".join(c["casting_roles"]), c["appearances"]["count"],
                     c["speaking"]["count"], c["silent"]["count"], c["voice_only"]["count"],
                     c["mentioned_only"]["count"], c["dialogue_blocks"], _eighths(c["eighths"]),
                     (fa["number"] or f"#{fa['seq']}") if fa else "", (la["number"] or f"#{la['seq']}") if la else "",
                     _scene_list(c["appearances"], f), CHECK if c["unreviewed"]["count"] else ""])
    _table(wb, "Characters", ["Character", "Casting role(s)", "Scenes", "Speaking in", "Silent in", "Voice only in",
                              "Only mentioned in", "Dialogue blocks", "Pages", "First scene", "Last scene",
                              "Scene numbers", "Check?"], rows,
           widths={"Character": 24, "Casting role(s)": 24, "Scene numbers": 50},
           note="Counts are numbers of scenes. An appearance means on screen or heard off-screen (O.S.); "
                "voice-over and mentions are counted separately.", check_col="Check?")

    # ------------------------------------------------------------------ 4. Locations
    rows = []
    for n in locs["locations"]:
        rows.append([n["label"], n["kind"], n["scenes"]["count"], _eighths(n["eighths"]),
                     ", ".join(f"{k} {c['count']}" for k, c in n["by_int_ext"].items()),
                     ", ".join(f"{k} {c['count']}" for k, c in n["by_time"].items()),
                     ", ".join(f"{c['name']} ({c['count']})" for c in n["characters"]),
                     _scene_list(n["scenes"], f)])
    _table(wb, "Locations", ["Location", "Type", "Scenes", "Pages", "INT/EXT", "Time of day",
                             "Characters (scenes)", "Scene numbers"], rows,
           widths={"Location": 36, "Characters (scenes)": 50, "Scene numbers": 40, "Time of day": 26},
           note="A location's count includes the scenes in all of its sets.")

    # ------------------------------------------------------------------ 5. Departments
    for spec in specs:
        rep = dept_reports[spec.key.value]
        if not rep["items"]:
            continue
        rows = []
        for it in rep["items"]:
            rows.append([it["name"], it.get("category") or "", it.get("owner") or "", it["scenes"]["count"],
                         it["max_in_one_scene"] if it["max_in_one_scene"] is not None else
                         ("unknown" if it["unknown_quantity"]["count"] else ""),
                         ", ".join(f"{c['name']} ({c['role']})" for c in it["characters"]),
                         it["first_scene"], _scene_list(it["scenes"], f),
                         "yes" if it["inferred"]["count"] else "",
                         CHECK if it["needs_review"]["count"] or it["unknown_quantity"]["count"] else ""])
        _table(wb, spec.label, [spec.item_noun.capitalize() if spec.item_noun else "Item", "Category", "Owner",
                                "Scenes", "Most in one scene", "Characters", "First scene", "Scene numbers",
                                "Inferred", "Check?"], rows,
               widths={"Characters": 36, "Scene numbers": 40, "Category": 16},
               note=f"{rep['unique_items']} {spec.item_noun or 'items'} across {rep['scenes']['count']} scenes. "
                    "'Inferred' means the script does not state it directly — review it.",
               check_col="Check?")

    # ------------------------------------------------------------------ 6. Continuity
    cont = continuity(f)
    rows = []
    for c in cont["characters"]:
        checks = {(k["scene_id"]): k["message"] for k in c["continuity_checks"]}
        for t in c["timeline"]:
            look = "; ".join(e["item"] + (f" ({e['state']})" if e["state"] else "") for e in t["entries"])
            rows.append([c["name"], t["scene"], t["time"], look or "(nothing recorded)",
                         "CHANGE" if t["changed"] else "", checks.get(t["scene_id"], ""),
                         CHECK if t["scene_id"] in checks else ""])
    if rows:
        _table(wb, "Costume & look continuity", ["Character", "Sc", "Time", "Costume / makeup / hair",
                                                 "Changed?", "Note", "Check?"], rows,
               widths={"Character": 22, "Costume / makeup / hair": 60, "Note": 50},
               note="Per character, in script order: what they wear and how they look, and where it changes.",
               check_col="Check?")

    # ------------------------------------------------------------------ 7. Schedules
    for sched in session.scalars(select(Schedule).where(Schedule.script_version_id == version_id)
                                 .order_by(Schedule.id)):
        rep = schedule_report(session, sched.id)
        rows = []
        for d in rep["oneliner"]:
            rows.append([f"DAY {d['day_number']}", d["date"] or "", d["label"] or "", "", "", "",
                         _eighths(d["eighths"]), ", ".join(d["story_days"]), ""])
            for e in d["scenes"]:
                rows.append(["", "", e["scene"], e["int_ext"], e["location"], e["time"],
                             _eighths(e["eighths"]) + (" (part)" if e["split"] else ""),
                             (e["story_day"] or "") + (" (check)" if e["story_day_inferred"] else ""),
                             ", ".join(str(i) for i in e["cast_ids"])])
        ws = _table(wb, f"Schedule - {sched.name}", ["Shooting day", "Date", "Sc", "INT/EXT", "Location", "Time",
                                                    "Pages", "Story day", "Cast IDs"], rows,
                    widths={"Location": 34, "Story day": 14, "Cast IDs": 16},
                    note="One-liner by SHOOTING day. Story days (plot time) are shown alongside for reference.",
                    night_col="Time")
        for row in ws.iter_rows(min_row=4):
            if row[0].value:
                for cell in row:
                    cell.font = Font(bold=True)
        dood = rep["dood"]
        day_cols = [f"Day {d['day_number']}" for d in dood["days"]]
        rows = [[r["cast_id"], r["requirement"], r["character"] or "", *[r["codes"].get(d["day_number"], "")
                 for d in dood["days"]], r["work_days"], r["hold_days"], r["drop_days"],
                 "; ".join(r["problems"]), CHECK if r["problems"] or r["implicit"] else ""]
                for r in dood["cast"]]
        _table(wb, f"DOOD - {sched.name}", ["ID", "Casting requirement", "Character", *day_cols, "Work", "Hold",
                                           "Drop", "Notes", "Check?"], rows,
               widths={"Casting requirement": 24, "Character": 20, "Notes": 40, **{c: 7 for c in day_cols}},
               note="Day Out of Days over SHOOTING days. SW start, W work, WF finish, SWF one day, H hold, "
                    "WD/PW drop and pick up.", check_col="Check?")

    # ------------------------------------------------------------------ 8. To check
    rows = []
    for ri in open_reviews:
        s = f.scene_by_id.get(ri.scene_id) if ri.scene_id else None
        rows.append([(s.number or f"#{s.seq}") if s else "", ri.category.value.replace("_", " "),
                     ri.severity.value, ri.message])
    _table(wb, "To check", ["Sc", "Type", "Importance", "What to check"], rows,
           widths={"Sc": 6, "Type": 16, "Importance": 11, "What to check": 100},
           note="Things the app was not sure about. Resolve them in the app's Review tab and export again.")

    # contents list on the Read me sheet
    for i, name in enumerate(wb.sheetnames[1:], start=contents_row):
        ws_readme = wb["Read me"]
        cell = ws_readme.cell(row=i, column=1, value=name)
        cell.hyperlink = f"#'{name}'!A1"
        cell.font = Font(color="1F4E99", underline="single")

    buf = io.BytesIO()
    wb.save(buf)
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", (title or "breakdown")).strip() or "breakdown"
    return buf.getvalue(), f"{stem} - breakdown.xlsx"
