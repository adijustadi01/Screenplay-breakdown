"""Scene detection must flag problems, never paper over them."""
from sqlalchemy import select

from app.importers.base import nonspace_len
from app.importers.coverage import coverage_report
from app.importers.fdx import parse_eighths
from app.importers.fountain import parse_fountain_text
from app.importers.service import _persist
from app.models import ExtractionRun, ReviewItem, Scene, ScriptVersion
from app.models.enums import RunKind, SceneNumberStatus, SourceFormat
from app.scene_detection.pagination import format_eighths


def _load(db, project, text):
    result = parse_fountain_text(text)
    v = ScriptVersion(project_id=project.id, source_filename="t.fountain",
                      source_format=SourceFormat.FOUNTAIN, source_sha256="0" * 64)
    db.add(v)
    db.flush()
    run = ExtractionRun(script_version_id=v.id, kind=RunKind.IMPORT)
    db.add(run)
    db.flush()
    _persist(db, v, run, result)
    scenes = db.scalars(select(Scene).where(Scene.script_version_id == v.id).order_by(Scene.seq)).all()
    codes = [i.reason_code for i in db.scalars(select(ReviewItem).where(ReviewItem.script_version_id == v.id))]
    return v, scenes, codes


def test_duplicate_and_out_of_order_numbers(db, project):
    text = ("INT. A - DAY #5#\n\nAction.\n\nINT. B - DAY #3#\n\nAction.\n\n"
            "INT. C - DAY #6#\n\nAction.\n\nINT. D - NIGHT #6#\n\nAction.\n")
    _, scenes, codes = _load(db, project, text)
    assert scenes[1].number_status == SceneNumberStatus.OUT_OF_ORDER
    assert scenes[2].number_status == SceneNumberStatus.DUPLICATE
    assert scenes[3].number_status == SceneNumberStatus.DUPLICATE
    assert "scene_number_out_of_order" in codes and "duplicate_scene_number" in codes
    assert [s.scene_number for s in scenes] == ["5", "3", "6", "6"]  # preserved verbatim


def test_unnumbered_script_is_one_info_item_and_no_numbers_generated(db, project):
    _, scenes, codes = _load(db, project, "INT. A - DAY\n\nX.\n\nEXT. B - NIGHT\n\nY.\n")
    assert all(s.scene_number is None for s in scenes)
    assert codes.count("script_unnumbered") == 1 and "missing_scene_number" not in codes


def test_story_text_before_first_heading_becomes_flagged_inferred_scene(db, project):
    text = ("A man runs through rain.\n\nARJUN\nWhere is she?\n\nHe stops.\n\n"
            "INT. HOUSE - DAY #1#\n\nQuiet.\n")
    v, scenes, codes = _load(db, project, text)
    assert scenes[0].heading_status.value == "inferred" and scenes[0].needs_review
    assert "missing_heading" in codes
    r = coverage_report(db, v.id)
    assert r["elements"]["unassigned"] == []  # nothing dropped: it's in the inferred scene
    assert r["verdict"] == "ATTENTION NEEDED"  # but the AD must look


def test_continuous_chain_and_unresolvable(db, project):
    text = ("INT. A - CONTINUOUS #1#\n\nX.\n\nEXT. B - NIGHT #2#\n\nY.\n\n"
            "INT. C - CONTINUOUS #3#\n\nZ.\n\nINT. D - SAME #4#\n\nW.\n")
    _, scenes, _ = _load(db, project, text)
    assert scenes[0].effective_time_of_day is None and scenes[0].needs_review  # nothing to inherit from
    assert scenes[2].effective_time_of_day.value == "NIGHT"
    assert scenes[3].effective_time_of_day.value == "NIGHT"
    assert scenes[3].effective_time_source_scene_id == scenes[1].id


def test_dropped_text_is_detected(db, project):
    result = parse_fountain_text("INT. A - DAY #1#\n\nSome action here.\n")
    result.source_char_count += 7  # simulate text the importer failed to place
    v = ScriptVersion(project_id=project.id, source_filename="t", source_format=SourceFormat.FOUNTAIN,
                      source_sha256="0" * 64)
    db.add(v)
    db.flush()
    run = ExtractionRun(script_version_id=v.id, kind=RunKind.IMPORT)
    db.add(run)
    db.flush()
    _persist(db, v, run, result)
    r = coverage_report(db, v.id)
    assert not r["text_accounting"]["ok"] and r["verdict"] == "ATTENTION NEEDED"


def test_eighths_helpers():
    assert parse_eighths("1 5/8") == 13 and parse_eighths("3/8") == 3 and parse_eighths("2") == 16
    assert format_eighths(13) == "1 5/8" and format_eighths(3) == "3/8" and format_eighths(16) == "2"
    assert nonspace_len(" a b\n c ") == 3


def test_fdx_scene_properties_used_when_complete(db, project, tmp_path):
    fdx = tmp_path / "t.fdx"
    fdx.write_text(
        '<?xml version="1.0"?><FinalDraft DocumentType="Script"><Content>'
        '<Paragraph Type="Scene Heading" Number="1"><SceneProperties Length="1 2/8" Page="1"/>'
        '<Text>INT. A - DAY</Text></Paragraph><Paragraph Type="Action"><Text>X.</Text></Paragraph>'
        '<Paragraph Type="Scene Heading" Number="2"><SceneProperties Length="3/8" Page="2"/>'
        '<Text>EXT. B - NIGHT</Text></Paragraph><Paragraph Type="Action"><Text>Y.</Text></Paragraph>'
        '</Content></FinalDraft>')
    from app.importers.service import import_script
    v = import_script(db, project.id, fdx)
    scenes = db.scalars(select(Scene).where(Scene.script_version_id == v.id).order_by(Scene.seq)).all()
    assert [s.length_eighths for s in scenes] == [10, 3]
    assert [s.start_page_label for s in scenes] == ["1", "2"]
    assert scenes[0].length_method.value == "source_document"


def test_back_to_back_headings_without_blank_line_are_two_scenes():
    from app.importers.layout import VisualLine, classify_lines
    from app.models.enums import ElementType
    lines = [
        VisualLine(1, "OMITTED", "24   OMITTED   24", 0.0, 100, 110, "24", "24", gap_before=True),
        VisualLine(1, "INT. POLICE STATION - DAY", "INT. POLICE STATION - DAY", 0.0, 112, 122, gap_before=False),
        VisualLine(1, "INT. CITY HOSPITAL - ICU -", "INT. CITY HOSPITAL - ICU -", 0.0, 140, 150, "5", "5"),
        VisualLine(1, "NIGHT", "NIGHT", 0.0, 152, 162, gap_before=False),  # genuinely wrapped heading
    ]
    els = classify_lines(lines)
    heads = [e.text for e in els if e.element_type == ElementType.SCENE_HEADING]
    assert heads == ["OMITTED", "INT. POLICE STATION - DAY", "INT. CITY HOSPITAL - ICU - NIGHT"]
