"""API endpoints for managing monitoring targets, check history, events and summaries."""
from datetime import timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import bindparam, case, delete, func, select
from sqlalchemy.orm import Session

from app import scheduler
from app.checker import SUPPORTED_CHECK_TYPES, run_check_and_store
from app.database import get_db
from app.evaluator import _normalize_latency, calculate_uptime_pct, evaluate_state
from app.models import CheckResult, Event, Target, utc_now
from app.schemas import (
    CheckResultRead,
    EventRead,
    EventSeverity,
    TargetCreate,
    TargetRead,
    TargetStatus,
    TargetSummary,
    TargetUpdate,
    validate_host_or_url,
    validate_thresholds,
)

router = APIRouter(prefix="/targets", tags=["targets"])
events_router = APIRouter(prefix="/events", tags=["events"])

# Ids are 32-bit positive integers; anything else is rejected before reaching SQLite,
# which cannot store integers above 64 bits.
TargetId = Annotated[int, Path(ge=1, le=2_147_483_647, description="Target id")]
HoursWindow = Annotated[
    Optional[int],
    Query(ge=1, le=720, description="Optional time window in hours (1-720)."),
]

# Changing any of these changes what or when the scheduler checks. Other edits (name,
# thresholds) are picked up by the next scheduled check without triggering a new one.
_SCHEDULE_FIELDS = {"host_or_url", "check_type", "interval_seconds", "enabled"}

_LIST_TARGETS_QUERY = select(Target).order_by(Target.id)
_MANUAL_CHECK_TARGET_QUERY = select(Target.host_or_url, Target.check_type).where(
    Target.id == bindparam("target_id")
)
_TARGET_EXISTS_QUERY = select(Target.id).where(Target.id == bindparam("target_id"))

_LIST_RESULTS_QUERY = (
    select(CheckResult)
    .where(CheckResult.target_id == bindparam("target_id"))
    .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
    .limit(bindparam("limit"))
)
_LIST_RESULTS_SINCE_QUERY = (
    select(CheckResult)
    .where(
        CheckResult.target_id == bindparam("target_id"),
        CheckResult.checked_at >= bindparam("since"),
    )
    .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
    .limit(bindparam("limit"))
)

_LIST_TARGET_EVENTS_QUERY = (
    select(Event)
    .where(Event.target_id == bindparam("target_id"))
    .order_by(Event.occurred_at.desc(), Event.id.desc())
    .limit(bindparam("limit"))
)
_LIST_TARGET_EVENTS_BY_SEVERITY_QUERY = (
    select(Event)
    .where(
        Event.target_id == bindparam("target_id"),
        Event.severity == bindparam("severity"),
    )
    .order_by(Event.occurred_at.desc(), Event.id.desc())
    .limit(bindparam("limit"))
)

_LIST_ALL_EVENTS_QUERY = (
    select(Event)
    .order_by(Event.occurred_at.desc(), Event.id.desc())
    .limit(bindparam("limit"))
)
_LIST_ALL_EVENTS_BY_SEVERITY_QUERY = (
    select(Event)
    .where(Event.severity == bindparam("severity"))
    .order_by(Event.occurred_at.desc(), Event.id.desc())
    .limit(bindparam("limit"))
)

_LATEST_RESULT_ID_SUBQ = (
    select(CheckResult.id)
    .where(CheckResult.target_id == Target.id)
    .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
    .limit(1)
    .correlate(Target)
    .scalar_subquery()
)

_TARGET_WITH_LATEST_COLUMNS = (
    Target.id,
    Target.name,
    Target.host_or_url,
    Target.check_type,
    Target.interval_seconds,
    Target.warning_ms,
    Target.critical_ms,
    Target.enabled,
    CheckResult.checked_at,
    CheckResult.is_up,
    CheckResult.latency_ms,
    CheckResult.http_status,
    CheckResult.error_message,
)

