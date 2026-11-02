"""Command line: python -m app.cli import <file> | coverage <id> | scenes <id>"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from .db.session import init_db, make_engine, make_session_factory
from .importers.coverage import coverage_report, format_coverage
from .importers.service import import_script
from .models import Project, Scene, ScriptVersion
from .scene_detection.pagination import format_eighths


def _session(db: str | None):
    engine = make_engine(db)
    init_db(engine)
    return make_session_factory(engine)()


def cmd_import(a) -> int:
    s = _session(a.db)
    project = s.scalars(select(Project).where(Project.title == a.project)).first()
    if project is None:
        project = Project(title=a.project)
        s.add(project)
        s.flush()
    v = import_script(s, project.id, a.file, label=a.label, ocr_lang=a.ocr_lang)
    s.commit()
    print(f"Imported as script version {v.id} (project {project.id} '{project.title}')\n")
    print(format_coverage(coverage_report(s, v.id)))
    return 0


def cmd_coverage(a) -> int:
    s = _session(a.db)
    r = coverage_report(s, a.version)
    print(json.dumps(r, indent=2) if a.json else format_coverage(r))
    return 0


def cmd_scenes(a) -> int:
    s = _session(a.db)
    v = s.get(ScriptVersion, a.version)
    if v is None:
        print(f"No script version {a.version}", file=sys.stderr)
        return 1
    print(f"{'SEQ':>4} {'SC#':>5}  {'I/E':7} {'LOCATION':38} {'TIME (as written)':18} {'INHERITED':9} "
          f"{'PAGES':9} {'LEN':>6}  FLAGS")
    for sc in s.scalars(select(Scene).where(Scene.script_version_id == v.id).order_by(Scene.seq)):
        loc = "OMITTED" if sc.is_omitted else (sc.location_raw or "—")
        eff = sc.effective_time_of_day.value if sc.effective_time_of_day else ""
        pages = f"{sc.start_page_label or '?'}-{sc.end_page_label or '?'}"
        print(f"{sc.seq:>4} {sc.scene_number or '—':>5}  {sc.int_ext.value:7} {loc[:38]:38} "
              f"{sc.time_of_day.value:18} {eff:9} {pages:9} {format_eighths(sc.length_eighths):>6}  "
              f"{sc.review_reason or ''}")
    return 0


def cmd_extract(a) -> int:
    from .extraction.service import run_extraction
    s = _session(a.db)
    if a.extractor == "claude":
        from .extraction.claude_extractor import ClaudeExtractor
        ex = ClaudeExtractor(model=a.model, effort=a.effort)
    else:
        from .extraction.heuristic_extractor import HeuristicExtractor
        ex = HeuristicExtractor()

    def progress(done, total):
        print(f"\r  extracted {done}/{total} scenes", end="", flush=True)

    run = run_extraction(s, a.version, ex, workers=a.workers, progress=progress)
    s.commit()
    print(f"\nRun {run.id}: {run.status.value} ({run.model}, {run.prompt_version}) {run.stats}")
    open_items = _open_counts(s, a.version, "extraction")
    print(f"Review queue (extraction): {open_items}")
    return 0 if run.status.value != "failed" else 1


def _open_counts(s, version_id, category):
    from collections import Counter
    from .models import ReviewItem
    items = s.scalars(select(ReviewItem).where(ReviewItem.script_version_id == version_id,
                                               ReviewItem.category == category,
                                               ReviewItem.status == "open")).all()
    return dict(Counter(i.reason_code for i in items))


def cmd_reconcile(a) -> int:
    from .reconciliation.service import propose
    s = _session(a.db)
    made = propose(s, a.version)
    s.commit()
    print(f"Proposals added to the review queue: {made or 'none'}")
    from .models import ReviewItem
    for i in s.scalars(select(ReviewItem).where(ReviewItem.script_version_id == a.version,
                                                ReviewItem.category == "reconciliation",
                                                ReviewItem.status == "open")):
        print(f"  [{i.id}] {i.message}")
    if a.accept_all:
        from .review.service import bulk_act
        ids = [i.id for i in s.scalars(select(ReviewItem).where(
            ReviewItem.script_version_id == a.version, ReviewItem.category == "reconciliation",
            ReviewItem.status == "open"))]
        print(bulk_act(s, ids, "accept", "cli"))
        s.commit()
    return 0


def cmd_accuracy(a) -> int:
    from .extraction.evaluate import evaluate, format_accuracy
    s = _session(a.db)
    print(format_accuracy(evaluate(s, a.version, a.truth)))
    return 0


def cmd_report(a) -> int:
    from .reports.service import run_report
    s = _session(a.db)
    kw = {"time_mode": a.time}
    r = run_report(s, a.version, a.name, **kw)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print(_format_report(a.name, r))
    return 0


def _num(ref):
    if not ref:
        return "-"
    return ref.get("number") or f"#{ref.get('seq')}"


def _c(cellv):
    return cellv["count"] if isinstance(cellv, dict) else cellv


def _format_report(name, r) -> str:
    out = []
    if name == "scenes":
        out.append(f"SCENES: {r['total']['count']} ({r['omitted']['count']} omitted), "
                   f"{format_eighths(r['total_eighths'])} pages, time = {r['time_mode']}")
        out.append("  INT/EXT: " + ", ".join(f"{k} {_c(v)}" for k, v in r["by_int_ext"].items()))
        out.append("  TIME:    " + ", ".join(f"{k} {_c(v)}" for k, v in r["by_time"].items()))
    elif name == "characters":
        out.append(f"CHARACTERS: {r['unique_characters']} on set/voice, {r['speaking_characters']} speaking, "
                   f"{r['silent_only_characters']} silent only, {r['mentioned_only_characters']} mentioned only")
        out.append(f"  {'NAME':24} {'SCN':>4} {'O.S.':>4} {'V.O.':>4} {'MENT':>4} {'SPK':>4} {'DLG':>4} "
                   f"{'PAGES':>7}  FIRST-LAST  ROLES")
        for c in r["characters"]:
            fa, la = c["first_appearance"], c["last_appearance"]
            span = f"{_num(fa)}-{_num(la)}"
            out.append(f"  {c['name'][:24]:24} {_c(c['appearances']):>4} {_c(c['off_screen']):>4} "
                       f"{_c(c['voice_only']):>4} {_c(c['mentioned_only']):>4} {_c(c['speaking']):>4} "
                       f"{c['dialogue_blocks']:>4} {format_eighths(c['eighths']):>7}  {span:10}  "
                       f"{', '.join(c['casting_roles'])}")
    elif name == "locations":
        out.append(f"LOCATIONS: {r['story_locations']} story locations, {r['sets']} sets")
        for n in r["locations"]:
            ind = "    " if n["parent_id"] else "  "
            ie = ", ".join(f"{k} {_c(v)}" for k, v in n["by_int_ext"].items())
            tm = ", ".join(f"{k} {_c(v)}" for k, v in n["by_time"].items())
            ch = ", ".join(f"{x['name']} {x['count']}" for x in n["characters"][:8])
            out.append(f"{ind}{n['name']}: {_c(n['scenes'])} scenes [{ie}] [{tm}]  cast: {ch}")
    elif name == "combinations":
        for row in r["combinations"][:40]:
            tm = ", ".join(f"{k} {_c(v)}" for k, v in row["by_time"].items())
            out.append(f"  {' + '.join(row['names'])}: {_c(row['together'])} scenes [{tm}]")
    else:
        cols = r["columns"]
        out.append(f"  {'':40} " + " ".join(f"{c[:8]:>8}" for c in cols) + "   TOTAL")
        for row in r["rows"]:
            out.append(f"  {row[:40]:40} " + " ".join(f"{_c(r['cells'][row][c]) or '':>8}" for c in cols)
                       + f"   {_c(r['row_totals'][row]):>5}")
    return "\n".join(out)


def cmd_departments(a) -> int:
    from .departments import registry
    from .departments.service import run_department_extraction
    s = _session(a.db)
    specs = registry.get(a.departments.split(",") if a.departments else None)
    if a.extractor == "claude":
        from .departments.claude_extractor import DepartmentClaudeExtractor
        ex = DepartmentClaudeExtractor(specs, model=a.model, effort=a.effort)
    else:
        from .departments.heuristic import DepartmentHeuristicExtractor
        ex = DepartmentHeuristicExtractor(specs)

    def progress(done, total):
        print(f"\r  {done}/{total} scenes", end="", flush=True)

    run = run_department_extraction(s, a.version, ex, workers=a.workers, progress=progress)
    s.commit()
    print(f"\nRun {run.id}: {run.status.value} — departments: {', '.join(x.key.value for x in specs)}")
    return 0 if run.status.value != "failed" else 1


def cmd_dept_report(a) -> int:
    from .departments.reports import run_department_report
    s = _session(a.db)
    r = run_department_report(s, a.version, a.name, time_mode=a.time)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    if a.name == "continuity":
        for c in r["characters"]:
            print(f"{c['name']} — {c['changes']} change(s)")
            for t in c["timeline"]:
                mark = "*" if t["changed"] else " "
                ents = "; ".join(f"{e['item']}{' [' + e['state'] + ']' if e['state'] else ''}" for e in t["entries"]) \
                    or "(nothing recorded)"
                print(f"   {mark} sc {t['scene']:>4} {t['time']:<10} {ents}")
            for chk in c.get("continuity_checks", []):
                print(f"     ? {chk['message']}")
        return 0
    peak = r["peak_units_in_one_scene"]
    print(f"{r['label'].upper()}: {r['unique_items']} unique {r['item_noun']}(s) in {_c(r['scenes'])} scene(s); "
          f"peak {peak['count'] if peak else 0} unit(s) at once (sc {peak['scene'] if peak else '-'}); "
          f"{_c(r['scenes_with_unknown_quantity'])} scene(s) with unknown quantity; "
          f"{_c(r['inferred'])} inferred; {_c(r['needs_review'])} to review")
    for it in r["items"]:
        chars = ", ".join(f"{c['name']}({c['role']}) {c['count']}" for c in it["characters"][:6])
        extra = {k: v for k, v in it.items() if k in ("drivers", "actions", "state_changes", "species", "practical",
                                                      "vfx_candidate", "playback", "headcount")}
        print(f"  {it['name'][:40]:40} {it['category'] or '':16} scenes {_c(it['scenes']):>3}  "
              f"max/scene {it['max_in_one_scene'] if it['max_in_one_scene'] is not None else '?':>4}  "
              f"from sc {it['first_scene']:>4}  {chars}  {extra if extra else ''}")
    return 0


def cmd_dept_accuracy(a) -> int:
    from .departments.evaluate import evaluate_departments, format_dept_accuracy
    s = _session(a.db)
    print(format_dept_accuracy(evaluate_departments(s, a.version, a.truth,
                                                    a.departments.split(",") if a.departments else None)))
    return 0


def cmd_revise(a) -> int:
    from .revisions.diff import revision_report
    from .revisions.service import revise
    s = _session(a.db)
    ex = dex = None
    if a.extractor != "none":
        if a.extractor == "claude":
            from .extraction.claude_extractor import ClaudeExtractor
            ex = ClaudeExtractor()
        else:
            from .extraction.heuristic_extractor import HeuristicExtractor
            ex = HeuristicExtractor()
        if a.departments:
            from .departments import registry
            specs = registry.get(None if a.departments == "all" else a.departments.split(","))
            if a.extractor == "claude":
                from .departments.claude_extractor import DepartmentClaudeExtractor
                dex = DepartmentClaudeExtractor(specs)
            else:
                from .departments.heuristic import DepartmentHeuristicExtractor
                dex = DepartmentHeuristicExtractor(specs)
    out = revise(s, a.old, a.new, ex, dex, workers=a.workers)
    s.commit()
    print(f"Revision {out['revision_id']}: carried forward {out['carried_forward']}; "
          f"extracted {out['extracted_scenes']} changed/added scene(s)\n")
    print(_format_revision(revision_report(s, out["revision_id"], include_text=False)))
    return 0


def _format_revision(r) -> str:
    lines = [f"REVISION v{r['from_version_id']} -> v{r['to_version_id']}: {r['summary']}, "
             f"pages {r['pages_delta_eighths']:+d}/8, {r['needs_review']} match(es) to review, "
             f"{r['not_extracted']} changed scene(s) not extracted yet"]
    if not r["cast_compared"]:
        lines.append("  cast NOT compared: one of the drafts has no cast extraction")
    if r["departments_not_compared"]:
        lines.append(f"  departments NOT compared (only one draft has them): {', '.join(r['departments_not_compared'])}")
    for k, label in (("characters_new_to_script", "characters new to script"),
                     ("characters_dropped_from_script", "characters dropped from script"),
                     ("casting_roles_new", "casting roles new"), ("casting_roles_dropped", "casting roles dropped")):
        if r[k]:
            lines.append(f"  {label}: {', '.join(r[k])}")
    for d, v in r["departments"].items():
        lines.append(f"  {d}: +{', '.join(v['new_to_script']) or '-'}  -{', '.join(v['dropped_from_script']) or '-'}")
    for x in r["affected_schedule"]:
        lines.append(f"  ! scheduled scene {x['scene']} ({x['status']}) on shooting day {x['shooting_day']} of {x['schedule']}")
    for sc in r["scenes"]:
        if sc["status"] == "unchanged":
            continue
        ref = sc["new"] or sc["old"]
        bits = [f"{sc['status'].upper():9} sc {ref['number'] or '#' + str(ref['seq']):>4}"]
        if sc["renumbered"]:
            bits.append(f"(was {sc['old']['number']})")
        if sc["heading_changes"]:
            bits.append("; ".join(sc["heading_changes"]))
        if sc["location"]:
            bits.append(f"location {sc['location']['from']} -> {sc['location']['to']}")
        if sc["cast_added"]:
            bits.append("cast +" + ", ".join(sc["cast_added"]))
        if sc["cast_removed"]:
            bits.append("cast -" + ", ".join(sc["cast_removed"]))
        if sc["presence_changed"]:
            bits.append("; ".join(sc["presence_changed"]))
        for d, v in sc["departments"].items():
            parts = [f"+{x}" for x in v["added"]] + [f"-{x}" for x in v["removed"]] + v["quantity_changed"]
            bits.append(f"{d}: {', '.join(parts)}")
        if sc["needs_review"]:
            bits.append(f"[review: {sc['review_reason']}]")
        lines.append("  " + " | ".join(bits))
    return "\n".join(lines)


def cmd_revision_report(a) -> int:
    from .revisions.diff import revision_report
    s = _session(a.db)
    r = revision_report(s, a.revision)
    print(json.dumps(r, indent=2) if a.json else _format_revision(r))
    return 0


def cmd_story_days(a) -> int:
    from .scheduling.story_days import infer_story_days
    s = _session(a.db)
    print(infer_story_days(s, a.version))
    s.commit()
    return 0


def cmd_schedule(a) -> int:
    from .scheduling import service as sv
    from .scheduling.reports import schedule_report
    s = _session(a.db)
    if a.create:
        sch = sv.create_schedule(s, a.version, a.create, a.drop_after)
        if a.autofill:
            sv.autofill(s, sch.id, a.autofill)
        s.commit()
        print(f"Schedule {sch.id} created")
        sid = sch.id
    else:
        sid = a.schedule
    r = schedule_report(s, sid)
    sv_ = r["story_vs_shooting"]
    print(f"SCHEDULE {r['schedule']['name']}: {sv_['shooting_days']} SHOOTING day(s) | {sv_['story_days']} STORY day(s)"
          f"{' (inferred)' if sv_['story_days_inferred'] else ''}\n  {sv_['explanation']}")
    for d in r["oneliner"]:
        print(f"\n  SHOOT DAY {d['day_number']}{' ' + d['date'] if d['date'] else ''} — "
              f"{d['eighths'] // 8} {d['eighths'] % 8}/8 pp — story: {', '.join(d['story_days']) or '?'}  {d['label'] or ''}")
        for e in d["scenes"]:
            print(f"     sc {e['scene']:>4} {e['int_ext']:7} {e['time']:12} story {e['story_day'] or '?':8} "
                  f"{e['location'][:32]:32} cast {','.join(map(str, e['cast_ids']))}")
    if r["unscheduled"]:
        print(f"\n  UNSCHEDULED: {', '.join(x['scene'] for x in r['unscheduled'])}")
    days = r["dood"]["days"]
    print("\n  DAY OUT OF DAYS (casting requirements x SHOOTING days)")
    print("  " + f"{'#':>3} {'REQUIREMENT':26} {'CHARACTER':16}" + "".join(f"{d['day_number']:>4}" for d in days)
          + "  WORK HOLD DROP")
    for c in r["dood"]["cast"]:
        req = c["requirement"] + (" *" if c["implicit"] else "")
        print("  " + f"{c['cast_id']:>3} {req[:26]:26} {c['character'][:16]:16}"
              + "".join(f"{c['codes'][d['day_number']]:>4}" for d in days)
              + f"  {c['work_days']:>4} {c['hold_days']:>4} {c['drop_days']:>4}")
    if r["dood"]["implicit_requirements"]:
        print("  * implicit: no casting role yet for this character (create roles to firm these up)")
    if r["dood"]["voice"]:
        print("  voice (not on set): " + ", ".join(v["requirement"] for v in r["dood"]["voice"]))
    if r["dood"]["background_groups"]:
        print("  background groups (not cast): " + ", ".join(g["requirement"] for g in r["dood"]["background_groups"]))
    for w in r["warnings"]:
        print(f"  ! {w}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="Screenplay breakdown")
    ap.add_argument("--db", help="database URL (default sqlite:///./breakdown.db or $DATABASE_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="import a script (PDF/DOCX/FDX/Fountain) and print the coverage check")
    p.add_argument("file")
    p.add_argument("--project", default="Untitled Project")
    p.add_argument("--label")
    p.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. eng+hin")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("coverage", help="print the coverage check for a script version")
    p.add_argument("version", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_coverage)
    p = sub.add_parser("scenes", help="list detected scenes")
    p.add_argument("version", type=int)
    p.set_defaults(fn=cmd_scenes)
    p = sub.add_parser("extract", help="per-scene extraction of characters and locations")
    p.add_argument("version", type=int)
    p.add_argument("--extractor", choices=["claude", "heuristic"], default="claude")
    p.add_argument("--model", help="Claude model id (default claude-opus-5-5 or $BREAKDOWN_MODEL)")
    p.add_argument("--effort", help="low|medium|high|xhigh|max (default medium)")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("reconcile", help="propose character/location merges into the review queue")
    p.add_argument("version", type=int)
    p.add_argument("--accept-all", action="store_true", help="accept every proposal (testing only)")
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("accuracy", help="score the stored breakdown against a ground-truth JSON")
    p.add_argument("version", type=int)
    p.add_argument("truth")
    p.set_defaults(fn=cmd_accuracy)
    p = sub.add_parser("report", help="scenes | characters | locations | combinations | crosstab:<kind>")
    p.add_argument("version", type=int)
    p.add_argument("name")
    p.add_argument("--time", choices=["as_written", "effective"], default="as_written")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("departments", help="Stage 2: departmental breakdown extraction")
    p.add_argument("version", type=int)
    p.add_argument("--departments", help="comma-separated, e.g. vehicles,props (default: all)")
    p.add_argument("--extractor", choices=["claude", "heuristic"], default="claude")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_departments)
    p = sub.add_parser("dept-report", help="department report: vehicles | props | ... | continuity")
    p.add_argument("version", type=int)
    p.add_argument("name")
    p.add_argument("--time", choices=["as_written", "effective"], default="as_written")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_dept_report)
    p = sub.add_parser("dept-accuracy", help="score department items against a ground-truth JSON")
    p.add_argument("version", type=int)
    p.add_argument("truth")
    p.add_argument("--departments")
    p.set_defaults(fn=cmd_dept_accuracy)
    p = sub.add_parser("revise", help="Stage 3: compare a revised draft with the previous one")
    p.add_argument("old", type=int, help="previous script version id")
    p.add_argument("new", type=int, help="revised script version id (import it first)")
    p.add_argument("--extractor", choices=["claude", "heuristic", "none"], default="none",
                   help="extract changed/added scenes (default: none)")
    p.add_argument("--departments", help="also run department extraction: 'all' or a comma list")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_revise)
    p = sub.add_parser("revision-report", help="print a stored revision report")
    p.add_argument("revision", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_revision_report)
    p = sub.add_parser("story-days", help="infer STORY days (story time, not shooting days)")
    p.add_argument("version", type=int)
    p.set_defaults(fn=cmd_story_days)
    p = sub.add_parser("schedule", help="create/print a shooting schedule, one-liner and DOOD")
    p.add_argument("version", type=int)
    p.add_argument("--create", metavar="NAME")
    p.add_argument("--autofill", type=int, metavar="EIGHTHS", help="suggest days of at most N eighths")
    p.add_argument("--drop-after", type=int, default=10)
    p.add_argument("--schedule", type=int, help="existing schedule id to print")
    p.set_defaults(fn=cmd_schedule)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
