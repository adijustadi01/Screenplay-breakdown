"""Stage 2 test helpers: a 'perfect LLM' answering from the department ground truth."""
import re

from app.departments import registry
from app.departments.base import ItemOut
from app.departments.service import run_department_extraction
from app.extraction.heuristic_extractor import HeuristicExtractor
from app.extraction.service import run_extraction
from app.importers.service import import_script

from .conftest import DEPT_TRUTH, SAMPLES


class TruthDeptExtractor:
    name = "truth"
    model = "truth"

    def __init__(self, departments=None, mutate=None):
        self.specs = registry.get(departments)
        self.prompt_version = "truth"
        self.mutate = mutate

    def set_script_context(self, cast, locations):
        pass

    def extract(self, scene_text, ctx):
        t = next(s for s in DEPT_TRUTH["scenes"] if s["number"] == ctx.label)
        out = {s.key.value: [] for s in self.specs}
        for ti in t["items"]:
            if ti["department"] not in out:
                continue
            ev = _evidence(scene_text, ti["match"])
            out[ti["department"]].append(ItemOut(
                name=ti["name"], category=ti["category"], description=ti["name"].title(),
                quantity_text=ti.get("qty_text"), quantity_min=ti["qty"], quantity_max=ti["qty"],
                characters=[{"name": n, "role": r} for n, r in ti["chars"].items()], details=dict(ti["details"]),
                evidence=ev, confidence=0.9, inferred=ti["inferred"],
                uncertainty_note="inferred from scene" if ti["inferred"] else None))
        return self.mutate(ctx.label, out) if self.mutate else out


def _evidence(text, keys):
    for k in keys:
        m = re.search(re.escape(k), text, re.IGNORECASE)
        if m:
            end = text.find(".", m.end())
            return text[m.start(): end if 0 < end - m.start() < 80 else m.end()]
    return keys[0]


def dept_pipeline(db, project, departments=None, extractor=None):
    v = import_script(db, project.id, SAMPLES / "highway_song.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    ex = extractor or TruthDeptExtractor(departments)
    run_department_extraction(db, v.id, ex, workers=1)
    db.flush()
    return v
