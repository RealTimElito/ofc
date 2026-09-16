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
    report_alterations = {
        "document_ids_json": "TEXT DEFAULT '[]'",
        "example_file_ids_json": "TEXT DEFAULT '[]'",
        "use_all_examples": "BOOLEAN DEFAULT 1",
        "style_notes_md": "TEXT DEFAULT ''",
        "style_notes_key": "TEXT DEFAULT ''",
        "theme_json": "TEXT DEFAULT '{}'",
    }
    document_alterations = {
        "stored_docx_path": "TEXT DEFAULT ''",
    }
    with engine.begin() as conn:
        report_rows = conn.execute(text("PRAGMA table_info(report_projects)")).fetchall()
        if report_rows:
            cols = {r[1] for r in report_rows}
            for name, decl in report_alterations.items():
                if name not in cols:
                    conn.execute(
                        text(f"ALTER TABLE report_projects ADD COLUMN {name} {decl}")
                    )
        doc_rows = conn.execute(text("PRAGMA table_info(documents)")).fetchall()
        if doc_rows:
            cols = {r[1] for r in doc_rows}
            for name, decl in document_alterations.items():
                if name not in cols:
                    conn.execute(
                        text(f"ALTER TABLE documents ADD COLUMN {name} {decl}")
                    )


def _backfill_library_docx(engine) -> None:
    """Copy matching upload binaries into library_docx for legacy docx documents."""
    import shutil
    import uuid
    from pathlib import Path

    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    with Session(engine) as db:
        rows = db.execute(
            text(
                "SELECT id, filename, COALESCE(stored_docx_path, '') "
                "FROM documents WHERE lower(format) = 'docx' "
                "OR lower(filename) LIKE '%.docx'"
            )
        ).fetchall()
        updated = False
        for doc_id, filename, stored in rows:
            if (stored or "").strip() and Path(stored).is_file():
                continue
            if not filename:
                continue
            match = db.execute(
                text(
                    "SELECT stored_path FROM uploaded_files "
                    "WHERE original_name = :name ORDER BY id DESC LIMIT 1"
                ),
                {"name": filename},
            ).fetchone()
            if not match:
                continue
            src = Path(match[0])
            if not src.is_file():
                continue
            dest = settings.library_docx_dir / f"{uuid.uuid4().hex}.docx"
            shutil.copy2(src, dest)
            db.execute(
                text("UPDATE documents SET stored_docx_path = :p WHERE id = :id"),
                {"p": str(dest.resolve()), "id": doc_id},
            )
            updated = True
        if updated:
            db.commit()


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)
    _backfill_library_docx(engine)


def get_db() -> Generator[Session, None, None]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
