"""Shared words for reading continuity states from action lines (offline rules)."""
import re

COSTUME_STATES = (
    ("bloodied", r"blood(?:ied|y|-soaked|stained)?"),
    ("torn", r"torn|ripped|tears loose|shredded"),
    ("wet", r"soaked|drenched|wet|dripping"),
    ("damp", r"damp"),
    ("dirty", r"muddy|dirty|grimy|dusty"),
    ("burnt", r"burnt|scorched|singed"),
    ("powdered", r"powder|streaked"),
)
MAKEUP_STATES = (
    ("bandaged", r"bandag\w*"),
    ("fresh", r"opens|fresh|bleeding|new cut"),
    ("healing", r"healing|scabbed|fading"),
    ("healed", r"healed"),
)


def state_from(sentence: str, table) -> dict:
    for state, rx in table:
        if re.search(rf"\b(?:{rx})\b", sentence, re.IGNORECASE):
            return {"state": state}
    return {}
