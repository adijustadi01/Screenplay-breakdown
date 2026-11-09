"""Step 2: per-scene extraction — verification, provenance and locking."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extraction.claude_extractor import ClaudeExtractor
from app.extraction.cues import cue_presence, parse_cue
from app.extraction.evaluate import evaluate
from app.extraction.heuristic_extractor import HeuristicExtractor
from app.extraction.service import ExtractionAborted, run_extraction
from app.importers.service import import_script
from app.models import Appearance, Character, ReviewItem
from app.models.enums import Presence, ReviewStatus

from .conftest import SAMPLES
from .helpers import TruthExtractor, pipeline, scenes

TRUTH = str(SAMPLES / "monsoon_ledger.ground_truth.json")


def test_cue_parsing():
    assert parse_cue("MEERA (V.O.)") == ("MEERA", {"V.O."})
    assert parse_cue("VIKRAM (CONT'D)")[0] == "VIKRAM"
    assert cue_presence({"V.O."}) == Presence.VOICE_ONLY
    assert cue_presence({"O.S."}) == Presence.OFF_SCREEN
    assert cue_presence({"CONT'D"}) == Presence.PRESENT


def test_perfect_extractor_scores_100_after_reconciliation(db, project):
    v = pipeline(db, project, reconcile=True)
    r = evaluate(db, v.id, TRUTH)
    assert r["appearances"]["precision"] == 100.0 and r["appearances"]["recall"] == 100.0, r["problems"]
    assert r["presence_accuracy"] == 100.0 and r["dialogue_count_accuracy"] == 100.0
    assert r["location_accuracy"] == 100.0, r["problems"]


@pytest.mark.parametrize("fmt", ["monsoon_ledger.fountain", "monsoon_ledger.pdf", "monsoon_ledger.fdx"])
def test_heuristic_baseline_accuracy(db, project, fmt):
    v = pipeline(db, project, fmt, HeuristicExtractor(), reconcile=True)
    r = evaluate(db, v.id, TRUTH)
    assert r["appearances"]["recall"] >= 90 and r["appearances"]["precision"] >= 90, r["problems"]
    assert r["speaking_accuracy"] == 100.0  # speaking comes from cues, in code


def test_cues_override_the_model(db, project):
    def lie(label, out):
        for c in out.characters:
            if label == "6" and c.canonical_name == "MEERA":
                c.presence = "present"      # model forgets the (V.O.)
            if label == "1" and c.canonical_name == "ARJUN":
                c.speaking = "silent"       # model says silent despite cues
        return out
    v = pipeline(db, project, extractor=TruthExtractor(lie))
    s = scenes(db, v)
    meera = db.scalars(select(Character).where(Character.canonical_name == "MEERA")).one()
    a6 = db.scalars(select(Appearance).where(Appearance.scene_id == s[5].id,
                                             Appearance.character_id == meera.id)).one()
    assert a6.presence == Presence.VOICE_ONLY and "llm_disagrees_with_cue" in a6.review_reason
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    a1 = db.scalars(select(Appearance).where(Appearance.scene_id == s[0].id,
                                             Appearance.character_id == arjun.id)).one()
    assert a1.speaking.value == "speaking" and a1.dialogue_block_count == 2


def test_invented_evidence_is_flagged(db, project):
    def fake(label, out):
        if label == "1":
            out.characters[2].evidence = "Dadi dances on the table"  # not in the script
        return out
    v = pipeline(db, project, extractor=TruthExtractor(fake))
    dadi = db.scalars(select(Character).where(Character.canonical_name == "DADI")).one()
    a = db.scalars(select(Appearance).where(Appearance.scene_id == scenes(db, v)[0].id,
                                            Appearance.character_id == dadi.id)).one()
    assert a.needs_review and "evidence_not_found" in a.review_reason and a.confidence < 0.7
    assert db.scalars(select(ReviewItem).where(ReviewItem.entity_table == "appearances",
                                               ReviewItem.entity_id == a.id)).first() is not None


def test_unknown_headcount_flagged_and_known_headcount_kept(db, project):
    v = pipeline(db, project)
    s = scenes(db, v)
    goons = db.scalars(select(Appearance).join(Character).where(Character.canonical_name == "GOONS")).one()
    assert (goons.quantity_min, goons.quantity_max) == (3, 3) and not goons.needs_review
    men = db.scalars(select(Appearance).join(Character).where(Character.canonical_name == "VIKRAM'S MEN")).one()
    assert men.quantity_min is None and "unknown_headcount" in men.review_reason
    assert men.scene_id == s[12].id


def test_reextraction_keeps_ad_confirmed_facts(db, project):
    from app.review.service import edit_fact
    v = pipeline(db, project)
    dadi = db.scalars(select(Character).where(Character.canonical_name == "DADI")).one()
    a = db.scalars(select(Appearance).where(Appearance.character_id == dadi.id)).first()
    edit_fact(db, "appearances", a.id, {"presence": "uncertain"}, "AD")
    run_extraction(db, v.id, TruthExtractor(), workers=1)
    kept = db.get(Appearance, a.id)
    assert kept is not None and kept.presence == Presence.UNCERTAIN and kept.review_status == ReviewStatus.EDITED
    # and no duplicate row was added for the same character/scene
    assert len(db.scalars(select(Appearance).where(Appearance.scene_id == a.scene_id,
                                                   Appearance.character_id == dadi.id)).all()) == 1


def test_int_ext_suggestion_is_a_proposal_not_applied(db, project):
    v = pipeline(db, project)
    rooftop = scenes(db, v)[12]
    assert rooftop.int_ext.value == "UNKNOWN"
    item = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "int_ext_suggestion")).one()
    assert item.proposal["value"] == "EXT"


# ----------------------------------------------------------------------- Claude client (mocked)
class FakeMessages:
    def __init__(self, payload=None, stop_reason="end_turn", exc=None):
        self.calls, self.payload, self.stop_reason, self.exc = [], payload, stop_reason, exc

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def _client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


PAYLOAD = {"characters": [{"name_as_written": "DADI", "canonical_name": "DADI", "presence": "present",
                           "speaking": "silent", "is_group": False, "quantity_text": None, "quantity_min": None,
                           "quantity_max": None, "age_or_version": None,
                           "evidence": "DADI (80s), wrapped in a white cotton saree", "confidence": 0.9,
                           "uncertainty_note": None}],
           "location": {"story_location": "ARJUN'S HOUSE", "sub_location": "KITCHEN", "modifiers": [],
                        "evidence": "ARJUN'S HOUSE - KITCHEN", "confidence": 0.95},
           "synopsis": "Arjun and Meera argue about money.", "int_ext_suggestion": "UNKNOWN", "notes": None}


def test_claude_request_shape_and_parsing():
    msgs = FakeMessages(PAYLOAD)
    ex = ClaudeExtractor(client=_client(msgs))
    ex.set_script_context(["ARJUN", "MEERA"], ["ARJUN'S HOUSE - KITCHEN"], ["DADI"])
    out = ex.extract("SCENE 1 — HEADING: INT. ARJUN'S HOUSE - KITCHEN - DAY", {})
    kw = msgs.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "DADI" in kw["system"][-1]["text"]  # script-wide context is in the cached block
    assert out.characters[0].canonical_name == "DADI"


def test_claude_refusal_becomes_failed_scene_not_a_guess(db, project):
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.fountain")
    ex = ClaudeExtractor(client=_client(FakeMessages(PAYLOAD, stop_reason="refusal")))
    run = run_extraction(db, v.id, ex, workers=2)
    assert run.status.value == "failed"
    items = db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "extraction_failed")).all()
    assert len(items) == 14  # every non-omitted scene flagged; nothing invented


def test_bad_credentials_abort_the_run(db, project):
    import anthropic
    import httpx
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.fountain")
    err = anthropic.AuthenticationError("bad key", response=httpx.Response(
        401, request=httpx.Request("POST", "https://api.anthropic.com")), body=None)
    ex = ClaudeExtractor(client=_client(FakeMessages(exc=err)))
    with pytest.raises(ExtractionAborted):
        run_extraction(db, v.id, ex, workers=1)
    assert not db.scalars(select(ReviewItem).where(ReviewItem.reason_code == "extraction_failed")).all()


def test_capitalised_props_are_not_characters():
    """Props and sounds in CAPS after 'a/the' ('a MIRROR') must not become characters;
    people introduced the same way ('A WARD BOY') still do, with all their words."""
    from app.extraction.heuristic_extractor import intro_names
    names = intro_names(["She looks into A MIRROR. A KNIFE glints. The GUNSHOT echoes.",
                         "A WARD BOY wheels a stretcher past. A CONSTABLE salutes.",
                         "The TAXI DRIVER watches him in the mirror."])
    assert names == {"WARD BOY", "CONSTABLE", "TAXI DRIVER"}
