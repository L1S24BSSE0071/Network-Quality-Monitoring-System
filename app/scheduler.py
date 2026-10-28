"""Background scheduler: runs each enabled target's check at its own interval."""
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import database
from app.checker import SUPPORTED_CHECK_TYPES, run_check_and_store
from app.models import Target

logger = logging.getLogger("nqms.scheduler")

MAX_PARALLEL_CHECKS = 20

_scheduler: Optional[BackgroundScheduler] = None


def _job_id(target_id: int) -> str:
    return f"target-{target_id}"


def run_scheduled_check(target_id: int) -> None:
    """Job body: check one target and store the result.

    Uses its own database session, reloads the target so edits apply on the
    next run, and never raises, so one failing target cannot affect others.
    """
    try:
        with database.SessionLocal() as db:
            target = db.get(Target, target_id)
            if target is None or not target.enabled:
                # Deleted or disabled since the job was scheduled: stop checking it.
                remove_target(target_id)
                return
            run_check_and_store(db, target)
    except Exception:
        logger.exception("Scheduled check failed for target %s", target_id)


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def sync_target(target: Target) -> None:
    """Make the schedule match the target: add or update its job, or remove it.

    A target is scheduled only if it is enabled and its check type is supported.
    Does nothing when the scheduler is not running (for example in tests).
    """
    if not is_running():
        return
    if not target.enabled or target.check_type not in SUPPORTED_CHECK_TYPES:
        remove_target(target.id)
        return
    _scheduler.add_job(
        run_scheduled_check,
        trigger=IntervalTrigger(seconds=target.interval_seconds, timezone=timezone.utc),
        args=[target.id],
        id=_job_id(target.id),
        replace_existing=True,
        max_instances=1,      # never run two checks of the same target at once
        coalesce=True,        # if runs were missed, run once rather than in a burst
        misfire_grace_time=30,
        next_run_time=datetime.now(timezone.utc),  # first check starts immediately
    )


def remove_target(target_id: int) -> None:
    """Stop checking a target (no error if it has no job)."""
    if not is_running():
        return
    job = _scheduler.get_job(_job_id(target_id))
    if job is not None:
        job.remove()


def start() -> None:
    """Start the scheduler and schedule every enabled target stored in the database."""
    global _scheduler
    if is_running():
        return
    _scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(MAX_PARALLEL_CHECKS)},
        timezone=timezone.utc,
    )
    _scheduler.start()
    with database.SessionLocal() as db:
        for target in db.query(Target).filter(Target.enabled.is_(True)).all():
            sync_target(target)
    logger.info("Scheduler started with %d target(s)", len(_scheduler.get_jobs()))


def shutdown() -> None:
    """Stop the scheduler without waiting for running checks."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
