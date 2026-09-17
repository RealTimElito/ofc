"""In-process background generate jobs (one active task per report)."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from app.db.session import get_engine
from app.models import ReportProject
from app.services.pipeline import GenerationCancelled, ReportPipeline
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_SessionLocal: Optional[sessionmaker] = None
_jobs: dict[int, asyncio.Task] = {}
_cancel_flags: set[int] = set()

ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "style_notes",
        "outlining",
        "drafting",
        "critiquing",
        "revising",
    }
)

TERMINAL_BY_STAGE: dict[str, frozenset[str]] = {
    "style_notes": frozenset({"ready", "draft", "outlined", "drafted", "critiqued", "done", "error", "cancelled"}),
    "outline": frozenset({"outlined", "error", "cancelled"}),
    "draft": frozenset({"drafted", "error", "cancelled"}),
    "critique": frozenset({"critiqued", "error", "cancelled"}),
    "revise": frozenset({"ready", "error", "cancelled"}),
    "full": frozenset({"ready", "error", "cancelled"}),
}


def _session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False
        )
    return _SessionLocal


def is_job_active(report_id: int) -> bool:
    task = _jobs.get(report_id)
    return task is not None and not task.done()


def request_cancel(report_id: int) -> bool:
    """Ask a running job to stop ASAP (aborts in-flight LLM HTTP). Returns False if none active."""
    if not is_job_active(report_id):
        return False
    _cancel_flags.add(report_id)
    return True


def cancel_requested(report_id: int) -> bool:
    return report_id in _cancel_flags


def is_terminal_status(stage: str, status: str) -> bool:
    terminals = TERMINAL_BY_STAGE.get(stage, TERMINAL_BY_STAGE["full"])
    return status in terminals


async def _run_job(report_id: int, stage: str, with_critique: bool) -> None:
    db: Session = _session_factory()()
    try:
        row = db.get(ReportProject, report_id)
        if not row:
            logger.warning("generate job: report %s missing", report_id)
            return

        def _check() -> bool:
            return cancel_requested(report_id)

        pipe = ReportPipeline(db, row, cancel_check=_check)
        try:
            if stage == "style_notes":
                await pipe.run_style_notes(force=True)
            elif stage == "outline":
                await pipe.run_outline()
            elif stage == "draft":
                await pipe.run_draft()
            elif stage == "critique":
                await pipe.run_critique()
            elif stage == "revise":
                await pipe.run_revise()
            elif stage == "full":
                await pipe.run_full(with_critique=with_critique)
            else:
                row.status = "error"
                db.commit()
                logger.error("generate job: unknown stage %r", stage)
        except GenerationCancelled:
            logger.info("generate job cancelled for report %s", report_id)
        except Exception:  # noqa: BLE001
            logger.exception("generate job failed for report %s", report_id)
            db.refresh(row)
            if row.status != "error":
                row.status = "error"
                db.commit()
    finally:
        db.close()
        _jobs.pop(report_id, None)
        _cancel_flags.discard(report_id)


def start_generate(report_id: int, stage: str, with_critique: bool = True) -> bool:
    """Schedule a background generate. Returns False if one is already running."""
    if is_job_active(report_id):
        return False
    _cancel_flags.discard(report_id)
    task = asyncio.create_task(
        _run_job(report_id, stage, with_critique),
        name=f"ofc-generate-{report_id}-{stage}",
    )
    _jobs[report_id] = task
    return True


def job_snapshot(report_id: int) -> dict:
    return {
        "active": is_job_active(report_id),
        "cancel_requested": cancel_requested(report_id),
    }


# Re-export for callers that want a typed cancel check factory
CancelCheck = Callable[[], bool]
