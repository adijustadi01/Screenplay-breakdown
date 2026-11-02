import pytest

from app.models.enums import HeadingStatus, IntExt, TimeOfDay
from app.scene_detection.headings import parse_heading, scene_number_sort_key

CASES = [
    # raw, number, int_ext, location, time, flags subset
    ("INT. ARJUN'S HOUSE - KITCHEN - NIGHT", None, IntExt.INT, "ARJUN'S HOUSE - KITCHEN", TimeOfDay.NIGHT, set()),
    ("23A  INT. HOUSE - DAY  23A", "23A", IntExt.INT, "HOUSE", TimeOfDay.DAY, set()),
    ("A23 EXT. FIELD - DUSK A23", "A23", IntExt.EXT, "FIELD", TimeOfDay.DUSK, set()),
    ("EXT. MARKET - DUSK #5#", "5", IntExt.EXT, "MARKET", TimeOfDay.DUSK, set()),
    ("INT./EXT. CAR - MOVING - NIGHT", None, IntExt.INT_EXT, "CAR - MOVING", TimeOfDay.NIGHT, set()),
    ("EXT/INT CAR - DAY", None, IntExt.INT_EXT, "CAR", TimeOfDay.DAY, set()),
    ("I/E VAN - DAY", None, IntExt.INT_EXT, "VAN", TimeOfDay.DAY, set()),
    ("INTERIOR TEMPLE - DAWN", None, IntExt.INT, "TEMPLE", TimeOfDay.DAWN, set()),
    ("INT. HOUSE - CONTINUOUS", None, IntExt.INT, "HOUSE", TimeOfDay.CONTINUOUS, set()),
    ("INT. HOUSE - MOMENTS LATER", None, IntExt.INT, "HOUSE", TimeOfDay.MOMENTS_LATER, set()),
    ("INT. HOUSE - SAME TIME", None, IntExt.INT, "HOUSE", TimeOfDay.SAME, set()),
    ("EXT. FIELD - DAY (FLASHBACK)", None, IntExt.EXT, "FIELD", TimeOfDay.DAY, set()),
    ("INT. HOUSE - NIGHT - CONTINUOUS", None, IntExt.INT, "HOUSE", TimeOfDay.NIGHT, {"multiple_times"}),
    ("INT. TEMPLE - LATE NIGHT", None, IntExt.INT, "TEMPLE", TimeOfDay.OTHER, {"unrecognised_time"}),
    ("INT. APARTMENT 4B", None, IntExt.INT, "APARTMENT 4B", TimeOfDay.UNSPECIFIED, {"no_time_of_day"}),
    ("INT. HOUSE DAY", None, IntExt.INT, "HOUSE", TimeOfDay.DAY, {"time_not_separated"}),
    ("HOSPITAL ROOFTOP - NIGHT", None, IntExt.UNKNOWN, "HOSPITAL ROOFTOP", TimeOfDay.NIGHT, {"no_int_ext"}),
    (".HOSPITAL ROOFTOP - NIGHT #12#", "12", IntExt.UNKNOWN, "HOSPITAL ROOFTOP", TimeOfDay.NIGHT, {"no_int_ext"}),
    ("EXT. ROAD -- NIGHT", None, IntExt.EXT, "ROAD", TimeOfDay.NIGHT, set()),
    ("INT. OFFICE – EVENING", None, IntExt.INT, "OFFICE", TimeOfDay.EVENING, set()),
    ("INT. HOUSE - DAY *", None, IntExt.INT, "HOUSE", TimeOfDay.DAY, set()),
]


@pytest.mark.parametrize("raw,num,ie,loc,tod,flags", CASES)
def test_parse_heading(raw, num, ie, loc, tod, flags):
    h = parse_heading(raw)
    assert h.scene_number == num
    assert h.int_ext == ie
    assert h.location_raw == loc
    assert h.time_of_day == tod
    assert flags <= set(h.flags)


def test_dusk_and_dawn_are_never_collapsed():
    assert parse_heading("EXT. X - DUSK").time_of_day is TimeOfDay.DUSK
    assert parse_heading("EXT. X - DAWN").time_of_day is TimeOfDay.DAWN


def test_omitted():
    h = parse_heading("12 OMITTED 12")
    assert h.is_omitted and h.scene_number == "12"


def test_margin_numbers_win_and_mismatch_flagged():
    h = parse_heading("INT. HOUSE - DAY", margin_left="23", margin_right="24")
    assert (h.scene_number_left, h.scene_number_right) == ("23", "24")
    assert "number_mismatch" in h.flags


def test_unprefixed_heading_is_ambiguous_with_lower_confidence():
    h = parse_heading("ROOFTOP - NIGHT")
    assert h.status is HeadingStatus.AMBIGUOUS and h.confidence < 0.7


def test_scene_number_ordering():
    keys = [scene_number_sort_key(n) for n in ["1", "2", "A3", "3", "3A", "3B", "4"]]
    assert keys == sorted(keys)
