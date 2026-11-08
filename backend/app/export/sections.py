"""The pieces of a breakdown that can be exported, one table per piece.

Both the Excel and the PDF export are built from these tables, so the two files always hold the
same numbers as each other and as the screens. Every number comes from the in-app reports
(computed in code from the reviewed facts). Anything the app is not sure about is marked CHECK,
never hidden or filled in.

The user picks which pieces to download; `catalog()` lists them, `build()` makes one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..departments import registry
from ..departments.reports import continuity, department_report, scene_sheet
from ..models import ReviewItem, Scene, Schedule, ScriptVersion
from ..models.enums import ReviewItemStatus
from ..reports.facts import load_facts
from ..reports.service import (
    character_report,
    combination_report,
    crosstab,
    location_report,
    scene_report,
)
from ..scene_detection.pagination import format_eighths
from ..scheduling.reports import _story_days, schedule_report

CHECK = "CHECK"


@dataclass
class Table:
    key: str
    title: str
    headers: list[str]
    rows: list[list[Any]]
    note: str | None = None
    widths: dict[str, int] = field(default_factory=dict)   # Excel column widths, in characters
    check_col: str | None = None      # rows whose value here is CHECK are highlighted yellow
    night_col: str | None = None      # rows whose value here is NIGHT / EVENING / DUSK are tinted blue
    bold_col: str | None = None       # rows with a value in this column are bold (schedule day rows)
    layout: str = "table"             # table | pairs (label/value page) | cards (one block per row in PDF)
    card_title: list[str] = field(default_factory=list)  # cards: columns joined into each block's title
    repeat_cols: int = 1              # PDF: columns repeated when a very wide table is split across pages


# ----------------------------------------------------------------------------- formatting helpers
def eighths(n: int | None) -> str:
    return format_eighths(n) if n is not None else "unknown"


def _qty(qmin: int | None, qmax: int | None, raw: str | None) -> str:
    if qmin is None and qmax is None:
        return f"unknown ({raw})" if raw else ""
    if qmin is not None and qmax is not None and qmin != qmax:
        return f"{qmin}–{qmax}"
    return str(qmax if qmax is not None else qmin)


def _counts(d: dict) -> str:
    return ", ".join(f"{k} {c['count']}" for k, c in d.items())


class Context:
    """Loads the facts once and shares them between every section being exported."""

    def __init__(self, session: Session, version_id: int):
        self.session = session
        self.version_id = version_id
        self.version = session.get(ScriptVersion, version_id)
        self.f = load_facts(session, version_id)
        self.title = (self.version.project.title if self.version and self.version.project else None) or "Script"

    def scene_no(self, sid: int) -> str:
        s = self.f.scene_by_id.get(sid)
        return (s.number or f"#{s.seq}") if s else ""

    def scene_list(self, c: dict) -> str:
        return ", ".join(x for x in (self.scene_no(i) for i in c.get("scene_ids", [])) if x)

    @cached_property
    def live(self):
        return [s for s in self.f.scenes if not s.omitted]

    @cached_property
    def chars(self):
        return character_report(self.f)

    @cached_property
    def locs(self):
        return location_report(self.f)

    @cached_property
    def specs(self):
        return list(registry.SPECS.values())

    @cached_property
    def dept_reports(self):
        return {spec.key.value: department_report(self.f, spec.key.value) for spec in self.specs}

    @cached_property
    def continuity(self):
        return continuity(self.f)

    @cached_property
    def schedules(self):
        return list(self.session.scalars(select(Schedule).where(Schedule.script_version_id == self.version_id)
                                         .order_by(Schedule.id)))

    @cached_property
    def open_reviews(self):
        return self.session.scalars(
            select(ReviewItem).where(ReviewItem.script_version_id == self.version_id,
                                     ReviewItem.status == ReviewItemStatus.OPEN)
            .order_by(ReviewItem.scene_id, ReviewItem.id)).all()


# ----------------------------------------------------------------------------- the catalogue
def catalog(ctx: Context) -> list[dict[str, Any]]:
    """Every piece that can be exported, in file order. `available` is False when there is
    nothing in it yet (it is then left out of a 'download everything' file)."""
    has_scenes = bool(ctx.live)
    has_cast = bool(ctx.chars["characters"])
    out = [
        ("summary", "Summary page", "Main", True, "Title, totals and how to read the file."),
        ("scenes", "Scene breakdown", "Main", bool(ctx.f.scenes),
         "Every scene with its cast, location and what each department needs. "
         "In the PDF this is one breakdown sheet per scene."),
        ("scene_summary", "Scene counts (INT/EXT × time of day)", "Reports", has_scenes, None),
        ("characters", "Characters", "Reports", has_cast, None),
        ("locations", "Locations", "Reports", bool(ctx.locs["locations"]), None),
        ("combinations", "Who appears together", "Reports", has_cast, "Pairs of characters and their shared scenes."),
        ("character_time", "Character × time of day", "Reports", has_cast, None),
        ("location_time", "Location × time of day", "Reports", has_scenes, None),
        ("character_location_time", "Character × location × time of day", "Reports", has_cast, None),
    ]
    for spec in ctx.specs:
        n = ctx.dept_reports[spec.key.value]["unique_items"]
        out.append((f"dept:{spec.key.value}", spec.label, "Departments", bool(n),
                    f"{n} found" if n else "nothing found yet"))
    out.append(("continuity", "Costume & look continuity", "Departments",
                any(c["timeline"] for c in ctx.continuity["characters"]), None))
    for sch in ctx.schedules:
        out.append((f"schedule:{sch.id}", f"One-liner — {sch.name}", "Schedule", True, None))
        out.append((f"dood:{sch.id}", f"Day Out of Days (DOOD) — {sch.name}", "Schedule", True, None))
    out.append(("to_check", f"Things to check ({len(ctx.open_reviews)})", "Other", True,
                "What the app was not sure about."))
    return [{"key": k, "label": label, "group": g, "available": a, "note": n} for k, label, g, a, n in out]


def resolve(ctx: Context, keys: list[str] | None) -> list[str]:
    """The keys to export, in file order. None means everything that has content."""
    cat = catalog(ctx)
    if keys is None:
        return [c["key"] for c in cat if c["available"]]
    known = {c["key"] for c in cat}
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise KeyError(", ".join(unknown))
    wanted = set(keys)
    return [c["key"] for c in cat if c["key"] in wanted]


def build(ctx: Context, key: str) -> Table:
    if key.startswith("dept:"):
        return _department(ctx, key.split(":", 1)[1])
    if key.startswith("schedule:") or key.startswith("dood:"):
        kind, sid = key.split(":", 1)
        return _schedule(ctx, int(sid)) if kind == "schedule" else _dood(ctx, int(sid))
    if key in ("character_time", "location_time", "character_location_time"):
        return _crosstab(ctx, key)
    return _BUILDERS[key](ctx)


# ----------------------------------------------------------------------------- sections
def _summary(ctx: Context) -> Table:
    v, f, chars, locs = ctx.version, ctx.f, ctx.chars, ctx.locs
    rows = [
        ["Script file", v.source_filename],
        ["Draft", " ".join(x for x in (v.label, v.revision_color, v.revision_date) if x) or "—"],
        ["Exported", datetime.now().strftime("%d %b %Y, %H:%M")],
        ["Scenes", len(ctx.live)],
        ["Omitted scenes", sum(1 for s in f.scenes if s.omitted)],
        ["Total length (pages)", eighths(sum(s.eighths for s in ctx.live))],
        ["Characters who appear", chars["unique_characters"]],
        ["Speaking characters", chars["speaking_characters"]],
        ["Locations / sets", f"{locs['story_locations']} locations, {locs['sets']} sets"],
        ["Items still to check", len(ctx.open_reviews)],
        ["", ""],
        ["CHECK / yellow rows", "The app was not sure about something here — please check it against the "
                                "script (see 'Things to check'). Nothing has been guessed or filled in."],
        ["Blue rows", "Night / evening / dusk scenes."],
        ["unknown", "The script does not say (for example 'a crowd' has no headcount). It is left unknown "
                    "on purpose."],
        ["Pages", "Lengths are in eighths of a page, e.g. 1 3/8."],
        ["Story day vs shooting day", "'Story day' is when the scene happens in the plot (worked out by the "
                                      "app — check it). Shooting days only exist in a schedule; they are on "
                                      "the One-liner and DOOD."],
        ["Character vs casting", "A character is a person in the story. A casting role is a hire (e.g. ARJUN "
                                 "and YOUNG ARJUN are one character but two actors). The DOOD lists hires."],
    ]
    return Table("summary", "Read me", ["", ""], rows, layout="pairs",
                 note=f"{ctx.title} — production breakdown")


def _scenes(ctx: Context) -> Table:
    f = ctx.f
    sdays = _story_days(ctx.session, f)
    scenes_db = {s.id: s for s in ctx.session.scalars(select(Scene).where(Scene.script_version_id == ctx.version_id))}
    apps_by_scene: dict[int, list] = {}
    for a in f.appearances:
        apps_by_scene.setdefault(a.scene_id, []).append(a)
    review_scenes = {ri.scene_id for ri in ctx.open_reviews if ri.scene_id}
    dept_cols = [spec.label for spec in ctx.specs]
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

        def name(a):
            return f.characters[a.character_id].canonical_name if a.character_id in f.characters else "?"
        on = sorted({name(a) for a in apps if a.presence == "present"})
        speaking = sorted({name(a) for a in apps if a.speaking == "speaking" and a.presence == "present"})
        off = sorted({f"{name(a)} ({'O.S.' if a.presence == 'off_screen' else 'V.O.'})" for a in apps
                      if a.presence in ("off_screen", "voice_only")})
        sheet = scene_sheet(f, s.id)
        dept_vals = []
        for spec in ctx.specs:
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
                     eighths(s.eighths), story, ", ".join(on), ", ".join(speaking), ", ".join(off),
                     db_s.synopsis if db_s else "", *dept_vals, CHECK if flagged else ""])
    return Table("scenes", "Scene breakdown", headers, rows,
                 widths={"Sc": 6, "Page": 6, "INT/EXT": 9, "Location": 30, "Time": 12, "Pages": 8,
                         "Story day": 12, "Cast (on screen)": 30, "Speaking": 24, "Off-screen / V.O.": 22,
                         "Synopsis": 50, **{c: 26 for c in dept_cols}, "Check?": 9},
                 note="One row per scene, in script order — the master breakdown. Department columns list "
                      "what each scene needs.",
                 check_col="Check?", night_col="Time", layout="cards",
                 card_title=["Sc", "INT/EXT", "Location", "Time"])


def _scene_summary(ctx: Context) -> Table:
    r = scene_report(ctx.f)
    t = r["int_ext_by_time"]
    rows = [[ie, *[t["cells"][ie][c]["count"] for c in t["columns"]], r["by_int_ext"][ie]["count"],
             eighths(r["by_int_ext"][ie]["eighths"])] for ie in t["rows"]]
    rows.append(["Total", *[r["by_time"][c]["count"] for c in t["columns"]], r["total"]["count"],
                 eighths(r["total_eighths"])])
    rows.append(["Pages", *[eighths(r["by_time"][c]["eighths"]) for c in t["columns"]], "", ""])
    return Table("scene_summary", "Scene counts", ["", *t["columns"], "Scenes", "Pages"], rows,
                 widths={"": 14},
                 note=f"Number of scenes by INT/EXT and time of day as written. {r['omitted']['count']} omitted, "
                      f"{r['inferred_time']['count']} with time inherited from CONTINUOUS, "
                      f"{r['needs_review']['count']} flagged headings.", bold_col=None)


def _characters(ctx: Context) -> Table:
    rows = []
    for c in ctx.chars["characters"]:
        fa, la = c["first_appearance"], c["last_appearance"]
        rows.append([c["name"], ", ".join(c["casting_roles"]), c["appearances"]["count"],
                     c["speaking"]["count"], c["silent"]["count"], c["voice_only"]["count"],
                     c["mentioned_only"]["count"], c["dialogue_blocks"], eighths(c["eighths"]),
                     (fa["number"] or f"#{fa['seq']}") if fa else "", (la["number"] or f"#{la['seq']}") if la else "",
                     ctx.scene_list(c["appearances"]), CHECK if c["unreviewed"]["count"] else ""])
    return Table("characters", "Characters",
                 ["Character", "Casting role(s)", "Scenes", "Speaking in", "Silent in", "Voice only in",
                  "Only mentioned in", "Dialogue blocks", "Pages", "First scene", "Last scene",
                  "Scene numbers", "Check?"], rows,
                 widths={"Character": 24, "Casting role(s)": 24, "Scene numbers": 50},
                 note="Counts are numbers of scenes. An appearance means on screen or heard off-screen (O.S.); "
                      "voice-over and mentions are counted separately.", check_col="Check?")


def _locations(ctx: Context) -> Table:
    rows = [[n["label"], n["kind"].replace("story_location", "location"), n["scenes"]["count"], eighths(n["eighths"]), _counts(n["by_int_ext"]),
             _counts(n["by_time"]), ", ".join(f"{c['name']} ({c['count']})" for c in n["characters"]),
             ctx.scene_list(n["scenes"])] for n in ctx.locs["locations"]]
    return Table("locations", "Locations", ["Location", "Type", "Scenes", "Pages", "INT/EXT", "Time of day",
                                            "Characters (scenes)", "Scene numbers"], rows,
                 widths={"Location": 36, "Type": 12, "Characters (scenes)": 50, "Scene numbers": 40, "Time of day": 26},
                 note="A location's count includes the scenes in all of its sets.")


def _combinations(ctx: Context) -> Table:
    r = combination_report(ctx.f)
    rows = [[" + ".join(c["names"]), c["together"]["count"], _counts(c["by_time"]),
             ", ".join(f"{k} ({v['count']})" for k, v in c["by_location"].items()),
             ctx.scene_list(c["together"])] for c in r["combinations"]]
    return Table("combinations", "Who appears together",
                 ["Characters", "Scenes together", "By time of day", "Where (scenes)", "Scene numbers"], rows,
                 widths={"Characters": 34, "By time of day": 26, "Where (scenes)": 50, "Scene numbers": 40},
                 note="Every pair of characters who appear in the same scene, most scenes first.")


_CROSSTAB_TITLES = {
    "character_time": ("Character × time of day", "Character"),
    "location_time": ("Location × time of day", "Location"),
    "character_location_time": ("Character × location × time of day", "Character @ location"),
}


def _crosstab(ctx: Context, key: str) -> Table:
    r = crosstab(ctx.f, key)
    title, first = _CROSSTAB_TITLES[key]
    rows = [[row, *[r["cells"][row][c]["count"] or "" for c in r["columns"]], r["row_totals"][row]["count"]]
            for row in r["rows"]]
    return Table(key, title, [first, *r["columns"], "Total"], rows, widths={first: 40},
                 note="Number of scenes in each time of day, as written.")


def _department(ctx: Context, dept: str) -> Table:
    spec = next(s for s in ctx.specs if s.key.value == dept)
    rep = ctx.dept_reports[dept]
    rows = []
    for it in rep["items"]:
        rows.append([it["name"], it.get("category") or "", it.get("owner") or "", it["scenes"]["count"],
                     it["max_in_one_scene"] if it["max_in_one_scene"] is not None else
                     ("unknown" if it["unknown_quantity"]["count"] else ""),
                     ", ".join(f"{c['name']} ({c['role']})" for c in it["characters"]),
                     it["first_scene"], ctx.scene_list(it["scenes"]),
                     "yes" if it["inferred"]["count"] else "",
                     CHECK if it["needs_review"]["count"] or it["unknown_quantity"]["count"] else ""])
    return Table(f"dept:{dept}", spec.label,
                 [spec.item_noun.capitalize() if spec.item_noun else "Item", "Category", "Owner", "Scenes",
                  "Most in one scene", "Characters", "First scene", "Scene numbers", "Inferred", "Check?"], rows,
                 widths={"Characters": 36, "Scene numbers": 40, "Category": 16},
                 note=f"{rep['unique_items']} {spec.item_noun or 'items'} across {rep['scenes']['count']} scenes. "
                      "'Inferred' means the script does not state it directly — review it.",
                 check_col="Check?")


def _continuity(ctx: Context) -> Table:
    rows = []
    for c in ctx.continuity["characters"]:
        checks = {k["scene_id"]: k["message"] for k in c["continuity_checks"]}
        for t in c["timeline"]:
            look = "; ".join(e["item"] + (f" ({e['state']})" if e["state"] else "") for e in t["entries"])
            rows.append([c["name"], t["scene"], t["time"], look or "(nothing recorded)",
                         "CHANGE" if t["changed"] else "", checks.get(t["scene_id"], ""),
                         CHECK if t["scene_id"] in checks else ""])
    return Table("continuity", "Costume & look continuity",
                 ["Character", "Sc", "Time", "Costume / makeup / hair", "Changed?", "Note", "Check?"], rows,
                 widths={"Character": 22, "Costume / makeup / hair": 60, "Note": 50},
                 note="Per character, in script order: what they wear and how they look, and where it changes.",
                 check_col="Check?")


def _sched(ctx: Context, sid: int):
    sch = next((s for s in ctx.schedules if s.id == sid), None)
    if sch is None:
        raise KeyError(f"schedule:{sid}")
    return sch, schedule_report(ctx.session, sid)


def _schedule(ctx: Context, sid: int) -> Table:
    sch, rep = _sched(ctx, sid)
    rows = []
    for d in rep["oneliner"]:
        rows.append([f"DAY {d['day_number']}", d["date"] or "", d["label"] or "", "", "", "",
                     eighths(d["eighths"]), ", ".join(d["story_days"]), ""])
        for e in d["scenes"]:
            rows.append(["", "", e["scene"], e["int_ext"], e["location"], e["time"],
                         eighths(e["eighths"]) + (" (part)" if e["split"] else ""),
                         (e["story_day"] or "") + (" (check)" if e["story_day_inferred"] else ""),
                         ", ".join(str(i) for i in e["cast_ids"])])
    return Table(f"schedule:{sid}", f"Schedule - {sch.name}",
                 ["Shooting day", "Date", "Sc", "INT/EXT", "Location", "Time", "Pages", "Story day", "Cast IDs"],
                 rows, widths={"Location": 34, "Story day": 14, "Cast IDs": 16},
                 note="One-liner by SHOOTING day. Story days (plot time) are shown alongside for reference.",
                 night_col="Time", bold_col="Shooting day")


def _dood(ctx: Context, sid: int) -> Table:
    sch, rep = _sched(ctx, sid)
    dood = rep["dood"]
    day_cols = [f"Day {d['day_number']}" for d in dood["days"]]
    rows = [[r["cast_id"], r["requirement"], r["character"] or "",
             *[r["codes"].get(d["day_number"], "") for d in dood["days"]], r["work_days"], r["hold_days"],
             r["drop_days"], "; ".join(r["problems"]), CHECK if r["problems"] or r["implicit"] else ""]
            for r in dood["cast"]]
    return Table(f"dood:{sid}", f"DOOD - {sch.name}",
                 ["ID", "Casting requirement", "Character", *day_cols, "Work", "Hold", "Drop", "Notes", "Check?"],
                 rows, widths={"ID": 5, "Casting requirement": 24, "Character": 20, "Notes": 40,
                               **{c: 7 for c in day_cols}},
                 note="Day Out of Days over SHOOTING days. SW start, W work, WF finish, SWF one day, H hold, "
                      "WD/PW drop and pick up.", check_col="Check?", repeat_cols=3)


def _to_check(ctx: Context) -> Table:
    rows = [[ctx.scene_no(ri.scene_id) if ri.scene_id else "", ri.category.value.replace("_", " "),
             ri.severity.value, ri.message] for ri in ctx.open_reviews]
    return Table("to_check", "To check", ["Sc", "Type", "Importance", "What to check"], rows,
                 widths={"Sc": 6, "Type": 16, "Importance": 11, "What to check": 100},
                 note="Things the app was not sure about. Resolve them in the app's 'To check' tab and export again.")


_BUILDERS = {
    "summary": _summary, "scenes": _scenes, "scene_summary": _scene_summary, "characters": _characters,
    "locations": _locations, "combinations": _combinations, "continuity": _continuity, "to_check": _to_check,
}


def file_stem(ctx: Context) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9 _-]+", "", ctx.title).strip() or "breakdown"
