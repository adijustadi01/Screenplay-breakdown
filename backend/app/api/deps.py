"""Shared FastAPI dependencies."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from ..db.session import init_db, make_engine, make_session_factory

_factory: sessionmaker | None = None


def configure(url: str | None = None) -> sessionmaker:
    global _factory
    engine = make_engine(url)
    init_db(engine)
    _factory = make_session_factory(engine)
    return _factory


def get_session() -> Iterator[Session]:
    factory = _factory or configure(os.environ.get("DATABASE_URL"))
    with factory() as s:
        yield s
