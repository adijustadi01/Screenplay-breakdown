import json

from sqlalchemy import select

from app.departments import registry
from app.departments.heuristic import DepartmentHeuristicExtractor
from app.departments.service import run_department_extraction
from app.extraction.heuristic_extractor import HeuristicExtractor
from app.extraction.service import run_extraction
from app.importers.service import import_script
from app.models import ReviewItem
from app.reconciliation.service import propose
from app.review.service import bulk_act

from .conftest import SAMPLES

EXPECTED = json.loads((SAMPLES / "monsoon_ledger_rev_blue.expected.json").read_text())


def prepared_draft(db, project, departments=True):
    v = import_script(db, project.id, SAMPLES / "monsoon_ledger.fountain")
    run_extraction(db, v.id, HeuristicExtractor(), workers=1)
    propose(db, v.id)
    bulk_act(db, [i.id for i in db.scalars(select(ReviewItem).where(ReviewItem.category == "reconciliation"))],
             "accept", "test")
    if departments:
        run_department_extraction(db, v.id, DepartmentHeuristicExtractor(registry.get()), workers=1)
    return v


def revised(db, project):
    return import_script(db, project.id, SAMPLES / "monsoon_ledger_rev_blue.fountain")


def extractors():
    return HeuristicExtractor(), DepartmentHeuristicExtractor(registry.get())