_ALL_TARGETS_WITH_LATEST_QUERY = (
    select(*_TARGET_WITH_LATEST_COLUMNS)
    .select_from(Target)
    .outerjoin(CheckResult, CheckResult.id == _LATEST_RESULT_ID_SUBQ)
    .order_by(Target.id)
)
_ONE_TARGET_WITH_LATEST_QUERY = (
    select(*_TARGET_WITH_LATEST_COLUMNS)
    .select_from(Target)
    .outerjoin(CheckResult, CheckResult.id == _LATEST_RESULT_ID_SUBQ)
    .where(Target.id == bindparam("target_id"))
)

_UP_CASE = case((CheckResult.is_up.is_(True), 1), else_=0)

_AGG_ALL_QUERY = select(
    CheckResult.target_id,
    func.count(CheckResult.id),
    func.coalesce(func.sum(_UP_CASE), 0),
    func.avg(CheckResult.latency_ms),
).group_by(CheckResult.target_id)
_AGG_ALL_SINCE_QUERY = _AGG_ALL_QUERY.where(
    CheckResult.target_id.in_(select(Target.id)),
    CheckResult.checked_at >= bindparam("since"),
)

_AGG_ONE_QUERY = select(
    func.count(CheckResult.id),
    func.coalesce(func.sum(_UP_CASE), 0),
    func.avg(CheckResult.latency_ms),
).where(CheckResult.target_id == bindparam("target_id"))
_AGG_ONE_SINCE_QUERY = _AGG_ONE_QUERY.where(CheckResult.checked_at >= bindparam("since"))

_DELETE_RESULTS_STMT = delete(CheckResult).where(CheckResult.target_id == bindparam("target_id"))
_DELETE_EVENTS_STMT = delete(Event).where(Event.target_id == bindparam("target_id"))
_DELETE_TARGET_STMT = delete(Target).where(Target.id == bindparam("target_id"))


def _get_target_or_404(target_id: int, db: Session) -> Target:
    target = db.get(Target, target_id)
    if target is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")
    return target


def _ensure_target_exists_or_404(target_id: int, db: Session) -> None:
    if db.scalar(_TARGET_EXISTS_QUERY, {"target_id": target_id}) is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")


def _build_summary(
    row: tuple,
    total_checks: int,
    up_checks: int,
    avg_latency_raw: Optional[float],
) -> TargetSummary:
    (
        target_id,
        name,
        host_or_url,
        check_type,
        interval_seconds,
        warning_ms,
        critical_ms,
        enabled,
        last_checked_at,
        last_is_up,
        last_latency_ms,
        last_http_status,
        last_error_message,
    ) = row

    clean_last_latency = _normalize_latency(last_latency_ms)
    if last_checked_at is None or last_is_up is None:
        target_status: TargetStatus = "unknown"
    else:
        target_status = evaluate_state(
            bool(last_is_up), clean_last_latency, warning_ms, critical_ms
        )

    total = max(int(total_checks or 0), 0)
    up = min(max(int(up_checks or 0), 0), total)
    return TargetSummary(
        target_id=target_id,
        name=name,
        host_or_url=host_or_url,
        check_type=check_type,
        interval_seconds=interval_seconds,
        warning_ms=warning_ms,
        critical_ms=critical_ms,
        enabled=enabled,
        status=target_status,
        last_checked_at=last_checked_at,
        last_is_up=last_is_up,
        last_latency_ms=clean_last_latency,
        last_http_status=last_http_status,
        last_error_message=last_error_message,
        total_checks=total,
        up_checks=up,
        failed_checks=total - up,
        uptime_pct=calculate_uptime_pct(total, up),
        avg_latency_ms=_normalize_latency(avg_latency_raw),
    )


@router.post("", response_model=TargetRead, status_code=status.HTTP_201_CREATED)
def create_target(payload: TargetCreate, db: Session = Depends(get_db)):
    """Add a new target to monitor."""
    target = Target(**payload.model_dump())
    db.add(target)
    db.commit()
    db.close()  # release the connection before acquiring the scheduler lock
    if target.enabled and target.check_type in SUPPORTED_CHECK_TYPES:
        scheduler.sync_target(target.id)  # start checking it in the background
    return target


