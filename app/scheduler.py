"""Background scheduler: runs each enabled target's check at its own interval."""
import logging
import threading
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

# Serialises changes to the schedule. Reentrant because start() calls sync_target().
_lock = threading.RLock()


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
            run_check_and_store(db, target, require_enabled=True)
    except Exception:
        logger.exception("Scheduled check failed for target %s", target_id)


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def sync_target(target_id: int) -> None:
    """Make the schedule match the target as it is stored in the database right now.

    The latest committed state is read here, under a lock, instead of trusting the
    caller's copy. Two edits made at the same moment can therefore never leave the
    schedule out of step with the database, whatever order they reach this function.

    A target is scheduled only if it exists, is enabled and has a supported check
    type. Does nothing when the scheduler is not running (for example in tests).
    """
    with _lock:
        if not is_running():
            return
        with database.SessionLocal() as db:
            target = db.get(Target, target_id)
            if target is None or not target.enabled or target.check_type not in SUPPORTED_CHECK_TYPES:
                remove_target(target_id)
                return
            interval_seconds = target.interval_seconds
        _scheduler.add_job(
            run_scheduled_check,
            trigger=IntervalTrigger(seconds=interval_seconds, timezone=timezone.utc),
            args=[target_id],
            id=_job_id(target_id),
            replace_existing=True,
            max_instances=1,      # never run two checks of the same target at once
            coalesce=True,        # if runs were missed, run once rather than in a burst
            misfire_grace_time=30,
            next_run_time=datetime.now(timezone.utc),  # first check starts immediately
        )


def remove_target(target_id: int) -> None:
    """Stop checking a target (no error if it has no job)."""
    with _lock:
        if not is_running():
            return
        job = _scheduler.get_job(_job_id(target_id))
        if job is not None:
            job.remove()


def start() -> None:
    """Start the scheduler and schedule every enabled target stored in the database."""
    global _scheduler
    with _lock:
        if is_running():
            return
        _scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(MAX_PARALLEL_CHECKS)},
            timezone=timezone.utc,
        )
        _scheduler.start()
        with database.SessionLocal() as db:
            enabled_ids = [row.id for row in db.query(Target.id).filter(Target.enabled.is_(True))]
        for target_id in enabled_ids:
            sync_target(target_id)
        logger.info("Scheduler started with %d target(s)", len(_scheduler.get_jobs()))


def shutdown() -> None:
    """Stop the scheduler without waiting for running checks."""
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None
