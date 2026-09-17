"""In-process background generate jobs (one active task per report)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from app.db.session import get_engine
from app.models import ReportProject
from app.services.pipeline import GenerationCancelled, ReportPipeline
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_SessionLocal: Optional[sessionmaker] = None
_jobs: dict[int, asyncio.Task] = {}
_cancel_flags: set[int] = set()
_job_started: dict[int, float] = {}

# In-memory jobs cannot outlive the process; DB rows left mid-stage are stale.
# Also used to drop hung tasks that never finish (no progress / stuck LLM).
HUNG_JOB_TTL_SECONDS = 6 * 60 * 60

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
    "style_notes": frozenset(
        {"ready", "draft", "outlined", "drafted", "critiqued", "done", "error", "cancelled"}
    ),
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


def _prune_finished_jobs() -> None:
    """Drop completed tasks so cancel/start/status cannot see orphans."""
    finished = [rid for rid, task in _jobs.items() if task.done()]
    for rid in finished:
        _jobs.pop(rid, None)
        _job_started.pop(rid, None)
        _cancel_flags.discard(rid)


def is_job_active(report_id: int) -> bool:
    _prune_finished_jobs()
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


def _terminal_for_orphan(*, wanted_cancel: bool) -> str:
    return "cancelled" if wanted_cancel else "error"


def _mark_row_terminal(
    db: Session,
    row: ReportProject,
    *,
    terminal: str,
    reason: str,
) -> bool:
    """Force an in-progress row to a terminal status. Returns True if changed."""
    if row.status not in ACTIVE_STATUSES:
        return False
    prior = row.status
    row.status = terminal
    db.commit()
    logger.info(
        "generate job cleanup: report %s %s -> %s (%s)",
        row.id,
        prior,
        terminal,
        reason,
    )
    return True


def reconcile_report_job(db: Session, report_id: int) -> Optional[str]:
    """
    If DB says in-progress but no live task (or the task is hung past TTL),
    mark the row terminal so polling stays accurate.
    Returns the status after reconcile (or None if report missing).
    """
    _prune_finished_jobs()
    row = db.get(ReportProject, report_id)
    if not row:
        return None

    wanted_cancel = cancel_requested(report_id)
    active = is_job_active(report_id)

    if active:
        started = _job_started.get(report_id)
        if started is not None and (time.monotonic() - started) > HUNG_JOB_TTL_SECONDS:
            logger.warning(
                "generate job hung past TTL for report %s; requesting cancel",
                report_id,
            )
            _cancel_flags.add(report_id)
        return row.status

    if row.status in ACTIVE_STATUSES:
        terminal = _terminal_for_orphan(wanted_cancel=wanted_cancel)
        _mark_row_terminal(
            db,
            row,
            terminal=terminal,
            reason="no live background task",
        )
        _cancel_flags.discard(report_id)
        db.refresh(row)
    return row.status


def sweep_stale_jobs_on_startup() -> int:
    """
    After process start, no in-memory tasks exist. Mark any leftover in-progress
    DB statuses terminal so UI/polling cannot show a ghost generate.
    """
    db: Session = _session_factory()()
    cleared = 0
    try:
        rows = (
            db.query(ReportProject)
            .filter(ReportProject.status.in_(tuple(ACTIVE_STATUSES)))
            .all()
        )
        for row in rows:
            if _mark_row_terminal(
                db,
                row,
                terminal="error",
                reason="server restart / startup sweep",
            ):
                cleared += 1
        if cleared:
            logger.info("startup generate sweep cleared %s stale job(s)", cleared)
        return cleared
    finally:
        db.close()
        _jobs.clear()
        _cancel_flags.clear()
        _job_started.clear()


def _finalize_job_row(db: Session, report_id: int) -> None:
    """Ensure the report is not left in an in-progress status after the task ends."""
    try:
        row = db.get(ReportProject, report_id)
        if not row:
            return
        db.refresh(row)
        if row.status not in ACTIVE_STATUSES:
            return
        terminal = _terminal_for_orphan(wanted_cancel=cancel_requested(report_id))
        _mark_row_terminal(
            db,
            row,
            terminal=terminal,
            reason="job exited while status still in-progress",
        )
    except Exception:  # noqa: BLE001
        logger.exception("generate job finalize failed for report %s", report_id)


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
        except asyncio.CancelledError:
            logger.info("generate job task cancelled for report %s", report_id)
            db.refresh(row)
            if row.status in ACTIVE_STATUSES:
                row.status = "cancelled"
                db.commit()
            raise
        except Exception:  # noqa: BLE001
            logger.exception("generate job failed for report %s", report_id)
            db.refresh(row)
            if row.status != "error":
                row.status = "error"
                db.commit()
    finally:
        try:
            _finalize_job_row(db, report_id)
        finally:
            db.close()
            _jobs.pop(report_id, None)
            _job_started.pop(report_id, None)
            _cancel_flags.discard(report_id)


def start_generate(report_id: int, stage: str, with_critique: bool = True) -> bool:
    """Schedule a background generate. Returns False if one is already running."""
    if is_job_active(report_id):
        return False
    _cancel_flags.discard(report_id)
    _job_started[report_id] = time.monotonic()
    task = asyncio.create_task(
        _run_job(report_id, stage, with_critique),
        name=f"ofc-generate-{report_id}-{stage}",
    )
    _jobs[report_id] = task
    return True


def job_snapshot(report_id: int, db: Optional[Session] = None) -> dict:
    """
    Live job view for polling. When ``db`` is provided, orphaned in-progress
    statuses are reconciled before reading.
    """
    status: Optional[str] = None
    if db is not None:
        status = reconcile_report_job(db, report_id)
    return {
        "active": is_job_active(report_id),
        "cancel_requested": cancel_requested(report_id),
        "status": status,
    }


# Re-export for callers that want a typed cancel check factory
CancelCheck = Callable[[], bool]
