"""Gather file + DB + document-library context for the report pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import DbConnection, Document, SavedQuery, UploadedFile

from .db_connector import query_as_markdown
from .docs import read_upload_text

NO_EXAMPLES_PLACEHOLDER = "_(no example reports attached)_"
NO_RESULTS_PLACEHOLDER = "_(no results context attached)_"


def has_real_examples(examples: str) -> bool:
    """True when the pack includes actual example text (not the empty placeholder)."""
    text = (examples or "").strip()
    return bool(text) and text != NO_EXAMPLES_PLACEHOLDER


def _as_context_block(title: str, body: str) -> str:
    return f"### {title}\n\n{body}\n"


def _is_stub_example(body: str) -> bool:
    """True for tiny placeholder examples that dilute style extraction."""
    text = (body or "").strip()
    if len(text) < 80:
        return True
    lowered = text.lower()
    if lowered.startswith("# smoke") and len(text) < 400:
        return True
    if lowered.startswith("sample prior report") and len(text) < 400:
        return True
    # Theme extracts / fragments without report section headings
    if len(text) < 220 and "## " not in text and not text.lstrip().startswith("# "):
        return True
    return False


def _split_query_ids_by_purpose(
    db: Session, query_ids: Iterable[int]
) -> tuple[list[int], list[int]]:
    """Split selected queries into (results_ids, example_ids) by saved purpose.

    purpose=examples must never land in results context (those bodies are style
    references, not facts for the new report). purpose=both contributes to both.
    """
    results_ids: list[int] = []
    example_ids: list[int] = []
    for qid in query_ids:
        saved = db.get(SavedQuery, qid)
        if not saved:
            continue
        purpose = (saved.purpose or "results").lower()
        if purpose == "examples":
            example_ids.append(qid)
        elif purpose == "both":
            results_ids.append(qid)
            example_ids.append(qid)
        else:
            results_ids.append(qid)
    return results_ids, example_ids


def gather_files_as(
    db: Session,
    file_ids: Iterable[int],
    *,
    as_role: str,
) -> str:
    """Force selected uploads into context or examples regardless of stored role."""
    parts: list[str] = []
    for fid in file_ids:
        row = db.get(UploadedFile, fid)
        if not row:
            continue
        text = read_upload_text(Path(row.stored_path))
        if as_role == "example" and _is_stub_example(text):
            continue
        parts.append(
            _as_context_block(
                f"File: {row.original_name} (used as {as_role})",
                text,
            )
        )
    return "\n".join(parts)


def gather_documents_as(
    db: Session,
    document_ids: Iterable[int],
    *,
    as_role: str,
) -> str:
    parts: list[str] = []
    for did in document_ids:
        row = db.get(Document, did)
        if not row:
            continue
        if as_role == "example" and _is_stub_example(row.body_md or ""):
            continue
        meta = f"format={row.format}"
        if row.filename:
            meta += f", file={row.filename}"
        parts.append(
            _as_context_block(
                f"Library document: {row.title} ({meta}, used as {as_role})",
                row.body_md,
            )
        )
    return "\n".join(parts)


def gather_queries_as(
    db: Session,
    query_ids: Iterable[int],
    *,
    as_role: str,
) -> str:
    parts: list[str] = []
    for qid in query_ids:
        saved = db.get(SavedQuery, qid)
        if not saved:
            continue
        conn = db.get(DbConnection, saved.connection_id)
        if not conn:
            continue
        try:
            # For examples, prefer body-column formatting when configured
            if as_role == "example" and not saved.example_body_column:
                # still run as markdown table / rows
                pass
            md = query_as_markdown(conn, saved)
        except Exception as exc:  # noqa: BLE001
            md = f"### Query: {saved.name}\n\n_Error running query: {exc}_"
        parts.append(md if md.startswith("###") else _as_context_block(f"Query ({as_role})", md))
    return "\n".join(parts)


def all_example_source_ids(db: Session) -> tuple[list[int], list[int], list[int]]:
    """Return (file_ids, document_ids, query_ids) for everything tagged as examples."""
    files = [
        r.id
        for r in db.query(UploadedFile).order_by(UploadedFile.id).all()
        if r.role in ("example", "both")
    ]
    docs = [
        r.id
        for r in db.query(Document).order_by(Document.id).all()
        if r.role in ("example", "both")
    ]
    queries = [
        r.id
        for r in db.query(SavedQuery).order_by(SavedQuery.id).all()
        if r.purpose in ("examples", "both")
    ]
    return files, docs, queries


def build_context_pack(
    db: Session,
    *,
    file_ids_json: str,
    query_ids_json: str,
    document_ids_json: str = "[]",
    example_file_ids_json: str = "[]",
    use_all_examples: bool = True,
    brief: str,
    title: str,
) -> dict[str, str]:
    context_file_ids = json.loads(file_ids_json or "[]")
    selected_query_ids = json.loads(query_ids_json or "[]")
    results_query_ids, selected_example_queries = _split_query_ids_by_purpose(
        db, selected_query_ids
    )

    if use_all_examples:
        ex_files, ex_docs, ex_queries = all_example_source_ids(db)
    else:
        ex_files = json.loads(example_file_ids_json or "[]")
        ex_docs = json.loads(document_ids_json or "[]")
        ex_queries = list(selected_example_queries)

    file_ctx = gather_files_as(db, context_file_ids, as_role="context")
    q_ctx = gather_queries_as(db, results_query_ids, as_role="context")
    # Context-tagged library docs are not selected separately in the UI yet;
    # document_ids are examples when not using "all".
    results = "\n\n".join(p for p in (file_ctx, q_ctx) if p).strip()

    file_ex = gather_files_as(db, ex_files, as_role="example")
    doc_ex = gather_documents_as(db, ex_docs, as_role="example")
    q_ex = gather_queries_as(db, ex_queries, as_role="example")
    # Longer / richer examples first so style extraction is not dominated by
    # short demo library docs when prior reports are also attached.
    example_blocks = [p for p in (file_ex, doc_ex, q_ex) if p]
    example_blocks.sort(key=len, reverse=True)
    examples = "\n\n".join(example_blocks).strip()

    return {
        "title": title,
        "brief": brief.strip(),
        "results_context": results or NO_RESULTS_PLACEHOLDER,
        "examples": examples or NO_EXAMPLES_PLACEHOLDER,
    }