@router.get("", response_model=list[TargetRead])
def list_targets(db: Session = Depends(get_db)):
    """List all targets, oldest first."""
    return db.scalars(_LIST_TARGETS_QUERY).all()


@router.get("/summaries", response_model=list[TargetSummary])
def list_target_summaries(
    hours: HoursWindow = None,
    db: Session = Depends(get_db),
):
    """Status, latest check and uptime summary for every target, oldest target first."""
    rows = db.execute(_ALL_TARGETS_WITH_LATEST_QUERY).all()
    if not rows:
        return []
    if all(row[8] is None for row in rows):
        return [_build_summary(row, 0, 0, None) for row in rows]

    if hours is None:
        agg_rows = db.execute(_AGG_ALL_QUERY).all()
    else:
        since = utc_now() - timedelta(hours=hours)
        if all(row[8] is None or row[8] < since for row in rows):
            return [_build_summary(row, 0, 0, None) for row in rows]
        agg_rows = db.execute(_AGG_ALL_SINCE_QUERY, {"since": since}).all()
    agg_by_target = {r[0]: (r[1], r[2], r[3]) for r in agg_rows}

    summaries = []
    for row in rows:
        total, up, avg_lat = agg_by_target.get(row[0], (0, 0, None))
        summaries.append(_build_summary(row, total, up, avg_lat))
    return summaries


@router.get("/{target_id}", response_model=TargetRead)
def get_target(target_id: TargetId, db: Session = Depends(get_db)):
    """Get one target by id."""
    return _get_target_or_404(target_id, db)


@router.get("/{target_id}/summary", response_model=TargetSummary)
def get_target_summary(
    target_id: TargetId,
    hours: HoursWindow = None,
    db: Session = Depends(get_db),
):
    """Status, latest check and uptime summary for one target."""
    row = db.execute(_ONE_TARGET_WITH_LATEST_QUERY, {"target_id": target_id}).one_or_none()
    if row is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")
    if row[8] is None:
        return _build_summary(row, 0, 0, None)

    if hours is None:
        agg = db.execute(_AGG_ONE_QUERY, {"target_id": target_id}).one()
    else:
        since = utc_now() - timedelta(hours=hours)
        if row[8] < since:
            return _build_summary(row, 0, 0, None)
        agg = db.execute(
            _AGG_ONE_SINCE_QUERY, {"target_id": target_id, "since": since}
        ).one()
    return _build_summary(row, agg[0], agg[1], agg[2])


