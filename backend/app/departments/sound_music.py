"""Songs, dance & sound: song sequences, performers, dancers, instruments, playback,
voiceovers and sounds the script calls for.

Derived in code (certain, not guessed): every (V.O.) cue becomes a VOICEOVER item, and
every 'SONG: "TITLE"' line becomes a SONG item with its title and playback flag.
"""
from __future__ import annotations

import re

from ..models.enums import Department, ElementType
from .base import DepartmentSpec, DetailField, ItemOut, SceneContext

_SONG_RE = re.compile(r"^\s*SONG\s*[:\-]\s*[\"“']?([^\"”'(]+?)[\"”']?\s*(\((?:LIP[- ]?SYNC|PLAYBACK|LIVE)[^)]*\))?\s*\.?$",
                      re.I)


def _review(item: ItemOut, ctx: SceneContext) -> list[str]:
    reasons = []
    if item.category == "song":
        reasons.append("song_sequence")             # rights, playback, choreography, rehearsal days
        if item.details.get("playback") is None:
            reasons.append("playback_undecided")
    return reasons


def _derive(ctx: SceneContext, by_dept) -> list[ItemOut]:
    out = []
    for name in sorted(ctx.voice_only):
        cue = next((e for e in ctx.elements if e.element_type == ElementType.CHARACTER
                    and e.text.upper().startswith(name) and "V" in e.text.upper()), None)
        out.append(ItemOut(name=f"VOICEOVER — {name}", category="voiceover",
                           description=f"{name} heard in voice-over / off-camera voice",
                           characters=[{"name": name, "role": "speaker"}],
                           evidence=cue.text if cue else name, confidence=1.0, extracted_by="parser"))
    for e in ctx.elements:
        if e.element_type != ElementType.ACTION:
            continue
        m = _SONG_RE.match(e.text)
        if m:
            title = m.group(1).strip().upper()
            tag = (m.group(2) or "").upper()
            out.append(ItemOut(name=f"SONG: {title}", category="song", description=e.text,
                               details={"song_title": title, "playback": True if "PLAYBACK" in tag or "LIP" in tag
                                        else (False if "LIVE" in tag else None),
                                        "lip_sync": True if ("PLAYBACK" in tag or "LIP" in tag) else None},
                               evidence=e.text, confidence=1.0, extracted_by="parser"))
    return out


def _extra(row: dict, occs: list) -> dict:
    return {"playback": any(o.details.get("playback") for o in occs),
            "lip_sync": any(o.details.get("lip_sync") for o in occs),
            "song_title": next((o.details.get("song_title") for o in occs if o.details.get("song_title")), None),
            "performers": sorted({f"{c['name']} ({c['role']})" for c in row["characters"]})}


SPEC = DepartmentSpec(
    key=Department.SOUND_MUSIC,
    label="Songs, dance & sound",
    item_noun="song/sound",
    categories=("song", "dance", "instrument", "playback", "voiceover", "scripted_sound", "ambience",
                "score_cue", "live_music"),
    roles=("singer", "dancer", "musician", "performer", "speaker"),
    default_role="performer",
    details=(
        DetailField("song_title", "string", "Song title if given."),
        DetailField("playback", "boolean", "Pre-recorded playback on set (vs live or none)."),
        DetailField("lip_sync", "boolean", "Performers lip-sync to playback."),
        DetailField("live_recording", "boolean", "Sung/played live and recorded on set."),
        DetailField("choreography_needed", "boolean", "Choreographed dance."),
    ),
    prompt="""
Song sequences (title, who sings, playback/lip-sync or live), dance, on-screen instruments and
musicians, voice-overs and narration, and specific sounds the script calls for (sirens, a phone
ringing, temple bells, a gunshot off screen, monitors beeping). Not general background score.
- Song items: `characters` with role singer/dancer; set `playback`, `lip_sync`, `live_recording`
  only when the script says so (e.g. "(PLAYBACK)", "unaccompanied").
- Scripted sounds: one item per sound ("PHONE RINGS", "TEMPLE BELLS").
""",
    lexicon={
        "song": ("lullaby", "sings", "singing", "song", "national anthem", "anthem", "bhajan", "qawwali", "hums",
                 "humming"),
        "dance": ("dances", "dancing"),
        "instrument": ("dhol", "drums", "tabla", "guitar", "flute", "sitar", "harmonium", "shehnai"),
        "scripted_sound": ("siren wailing", "sirens", "siren", "phone rings", "rings", "gunshot", "temple bells",
                           "bells", "beeps", "barks", "bark", "thunder", "screech", r"blows (?:the|a|his|her) whistle",
                           "whistles", "roars?", "cheers?", "applause", "clapping", "banging", "knock(?:s|ing)?",
                           "honks?", "horn blares", "crows", "alarm", "screams?", "thud", "gunshots"),
    },
    derive=_derive,
    offline_category_names=("song", "dance"),
    review=_review,
    report_extra=_extra,
)
