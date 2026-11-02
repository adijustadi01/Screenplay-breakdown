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
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