@router.patch("/{target_id}", response_model=TargetRead)
def update_target(target_id: TargetId, payload: TargetUpdate, db: Session = Depends(get_db)):
    """Edit a target. Only the fields sent are changed."""
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        # Acquire SQLite's write lock before reading so two concurrent PATCH requests
        # cannot validate against a stale snapshot and commit conflicting fields.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    target = _get_target_or_404(target_id, db)

    # Re-validate cross-field rules against the merged values, e.g. sending only
    # warning_ms must keep it below the stored critical_ms.
    try:
        if "host_or_url" in changes or "check_type" in changes:
            validate_host_or_url(
                changes.get("host_or_url", target.host_or_url),
                changes.get("check_type", target.check_type),
            )
        if "warning_ms" in changes or "critical_ms" in changes:
            validate_thresholds(
                changes.get("warning_ms", target.warning_ms),
                changes.get("critical_ms", target.critical_ms),
            )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"field": "(request)", "message": str(exc)}],
        )

    changed_fields = {
        field for field, value in changes.items() if getattr(target, field) != value
    }
    if not changed_fields:
        db.expunge(target)  # keep loaded attributes in memory so rollback does not expire them
        db.rollback()
        return target

    for field in changed_fields:
        setattr(target, field, changes[field])
    db.commit()
    db.close()  # release the connection before acquiring the scheduler lock
    if _SCHEDULE_FIELDS & changed_fields:
        scheduler.sync_target(target_id)  # apply a new interval, enable/disable, etc.
    return target


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(target_id: TargetId, db: Session = Depends(get_db)):
    """Delete a target together with its check results and events."""
    params = {"target_id": target_id}
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    # Delete the history and target with bulk statements instead of loading ORM rows,
    # and so that it also works on databases created before ON DELETE CASCADE was added.
    db.execute(_DELETE_RESULTS_STMT, params)
    db.execute(_DELETE_EVENTS_STMT, params)
    deleted = db.execute(_DELETE_TARGET_STMT, params).rowcount
    if not deleted:
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")
    db.commit()
    db.close()
    scheduler.remove_target(target_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{target_id}/check", response_model=CheckResultRead, status_code=status.HTTP_201_CREATED)
def run_manual_check(target_id: TargetId, db: Session = Depends(get_db)):
    """Run one check on the target right now and store the result.

    A failed check (timeout, DNS error...) is still a valid result: it is stored
    with is_up=false and an error_message, and returned with status 201.
    """
    row = db.execute(_MANUAL_CHECK_TARGET_QUERY, {"target_id": target_id}).one_or_none()
    if row is None:
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")
    stub = Target(id=target_id, host_or_url=row[0], check_type=row[1])
    try:
        result = run_check_and_store(db, stub)
    except NotImplementedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
    if result is None:
        exists = db.scalar(_TARGET_EXISTS_QUERY, {"target_id": target_id}) is not None
        db.rollback()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Target {target_id} was deleted during the check")
        raise HTTPException(status_code=409, detail=f"Target {target_id} was modified during the check")
    return result


@router.get("/{target_id}/results", response_model=list[CheckResultRead])
def list_results(
    target_id: TargetId,
    limit: int = Query(50, ge=1, le=1000),
    hours: HoursWindow = None,
    db: Session = Depends(get_db),
):
    """Most recent check results for a target, newest first."""
    if hours is None:
        results = db.scalars(
            _LIST_RESULTS_QUERY, {"target_id": target_id, "limit": limit}
        ).all()
    else:
        since = utc_now() - timedelta(hours=hours)
        results = db.scalars(
            _LIST_RESULTS_SINCE_QUERY,
            {"target_id": target_id, "since": since, "limit": limit},
        ).all()
    if not results:
        # Foreign keys guarantee a target exists when results are present; only query
        # the targets table when there are no results (to distinguish empty vs 404).
        _ensure_target_exists_or_404(target_id, db)
    return results


@router.get("/{target_id}/events", response_model=list[EventRead])
def list_target_events(
    target_id: TargetId,
    limit: int = Query(50, ge=1, le=1000),
    severity: Optional[EventSeverity] = Query(None, description="Filter by severity."),
    db: Session = Depends(get_db),
):
    """Most recent state-change events for a target, newest first."""
    if severity is None:
        events = db.scalars(
            _LIST_TARGET_EVENTS_QUERY, {"target_id": target_id, "limit": limit}
        ).all()
    else:
        events = db.scalars(
            _LIST_TARGET_EVENTS_BY_SEVERITY_QUERY,
            {"target_id": target_id, "severity": severity, "limit": limit},
        ).all()
    if not events:
        _ensure_target_exists_or_404(target_id, db)
    return events


@events_router.get("", response_model=list[EventRead])
def list_all_events(
    limit: int = Query(50, ge=1, le=1000),
    severity: Optional[EventSeverity] = Query(None, description="Filter by severity."),
    target_id: Optional[int] = Query(
        None, ge=1, le=2_147_483_647, description="Filter by target id."
    ),
    db: Session = Depends(get_db),
):
    """Most recent state-change events across all targets, newest first."""
    if target_id is not None:
        if severity is None:
            return db.scalars(
                _LIST_TARGET_EVENTS_QUERY, {"target_id": target_id, "limit": limit}
            ).all()
        return db.scalars(
            _LIST_TARGET_EVENTS_BY_SEVERITY_QUERY,
            {"target_id": target_id, "severity": severity, "limit": limit},
        ).all()
    if severity is None:
        return db.scalars(_LIST_ALL_EVENTS_QUERY, {"limit": limit}).all()
    return db.scalars(
        _LIST_ALL_EVENTS_BY_SEVERITY_QUERY, {"severity": severity, "limit": limit}
    ).all()
