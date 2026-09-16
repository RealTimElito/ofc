"""Database session helpers."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        url = f"sqlite:///{settings.db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def _ensure_sqlite_columns(engine) -> None:
    """Lightweight additive migrations for existing air-gapped installs."""
    alterations = {
        "document_ids_json": "TEXT DEFAULT '[]'",
        "example_file_ids_json": "TEXT DEFAULT '[]'",
        "use_all_examples": "BOOLEAN DEFAULT 1",
        "style_notes_md": "TEXT DEFAULT ''",
        "style_notes_key": "TEXT DEFAULT ''",
        "theme_json": "TEXT DEFAULT '{}'",
    }
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(report_projects)")).fetchall()
        if not rows:
            return
        cols = {r[1] for r in rows}
        for name, decl in alterations.items():
            if name not in cols:
                conn.execute(
                    text(f"ALTER TABLE report_projects ADD COLUMN {name} {decl}")
                )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)


def get_db() -> Generator[Session, None, None]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
