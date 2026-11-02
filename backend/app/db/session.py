"""Engine and session factory. SQLite by default; set DATABASE_URL for Postgres."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

DEFAULT_URL = "sqlite:///./breakdown.db"


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys unless asked; we rely on them for integrity.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    """Create tables. (Alembic migrations take over once the schema is frozen for shipping.)"""
    from .. import models  # noqa: F401  - register mappers

    models.Base.metadata.create_all(engine)
