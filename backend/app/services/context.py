"""Gather file + DB + document-library context for the report pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import DbConnection, Document, SavedQuery, UploadedFile

from .db_connector import query_as_markdown
from .docs import read_upload_text

NO_EXAMPLES_PLACEHOLDER = "_(no example reports attached)_"
NO_RESULTS_PLACEHOLDER = "_(no results context attached)_"

# Soft caps when use_all_examples pulls a large library (<1000-doc regime).
_MAX_RANKED_EXAMPLES = 8
_MAX_EXAMPLE_CHARS = 24_000

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "have",
        "has",
        "was",
        "were",
        "are",
        "been",
        "will",
        "shall",
        "should",
        "would",
        "could",
        "about",
        "over",
        "under",
        "than",
        "then",
        "also",
        "only",
        "such",
        "into",
        "using",
        "used",
        "use",
        "per",
        "via",
        "not",
        "any",
        "all",
        "our",
        "their",
        "they",
        "them",
        "its",
        "report",
        "reports",
        "write",
        "cover",
        "match",
        "prior",
        "attached",
        "summary",
        "section",
        "sections",
        "document",
        "library",
        "example",
        "examples",
        "query",
        "file",
        "format",
        "markdown",
        "none",
        "null",
    }
)


def has_real_examples(examples: str) -> bool:
    """True when the pack includes actual example text (not the empty placeholder)."""
    text = (examples or "").strip()
    return bool(text) and text != NO_EXAMPLES_PLACEHOLDER


def _as_context_block(title: str, body: str) -> str:
    return f"### {title}\n\n{body}\n"


def stub_example_reason(body: str) -> str | None:
    """Why a library body looks like a stub, or None if it seems real.

    Same heuristics the pipeline uses to skip contaminated example text
    (smoke/# Smoke shorts, tiny placeholders, heading-less fragments).
    """
    text = (body or "").strip()
    if len(text) < 80:
        return "very short (<80 chars)"
    lowered = text.lower()
    if lowered.startswith("# smoke") and len(text) < 400:
        return "smoke / short stub (# smoke…)"
    if lowered.startswith("sample prior report") and len(text) < 400:
        return "sample prior report placeholder"
    # Theme extracts / fragments without report section headings
    if len(text) < 220 and "## " not in text and not text.lstrip().startswith("# "):
        return "short fragment without section headings"
    return None


def is_stub_example(body: str) -> bool:
    """True for tiny placeholder examples that dilute style extraction."""
    return stub_example_reason(body) is not None


# Backward-compatible private alias used by older call sites / tests.
_is_stub_example = is_stub_example


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def example_similarity(query: str, example: str) -> float:
    """Cheap lexical overlap of query terms covered by an example body (0..1)."""
    q = _tokens(query)
    if not q:
        return 0.0
    e = _tokens(example)
    if not e:
        return 0.0
    return len(q & e) / len(q)


def rank_example_blocks(
    blocks: list[str],
    *,
    query: str,
    cap: bool,
    max_blocks: int = _MAX_RANKED_EXAMPLES,
    max_chars: int = _MAX_EXAMPLE_CHARS,
) -> list[str]:
    """Order examples by brief/title similarity; optionally keep a top budget.

    Prefer retrieval ranking over model fine-tuning for small corpora.
    Length remains a tie-breaker so short stubs lose to richer matches.
    """
    if not blocks:
        return []
    scored = sorted(
        (
            (example_similarity(query, block), len(block), i, block)
            for i, block in enumerate(blocks)
        ),
        key=lambda row: (row[0], row[1]),
        reverse=True,
    )
    ordered = [row[3] for row in scored]
    if not cap:
        return ordered
    kept: list[str] = []
    total = 0
    for block in ordered:
        if len(kept) >= max_blocks:
            break
        extra = len(block) + (2 if kept else 0)
        if kept and total + extra > max_chars:
            break
        kept.append(block)
        total += extra
    return kept or ordered[:1]


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
) -> list[str]:
    """Force selected uploads into context or examples regardless of stored role."""
    parts: list[str] = []
    for fid in file_ids:
        row = db.get(UploadedFile, fid)
        if not row:
            continue
        text = read_upload_text(Path(row.stored_path))
        if as_role == "example" and is_stub_example(text):
            continue
        parts.append(
            _as_context_block(
                f"File: {row.original_name} (used as {as_role})",
                text,
            )
        )
    return parts


def gather_documents_as(
    db: Session,
    document_ids: Iterable[int],
    *,
    as_role: str,
) -> list[str]:
    parts: list[str] = []
    for did in document_ids:
        row = db.get(Document, did)
        if not row:
            continue
        if as_role == "example" and is_stub_example(row.body_md or ""):
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
    return parts


def gather_queries_as(
    db: Session,
    query_ids: Iterable[int],
    *,
    as_role: str,
) -> list[str]:
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
    return parts


def _library_example_filenames(db: Session, document_ids: list[int] | None = None) -> set[str]:
    """Lowercased filenames for library docs (optionally limited to ids)."""
    q = db.query(Document)
    if document_ids is not None:
        if not document_ids:
            return set()
        q = q.filter(Document.id.in_(document_ids))
    names: set[str] = set()
    for row in q.all():
        name = (row.filename or "").strip().lower()
        if name:
            names.add(name)
    return names


def dedupe_example_file_ids(
    db: Session, file_ids: list[int], document_ids: list[int]
) -> list[int]:
    """Prefer library docs over uploads that mirror the same filename."""
    library_names = _library_example_filenames(db, document_ids)
    if not library_names:
        return list(file_ids)
    kept: list[int] = []
    for fid in file_ids:
        row = db.get(UploadedFile, fid)
        if not row:
            continue
        name = (row.original_name or "").strip().lower()
        if name and name in library_names:
            continue
        kept.append(fid)
    return kept


def all_example_source_ids(db: Session) -> tuple[list[int], list[int], list[int]]:
    """Return (file_ids, document_ids, query_ids) for everything tagged as examples."""
    docs = [
        r.id
        for r in db.query(Document).order_by(Document.id).all()
        if r.role in ("example", "both")
    ]
    library_names = _library_example_filenames(db, docs)
    files = [
        r.id
        for r in db.query(UploadedFile).order_by(UploadedFile.id).all()
        if r.role in ("example", "both")
        and (r.original_name or "").strip().lower() not in library_names
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
        ex_files = dedupe_example_file_ids(db, ex_files, ex_docs)

    file_ctx = gather_files_as(db, context_file_ids, as_role="context")
    q_ctx = gather_queries_as(db, results_query_ids, as_role="context")
    # Context-tagged library docs are not selected separately in the UI yet;
    # document_ids are examples when not using "all".
    results = "\n\n".join(p for p in (*file_ctx, *q_ctx) if p).strip()

    example_blocks = [
        *gather_files_as(db, ex_files, as_role="example"),
        *gather_documents_as(db, ex_docs, as_role="example"),
        *gather_queries_as(db, ex_queries, as_role="example"),
    ]
    query = f"{title}\n{brief}".strip()
    ranked = rank_example_blocks(
        example_blocks,
        query=query,
        # Cap only the "use all" path so deliberate picks stay complete.
        cap=use_all_examples,
    )
    examples = "\n\n".join(ranked).strip()

    return {
        "title": title,
        "brief": brief.strip(),
        "results_context": results or NO_RESULTS_PLACEHOLDER,
        "examples": examples or NO_EXAMPLES_PLACEHOLDER,
    }
