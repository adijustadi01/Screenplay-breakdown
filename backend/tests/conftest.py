import json
import shutil
from pathlib import Path

import pytest

from app.db.session import init_db, make_engine, make_session_factory
from app.models import Project

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
GROUND_TRUTH = json.loads((SAMPLES / "monsoon_ledger.ground_truth.json").read_text())
HAS_TESSERACT = shutil.which("tesseract") is not None


@pytest.fixture()
def db():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture()
def project(db):
    p = Project(title="Test")
    db.add(p)
    db.flush()
    return p
