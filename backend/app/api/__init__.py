"""REST API routers."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.api.schemas import (
    DbConnectionIn,
    DbConnectionOut,
    DocumentIn,
    DocumentOut,
    FileOut,
    GenerateIn,
    HealthOut,
    LlmProfileIn,
    LlmProfileOut,
    MarkDoneIn,
    PipelineRunOut,
    PruneStubsIn,
    PruneStubsOut,
    QueryPreviewOut,
    ReportProjectIn,
    ReportProjectOut,
    ReportProjectUpdate,
    ReportTheme,
    SavedQueryIn,
    SavedQueryOut,
    SettingsOut,
    ThemeImportIn,
)
from app.config import get_settings
from app.db.session import get_db
from app.models import (
    DbConnection,
    Document,
    LlmProfile,
    PipelineRun,
    ReportProject,
    SavedQuery,
    UploadedFile,
)
from app.services.crypto import encrypt_secret
from app.services.db_connector import run_query
from app.services.docs import read_upload_text
from app.services.export_docx import markdown_to_docx_bytes
from app.services.export_html import render_html_export
from app.services.generate_jobs import (
    ACTIVE_STATUSES,
    is_job_active,
    job_snapshot,
    reconcile_report_job,
    request_cancel,
    start_generate,
)
from app.services.llm import LlmClient, config_from_profile, config_from_settings
from app.services.theme import (
    empty_theme,
    extract_theme_from_docx,
    parse_theme_json,
    theme_to_json,
)

router = APIRouter()


def _profile_out(p: LlmProfile) -> LlmProfileOut:
    return LlmProfileOut(
        id=p.id,
        name=p.name,
        base_url=p.base_url,
        model=p.model,
        temperature=p.temperature,
        max_tokens=p.max_tokens,
        system_prompt=p.system_prompt,
        is_default=p.is_default,
        has_api_key=bool(p.api_key_enc),
        created_at=p.created_at,
    )


def _theme_out(p: ReportProject) -> ReportTheme:
    data = parse_theme_json(getattr(p, "theme_json", None) or "{}")
    return ReportTheme(**{k: data.get(k) for k in empty_theme()})


def _project_out(p: ReportProject) -> ReportProjectOut:
    return ReportProjectOut(
        id=p.id,
        title=p.title,
        brief=p.brief,
        file_ids=json.loads(p.file_ids_json or "[]"),
        query_ids=json.loads(p.query_ids_json or "[]"),
        document_ids=json.loads(getattr(p, "document_ids_json", None) or "[]"),
        example_file_ids=json.loads(getattr(p, "example_file_ids_json", None) or "[]"),
        use_all_examples=bool(getattr(p, "use_all_examples", True)),
        llm_profile_id=p.llm_profile_id,
        status=p.status,
        style_notes_md=getattr(p, "style_notes_md", None) or "",
        style_notes_key=getattr(p, "style_notes_key", None) or "",
        outline_md=p.outline_md,
        body_md=p.body_md,
        critique_md=p.critique_md,
        theme=_theme_out(p),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _clear_project_style_notes(project: ReportProject) -> bool:
    """Drop stored style notes on a project. Returns True if anything changed."""
    had = bool(
        (getattr(project, "style_notes_md", None) or "").strip()
        or (getattr(project, "style_notes_key", None) or "").strip()
    )
    project.style_notes_md = ""
    project.style_notes_key = ""
    return had


def _invalidate_style_after_examples_changed(db: Session) -> int:
    """Wipe disk style cache and clear style notes on every report.

    Example packs are content-fingerprinted; when the library / uploads /
    example-query corpus changes, prior notes and cache entries can disagree
    with the next pack. Clearing is cheaper than a wrong formulation pass.
    """
    from app.services.style_cache import clear_style_cache

    settings = get_settings()
    settings.ensure_dirs()
    cleared_files = clear_style_cache(settings.style_cache_dir)
    for project in db.query(ReportProject).all():
        _clear_project_style_notes(project)
    db.commit()
    return cleared_files


def _role_includes_examples(role: str | None) -> bool:
    return (role or "") in ("example", "both", "examples")


def _document_out(row: Document) -> DocumentOut:
    from app.services.context import stub_example_reason

    reason = stub_example_reason(row.body_md or "")
    return DocumentOut(
        id=row.id,
        title=row.title,
        filename=row.filename,
        format=row.format,
        body_md=row.body_md,
        source_report_id=row.source_report_id,
        role=row.role,
        has_theme_docx=bool(row.has_theme_docx),
        is_stub=reason is not None,
        stub_reason=reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _unlink_library_docx(stored: str | None) -> None:
    stored = (stored or "").strip()
    if not stored:
        return
    path = Path(stored)
    settings = get_settings()
    try:
        if path.is_file() and path.resolve().is_relative_to(
            settings.library_docx_dir.resolve()
        ):
            path.unlink()
    except OSError:
        pass


def _delete_document_row(db: Session, row: Document) -> bool:
    """Delete a library document and its durable docx copy. Returns was_example."""
    was_example = _role_includes_examples(row.role)
    stored = getattr(row, "stored_docx_path", None)
    db.delete(row)
    db.flush()
    _unlink_library_docx(stored)
    return was_example


def _persist_library_docx(src: Path) -> str:
    """Copy a .docx into durable library storage; return absolute path string."""
    settings = get_settings()
    settings.ensure_dirs()
    dest = settings.library_docx_dir / f"{uuid.uuid4().hex}.docx"
    shutil.copy2(src, dest)
    return str(dest.resolve())


def _resolve_docx_path(db: Session, source: str, source_id: int) -> tuple[Path, str]:
    """Return (docx path, label) for theme extraction."""
    if source == "file":
        row = db.get(UploadedFile, source_id)
        if not row:
            raise HTTPException(404, "Upload not found")
        path = Path(row.stored_path)
        if path.suffix.lower() != ".docx":
            raise HTTPException(400, "Theme import needs a .docx upload")
        if not path.is_file():
            raise HTTPException(404, "Upload file missing on disk")
        return path, row.original_name

    if source == "document":
        doc = db.get(Document, source_id)
        if not doc:
            raise HTTPException(404, "Document not found")
        label = doc.filename or doc.title
        stored = (getattr(doc, "stored_docx_path", None) or "").strip()
        if stored:
            path = Path(stored)
            if path.is_file():
                return path, label or doc.title
        # Legacy fallback: matching upload by original name
        if (doc.format or "").lower() == "docx" or (doc.filename or "").lower().endswith(
            ".docx"
        ):
            match = (
                db.query(UploadedFile)
                .filter(UploadedFile.original_name == doc.filename)
                .order_by(UploadedFile.id.desc())
                .first()
            )
            if match and Path(match.stored_path).is_file():
                # Heal: persist a durable copy so future imports survive upload delete
                try:
                    doc.stored_docx_path = _persist_library_docx(Path(match.stored_path))
                    db.commit()
                except OSError:
                    pass
                return Path(match.stored_path), label or match.original_name
        raise HTTPException(
            400,
            "No .docx file found for this library document — upload the original "
            ".docx (or use Import theme from an upload).",
        )

    raise HTTPException(400, "source must be file|document")


def _archive_report_to_library(
    db: Session, report: ReportProject, role: str = "example"
) -> Document:
    filename = f"{report.title.strip().replace(' ', '_') or 'report'}.md"
    existing = (
        db.query(Document)
        .filter(Document.source_report_id == report.id)
        .order_by(Document.id.desc())
        .first()
    )
    if existing:
        existing.title = report.title
        existing.filename = filename
        existing.format = "markdown"
        existing.body_md = report.body_md or ""
        existing.role = role
        db.commit()
        db.refresh(existing)
        if _role_includes_examples(role):
            _invalidate_style_after_examples_changed(db)
        return existing
    doc = Document(
        title=report.title,
        filename=filename,
        format="markdown",
        body_md=report.body_md or "",
        source_report_id=report.id,
        role=role,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    if _role_includes_examples(role):
        _invalidate_style_after_examples_changed(db)
    return doc


@router.get("/health", response_model=HealthOut)
def health():
    s = get_settings()
    return HealthOut(status="ok", llm_configured=bool(s.llm_base_url and s.llm_model))


@router.get("/settings", response_model=SettingsOut)
def settings_view():
    s = get_settings()
    return SettingsOut(
        llm_base_url=s.llm_base_url,
        llm_model=s.llm_model,
        data_dir=str(s.ofc_data_dir.resolve()),
        air_gapped=True,
    )


# --- LLM profiles ---


@router.get("/llm/profiles", response_model=list[LlmProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_profile_out(p) for p in db.query(LlmProfile).order_by(LlmProfile.id).all()]


@router.post("/llm/profiles", response_model=LlmProfileOut)
def create_profile(body: LlmProfileIn, db: Session = Depends(get_db)):
    if body.is_default:
        for p in db.query(LlmProfile).filter(LlmProfile.is_default.is_(True)):
            p.is_default = False
    row = LlmProfile(
        name=body.name,
        base_url=body.base_url.rstrip("/"),
        api_key_enc=encrypt_secret(body.api_key) if body.api_key else "",
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        system_prompt=body.system_prompt,
        is_default=body.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _profile_out(row)


@router.delete("/llm/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    row = db.get(LlmProfile, profile_id)
    if not row:
        raise HTTPException(404, "Profile not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/llm/profiles/{profile_id}/ping")
async def ping_profile(profile_id: int, db: Session = Depends(get_db)):
    row = db.get(LlmProfile, profile_id)
    if not row:
        raise HTTPException(404, "Profile not found")
    client = LlmClient(config_from_profile(row))
    try:
        return await client.ping()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LLM unreachable: {exc}") from exc


@router.post("/llm/ping-default")
async def ping_default():
    client = LlmClient(config_from_settings())
    try:
        return await client.ping()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LLM unreachable: {exc}") from exc


# --- Files ---


@router.get("/files", response_model=list[FileOut])
def list_files(db: Session = Depends(get_db)):
    return db.query(UploadedFile).order_by(UploadedFile.id.desc()).all()


@router.post("/files", response_model=FileOut)
async def upload_file(
    file: UploadFile = File(...),
    role: str = Form("context"),
    db: Session = Depends(get_db),
):
    if role not in ("context", "example", "both"):
        raise HTTPException(400, "role must be context|example|both")
    settings = get_settings()
    settings.ensure_dirs()
    suffix = Path(file.filename or "upload").suffix
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    dest = settings.uploads_dir / stored_name
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)
    row = UploadedFile(
        original_name=file.filename or stored_name,
        stored_path=str(dest.resolve()),
        mime_type=file.content_type,
        size_bytes=size,
        role=role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Optionally also mirror extracted text into the document library for .docx/.md
    if suffix.lower() in {".docx", ".md", ".markdown", ".txt"}:
        body = read_upload_text(dest)
        if not body.startswith("["):
            stored_docx = ""
            if suffix.lower() == ".docx":
                try:
                    stored_docx = _persist_library_docx(dest)
                except OSError as exc:
                    raise HTTPException(
                        500, f"Failed to store library .docx copy: {exc}"
                    ) from exc
            db.add(
                Document(
                    title=Path(row.original_name).stem,
                    filename=row.original_name,
                    format="docx" if suffix.lower() == ".docx" else "text",
                    body_md=body,
                    role=role,
                    stored_docx_path=stored_docx,
                )
            )
            db.commit()
    if _role_includes_examples(role):
        _invalidate_style_after_examples_changed(db)
    return row


@router.patch("/files/{file_id}", response_model=FileOut)
def update_file_role(file_id: int, role: str, db: Session = Depends(get_db)):
    if role not in ("context", "example", "both"):
        raise HTTPException(400, "role must be context|example|both")
    row = db.get(UploadedFile, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    prev = row.role
    row.role = role
    db.commit()
    db.refresh(row)
    if _role_includes_examples(prev) or _role_includes_examples(role):
        _invalidate_style_after_examples_changed(db)
    return row


@router.delete("/files/{file_id}")
def delete_file(file_id: int, db: Session = Depends(get_db)):
    row = db.get(UploadedFile, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    was_example = _role_includes_examples(row.role)
    path = Path(row.stored_path)
    if path.exists():
        path.unlink()
    db.delete(row)
    db.commit()
    if was_example:
        _invalidate_style_after_examples_changed(db)
    return {"ok": True}


# --- Document library ---


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    rows = db.query(Document).order_by(Document.id.desc()).all()
    return [_document_out(row) for row in rows]


@router.get("/documents/stubs", response_model=list[DocumentOut])
def list_document_stubs(db: Session = Depends(get_db)):
    """Library docs that match pipeline stub heuristics (smoke / short placeholders)."""
    rows = db.query(Document).order_by(Document.id.desc()).all()
    return [out for row in rows if (out := _document_out(row)).is_stub]


@router.post("/documents", response_model=DocumentOut)
def create_document(body: DocumentIn, db: Session = Depends(get_db)):
    if body.role not in ("context", "example", "both"):
        raise HTTPException(400, "role must be context|example|both")
    row = Document(
        title=body.title,
        filename=body.filename,
        format=body.format,
        body_md=body.body_md,
        role=body.role,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if _role_includes_examples(body.role):
        _invalidate_style_after_examples_changed(db)
    return _document_out(row)


@router.patch("/documents/{doc_id}", response_model=DocumentOut)
def update_document_role(doc_id: int, role: str, db: Session = Depends(get_db)):
    if role not in ("context", "example", "both"):
        raise HTTPException(400, "role must be context|example|both")
    row = db.get(Document, doc_id)
    if not row:
        raise HTTPException(404, "Document not found")
    prev = row.role
    row.role = role
    db.commit()
    db.refresh(row)
    if _role_includes_examples(prev) or _role_includes_examples(role):
        _invalidate_style_after_examples_changed(db)
    return _document_out(row)


@router.post("/documents/prune-stubs", response_model=PruneStubsOut)
def prune_document_stubs(body: PruneStubsIn, db: Session = Depends(get_db)):
    """Delete selected stub docs, or all library stubs when ids is omitted/empty."""
    from app.services.context import is_stub_example

    rows = db.query(Document).order_by(Document.id.desc()).all()
    stubs = [row for row in rows if is_stub_example(row.body_md or "")]
    if body.ids is not None:
        wanted = set(body.ids)
        stubs = [row for row in stubs if row.id in wanted]
        missing = wanted - {row.id for row in stubs}
        if missing:
            # Non-stubs or unknown ids — refuse so the UI cannot wipe real docs by mistake
            raise HTTPException(
                400,
                f"ids are not detected stubs (or missing): {sorted(missing)}",
            )

    deleted_ids: list[int] = []
    any_example = False
    for row in stubs:
        did = row.id
        if _delete_document_row(db, row):
            any_example = True
        deleted_ids.append(did)
    db.commit()
    if any_example and deleted_ids:
        _invalidate_style_after_examples_changed(db)
    return PruneStubsOut(deleted_ids=deleted_ids, deleted_count=len(deleted_ids))


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    row = db.get(Document, doc_id)
    if not row:
        raise HTTPException(404, "Document not found")
    was_example = _delete_document_row(db, row)
    db.commit()
    if was_example:
        _invalidate_style_after_examples_changed(db)
    return {"ok": True}


# --- DB connections & queries ---


@router.get("/db/connections", response_model=list[DbConnectionOut])
def list_connections(db: Session = Depends(get_db)):
    return db.query(DbConnection).order_by(DbConnection.id).all()


@router.post("/db/connections", response_model=DbConnectionOut)
def create_connection(body: DbConnectionIn, db: Session = Depends(get_db)):
    if body.dialect not in ("sqlite", "postgresql", "mysql"):
        raise HTTPException(400, "dialect must be sqlite|postgresql|mysql")
    row = DbConnection(
        name=body.name,
        dialect=body.dialect,
        dsn_enc=encrypt_secret(body.dsn),
        notes=body.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/db/connections/{conn_id}")
def delete_connection(conn_id: int, db: Session = Depends(get_db)):
    row = db.get(DbConnection, conn_id)
    if not row:
        raise HTTPException(404, "Connection not found")
    db.query(SavedQuery).filter(SavedQuery.connection_id == conn_id).delete()
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/db/queries", response_model=list[SavedQueryOut])
def list_queries(db: Session = Depends(get_db)):
    return db.query(SavedQuery).order_by(SavedQuery.id).all()


@router.post("/db/queries", response_model=SavedQueryOut)
def create_query(body: SavedQueryIn, db: Session = Depends(get_db)):
    if body.purpose not in ("results", "examples", "both"):
        raise HTTPException(400, "purpose must be results|examples|both")
    if not db.get(DbConnection, body.connection_id):
        raise HTTPException(404, "Connection not found")
    row = SavedQuery(
        connection_id=body.connection_id,
        name=body.name,
        purpose=body.purpose,
        sql_text=body.sql_text,
        example_body_column=body.example_body_column,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if _role_includes_examples(body.purpose):
        _invalidate_style_after_examples_changed(db)
    return row


@router.delete("/db/queries/{query_id}")
def delete_query(query_id: int, db: Session = Depends(get_db)):
    row = db.get(SavedQuery, query_id)
    if not row:
        raise HTTPException(404, "Query not found")
    was_example = _role_includes_examples(row.purpose)
    db.delete(row)
    db.commit()
    if was_example:
        _invalidate_style_after_examples_changed(db)
    return {"ok": True}


@router.post("/db/queries/{query_id}/preview", response_model=QueryPreviewOut)
def preview_query(query_id: int, db: Session = Depends(get_db)):
    saved = db.get(SavedQuery, query_id)
    if not saved:
        raise HTTPException(404, "Query not found")
    conn = db.get(DbConnection, saved.connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    try:
        data = run_query(conn, saved.sql_text, limit=50)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return QueryPreviewOut(**data)


# --- Reports ---


@router.get("/reports", response_model=list[ReportProjectOut])
def list_reports(db: Session = Depends(get_db)):
    rows = db.query(ReportProject).order_by(ReportProject.id.desc()).all()
    return [_project_out(r) for r in rows]


@router.post("/reports", response_model=ReportProjectOut)
def create_report(body: ReportProjectIn, db: Session = Depends(get_db)):
    row = ReportProject(
        title=body.title,
        brief=body.brief,
        file_ids_json=json.dumps(body.file_ids),
        query_ids_json=json.dumps(body.query_ids),
        document_ids_json=json.dumps(body.document_ids),
        example_file_ids_json=json.dumps(body.example_file_ids),
        use_all_examples=body.use_all_examples,
        llm_profile_id=body.llm_profile_id,
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _project_out(row)


@router.get("/reports/{report_id}", response_model=ReportProjectOut)
def get_report(report_id: int, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    return _project_out(row)


@router.patch("/reports/{report_id}", response_model=ReportProjectOut)
def update_report(
    report_id: int, body: ReportProjectUpdate, db: Session = Depends(get_db)
):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    examples_touched = (
        body.file_ids is not None
        or body.query_ids is not None
        or body.document_ids is not None
        or body.example_file_ids is not None
        or body.use_all_examples is not None
    )
    # use-all ranking keys off title/brief, so brief edits can change the pack.
    ranking_touched = body.brief is not None or body.title is not None
    if body.title is not None:
        row.title = body.title
    if body.brief is not None:
        row.brief = body.brief
    if body.file_ids is not None:
        row.file_ids_json = json.dumps(body.file_ids)
    if body.query_ids is not None:
        row.query_ids_json = json.dumps(body.query_ids)
    if body.document_ids is not None:
        row.document_ids_json = json.dumps(body.document_ids)
    if body.example_file_ids is not None:
        row.example_file_ids_json = json.dumps(body.example_file_ids)
    if body.use_all_examples is not None:
        row.use_all_examples = body.use_all_examples
    if body.llm_profile_id is not None:
        row.llm_profile_id = body.llm_profile_id
    if body.body_md is not None:
        row.body_md = body.body_md
    if body.outline_md is not None:
        row.outline_md = body.outline_md
    if body.theme is not None:
        row.theme_json = theme_to_json(body.theme.model_dump())

    if examples_touched or (
        ranking_touched and bool(getattr(row, "use_all_examples", True))
    ):
        old_key = getattr(row, "style_notes_key", None) or ""
        if (getattr(row, "style_notes_md", None) or "").strip() or old_key:
            from app.services.context import build_context_pack, has_real_examples
            from app.services.style_cache import fingerprint_examples

            pack = build_context_pack(
                db,
                file_ids_json=row.file_ids_json,
                query_ids_json=row.query_ids_json,
                document_ids_json=getattr(row, "document_ids_json", None) or "[]",
                example_file_ids_json=getattr(row, "example_file_ids_json", None)
                or "[]",
                use_all_examples=bool(getattr(row, "use_all_examples", True)),
                brief=row.brief,
                title=row.title,
            )
            examples = pack["examples"]
            new_key = (
                fingerprint_examples(examples) if has_real_examples(examples) else ""
            )
            if new_key != old_key:
                _clear_project_style_notes(row)

    db.commit()
    db.refresh(row)
    return _project_out(row)


@router.post("/reports/{report_id}/theme/import", response_model=ReportProjectOut)
def import_report_theme(
    report_id: int, body: ThemeImportIn, db: Session = Depends(get_db)
):
    """Extract fonts/header/footer/logos from a .docx example into this report."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    docx_path, label = _resolve_docx_path(db, body.source, body.source_id)
    settings = get_settings()
    settings.ensure_dirs()
    assets_dir = settings.theme_assets_dir(report_id)
    try:
        theme = extract_theme_from_docx(
            docx_path, assets_dir, source_label=label
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not read theme from .docx: {exc}") from exc
    row.theme_json = theme_to_json(theme)
    db.commit()
    db.refresh(row)
    return _project_out(row)


@router.delete("/reports/{report_id}/theme", response_model=ReportProjectOut)
def clear_report_theme(report_id: int, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    row.theme_json = theme_to_json(empty_theme())
    settings = get_settings()
    assets_dir = settings.theme_assets_dir(report_id)
    if assets_dir.exists():
        shutil.rmtree(assets_dir, ignore_errors=True)
    db.commit()
    db.refresh(row)
    return _project_out(row)


@router.get("/reports/{report_id}/theme/assets/{filename}")
def get_theme_asset(report_id: int, filename: str, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    # Prevent path traversal
    safe = Path(filename).name
    if safe != filename or not safe:
        raise HTTPException(400, "Invalid asset name")
    path = get_settings().theme_assets_dir(report_id) / safe
    if not path.is_file():
        raise HTTPException(404, "Asset not found")
    media = "application/octet-stream"
    lower = safe.lower()
    if lower.endswith(".png"):
        media = "image/png"
    elif lower.endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif lower.endswith(".gif"):
        media = "image/gif"
    elif lower.endswith(".webp"):
        media = "image/webp"
    return Response(content=path.read_bytes(), media_type=media)


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    db.query(PipelineRun).filter(PipelineRun.project_id == report_id).delete()
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/reports/{report_id}/runs", response_model=list[PipelineRunOut])
def list_report_runs(
    report_id: int,
    limit: int = 20,
    errors_only: bool = False,
    db: Session = Depends(get_db),
):
    """Recent pipeline runs for a report (newest first), including error log_text."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    limit = max(1, min(limit, 100))
    q = db.query(PipelineRun).filter(PipelineRun.project_id == report_id)
    if errors_only:
        q = q.filter(PipelineRun.status == "error")
    rows = q.order_by(PipelineRun.id.desc()).limit(limit).all()
    return [PipelineRunOut.model_validate(r) for r in rows]


@router.post("/reports/{report_id}/generate", response_model=ReportProjectOut)
async def generate_report(
    report_id: int, body: GenerateIn, db: Session = Depends(get_db)
):
    """Start a background generate; poll GET report / generate-status for progress."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    stage = body.stage
    if stage not in (
        "style_notes",
        "outline",
        "draft",
        "critique",
        "revise",
        "full",
    ):
        raise HTTPException(400, "Unknown stage")
    if is_job_active(report_id):
        raise HTTPException(409, "Generation already in progress for this report")
    # Clear any ghost in-progress status left by a prior crash/restart.
    reconcile_report_job(db, report_id)
    db.refresh(row)
    prior_status = row.status or "draft"
    row.status = "queued"
    db.commit()
    if not start_generate(report_id, stage, with_critique=body.with_critique):
        row.status = prior_status if prior_status not in ACTIVE_STATUSES else "draft"
        db.commit()
        raise HTTPException(409, "Generation already in progress for this report")
    db.refresh(row)
    return _project_out(row)


@router.get("/reports/{report_id}/generate-status")
def generate_status(report_id: int, db: Session = Depends(get_db)):
    """Whether a background generate is still running for this report."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    snap = job_snapshot(report_id, db=db)
    db.refresh(row)
    return {
        "report_id": report_id,
        "status": snap["status"] if snap["status"] is not None else row.status,
        "active": snap["active"],
        "cancel_requested": snap["cancel_requested"],
    }


@router.post("/reports/{report_id}/cancel")
def cancel_generate(report_id: int, db: Session = Depends(get_db)):
    """Request cancel; aborts in-flight LLM HTTP and stops between stages."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    if request_cancel(report_id):
        db.refresh(row)
        return {
            "ok": True,
            "report_id": report_id,
            "status": row.status,
            "cancel_requested": True,
        }
    # No live task: clear orphaned in-progress status so the UI can recover.
    if row.status in ACTIVE_STATUSES:
        row.status = "cancelled"
        db.commit()
        return {
            "ok": True,
            "report_id": report_id,
            "status": row.status,
            "cancel_requested": False,
        }
    raise HTTPException(409, "No generation in progress for this report")


@router.post("/reports/{report_id}/scrub-bleed", response_model=ReportProjectOut)
def scrub_report_bleed(report_id: int, db: Session = Depends(get_db)):
    """Strip example-only phrasing from the current draft without regenerating."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    if not (row.body_md or "").strip():
        raise HTTPException(400, "Report body is empty — nothing to scrub")
    if is_job_active(report_id):
        raise HTTPException(409, "Generation in progress — cancel or wait before scrubbing")
    from app.services.pipeline import ReportPipeline

    pipeline = ReportPipeline(db, row)
    pipeline.scrub_current_draft()
    db.refresh(row)
    return _project_out(row)


@router.get("/reports/{report_id}/context-preview")
def preview_report_context(report_id: int, db: Session = Depends(get_db)):
    """Dry-run the context pack (no LLM): lengths, fingerprint, cache hit."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    from app.services.context import build_context_pack, has_real_examples
    from app.services.style_cache import fingerprint_examples, load_style_notes

    pack = build_context_pack(
        db,
        file_ids_json=row.file_ids_json,
        query_ids_json=row.query_ids_json,
        document_ids_json=getattr(row, "document_ids_json", None) or "[]",
        example_file_ids_json=getattr(row, "example_file_ids_json", None) or "[]",
        use_all_examples=bool(getattr(row, "use_all_examples", True)),
        brief=row.brief,
        title=row.title,
    )
    examples = pack["examples"]
    key = fingerprint_examples(examples) if has_real_examples(examples) else ""
    settings = get_settings()
    cached = load_style_notes(settings.style_cache_dir, key) if key else None
    project_key = getattr(row, "style_notes_key", None) or ""
    on_project = bool((getattr(row, "style_notes_md", None) or "").strip())
    example_block_count = 0
    if has_real_examples(examples):
        example_block_count = sum(
            1 for line in examples.splitlines() if line.startswith("### ")
        )
    return {
        "title": pack["title"],
        "brief_chars": len(pack["brief"] or ""),
        "results_chars": len(pack["results_context"] or ""),
        "examples_chars": len(examples or ""),
        "example_blocks": example_block_count,
        "has_examples": has_real_examples(examples),
        "style_notes_key": key,
        "project_style_notes_key": project_key,
        "style_notes_cached": bool(cached),
        "style_notes_on_project": on_project,
        "style_notes_stale": on_project and (not key or project_key != key),
        "results_preview": (pack["results_context"] or "")[:1200],
        "examples_preview": (examples or "")[:1200],
    }


@router.post("/reports/{report_id}/done")
def mark_report_done(
    report_id: int,
    body: MarkDoneIn | None = None,
    db: Session = Depends(get_db),
):
    """Mark finished and copy into the document library for reuse as an example."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    if not (row.body_md or "").strip():
        raise HTTPException(400, "Report body is empty — generate or write content first")
    role = (body.role if body else "example") or "example"
    if role not in ("context", "example", "both"):
        raise HTTPException(400, "role must be context|example|both")
    row.status = "done"
    db.commit()
    doc = _archive_report_to_library(db, row, role=role)
    db.refresh(row)
    return {"report": _project_out(row), "document": _document_out(doc)}


@router.get("/reports/{report_id}/export.md")
def export_md(report_id: int, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    settings = get_settings()
    out = settings.reports_dir / f"report_{report_id}.md"
    out.write_text(row.body_md or "", encoding="utf-8")
    return PlainTextResponse(
        row.body_md or "",
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.md"'},
    )


@router.get("/reports/{report_id}/export.html")
def export_html(report_id: int, db: Session = Depends(get_db)):
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    settings = get_settings()
    theme = parse_theme_json(getattr(row, "theme_json", None) or "{}")
    assets_dir = settings.theme_assets_dir(report_id)
    html_doc = render_html_export(
        row.title or "Report",
        row.body_md or "",
        theme=theme,
        theme_assets_dir=assets_dir,
    )
    out = settings.reports_dir / f"report_{report_id}.html"
    out.write_text(html_doc, encoding="utf-8")
    return HTMLResponse(
        html_doc,
        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.html"'},
    )


@router.get("/reports/{report_id}/export.docx")
def export_docx(report_id: int, db: Session = Depends(get_db)):
    """Export report body as Word .docx (legacy .doc is not supported)."""
    row = db.get(ReportProject, report_id)
    if not row:
        raise HTTPException(404, "Report not found")
    settings = get_settings()
    settings.ensure_dirs()
    theme = parse_theme_json(getattr(row, "theme_json", None) or "{}")
    assets_dir = settings.theme_assets_dir(report_id)
    data = markdown_to_docx_bytes(
        row.title,
        row.body_md or "",
        theme=theme,
        theme_assets_dir=assets_dir,
    )
    out = settings.reports_dir / f"report_{report_id}.docx"
    out.write_bytes(data)
    filename = f"report_{report_id}.docx"
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
