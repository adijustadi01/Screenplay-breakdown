"""Shared pipeline helpers for Stage 1 tests."""
import re

from sqlalchemy import select

from app.extraction.heuristic_extractor import split_location
from app.extraction.schema import CharacterOut, LocationOut, SceneExtraction
from app.extraction.service import run_extraction
from app.importers.service import import_script
from app.models import Scene

from .conftest import GROUND_TRUTH, SAMPLES


class TruthExtractor:
    """Plays the role of a perfect LLM: answers from the ground truth file.

    Lets us test everything downstream of the model (verification, persistence,
    reconciliation, reports, review) deterministically.
    """

    name = "truth"
    model = "truth"
    prompt_version = "truth"

    def __init__(self, mutate=None):
        self.by_heading = {}
        self.mutate = mutate

    def set_script_context(self, *a, **k):
        pass

    def extract(self, scene_text, ctx):
        m = re.search(r"^SCENE (\S+)", scene_text)
        label = m.group(1)
        t = next(s for s in GROUND_TRUTH["scenes"]
                 if (s.get("number") or "(unnumbered)") == label and not s.get("omitted"))
        chars = []
        for c in t["characters"]:
            written = c.get("casting") or c["character"]
            ev = _evidence(scene_text, written, c.get("aliases", []))
            chars.append(CharacterOut(
                name_as_written=written, canonical_name=written, presence=c["presence"], speaking=c["speaking"],
                is_group=c.get("kind") == "group", quantity_text=None, quantity_min=c.get("quantity_min"),
                quantity_max=c.get("quantity_max"), age_or_version=None, evidence=ev, confidence=0.95,
                uncertainty_note=None))
        # Like a careful LLM: keep the heading's own spelling (reconciliation fixes variants).
        parent, sub, mods = split_location(t["location_raw"])
        out = SceneExtraction(
            characters=chars,
            location=LocationOut(story_location=parent, sub_location=sub, modifiers=mods,
                                 evidence=t["location_raw"] or "", confidence=0.95),
            synopsis=f"Scene {label}.", int_ext_suggestion="EXT" if t["int_ext"] == "UNKNOWN" else "UNKNOWN",
            notes=None)
        return self.mutate(label, out) if self.mutate else out


def _evidence(text, name, aliases):
    words = {"ARJUN": ["Arjun", "Papa", "husband"], "VIKRAM": ["Vikram", "Vikram's"], "KABIR": ["KABIR"]}
    for cand in [name, name.title(), *aliases, *words.get(name, []), name.split()[-1].title()]:
        i = text.find(cand)
        if i >= 0:
            return text[i:i + len(cand)]
    return name


def pipeline(db, project, fmt="monsoon_ledger.fountain", extractor=None, reconcile=False):
    from app.reconciliation.service import propose
    from app.review.service import bulk_act
    from app.models import ReviewItem
    v = import_script(db, project.id, SAMPLES / fmt)
    run_extraction(db, v.id, extractor or TruthExtractor(), workers=1)
    if reconcile:
        propose(db, v.id)
        ids = [i.id for i in db.scalars(select(ReviewItem).where(
            ReviewItem.script_version_id == v.id, ReviewItem.category == "reconciliation"))]
        bulk_act(db, ids, "accept", "test")
    db.flush()
    return v


def scenes(db, v):
    return db.scalars(select(Scene).where(Scene.script_version_id == v.id).order_by(Scene.seq)).all()
