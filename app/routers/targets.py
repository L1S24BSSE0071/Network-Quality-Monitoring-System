"""API endpoints for managing monitoring targets."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import scheduler
from app.checker import run_check_and_store
from app.database import get_db
from app.errors import format_errors
from app.models import CheckResult, Event, Target
from app.schemas import CheckResultRead, TargetCreate, TargetRead, TargetUpdate

router = APIRouter(prefix="/targets", tags=["targets"])

# Ids are 32-bit positive integers; anything else is rejected before reaching SQLite,
# which cannot store integers above 64 bits.
TargetId = Annotated[int, Path(ge=1, le=2_147_483_647, description="Target id")]

# Changing any of these changes what or when the scheduler checks. Other edits (name,
# thresholds) are picked up by the next scheduled check without triggering a new one.
_SCHEDULE_FIELDS = {"host_or_url", "check_type", "interval_seconds", "enabled"}


def _get_target_or_404(target_id: int, db: Session) -> Target:
    target = db.get(Target, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Target {target_id} not found")
    return target


@router.post("", response_model=TargetRead, status_code=status.HTTP_201_CREATED)
def create_target(payload: TargetCreate, db: Session = Depends(get_db)):
    """Add a new target to monitor."""
    target = Target(**payload.model_dump())
    db.add(target)
    db.commit()
    db.refresh(target)
    scheduler.sync_target(target.id, db)  # start checking it in the background
    return target


@router.get("", response_model=list[TargetRead])
def list_targets(db: Session = Depends(get_db)):
    """List all targets, oldest first."""
    return db.scalars(select(Target).order_by(Target.id)).all()


@router.get("/{target_id}", response_model=TargetRead)
def get_target(target_id: TargetId, db: Session = Depends(get_db)):
    """Get one target by id."""
    return _get_target_or_404(target_id, db)


@router.patch("/{target_id}", response_model=TargetRead)
def update_target(target_id: TargetId, payload: TargetUpdate, db: Session = Depends(get_db)):
    """Edit a target. Only the fields sent are changed."""
    target = _get_target_or_404(target_id, db)
    changes = payload.model_dump(exclude_unset=True)

    # Re-validate the merged result so cross-field rules still hold, e.g. sending
    # only warning_ms must keep it below the stored critical_ms.
    current = TargetRead.model_validate(target).model_dump(exclude={"id", "created_at"})
    try:
        TargetCreate(**{**current, **changes})
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=format_errors(exc.errors()),
        )

    for field, value in changes.items():
        setattr(target, field, value)
    db.commit()
    db.refresh(target)
    if _SCHEDULE_FIELDS & changes.keys():
        scheduler.sync_target(target_id, db)  # apply a new interval, enable/disable, etc.
    return target


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(target_id: TargetId, db: Session = Depends(get_db)):
    """Delete a target together with its check results and events."""
    target = _get_target_or_404(target_id, db)
    # Delete the history with two bulk statements instead of loading every row, and so
    # that it also works on databases created before ON DELETE CASCADE was added.
    db.execute(delete(CheckResult).where(CheckResult.target_id == target_id))
    db.execute(delete(Event).where(Event.target_id == target_id))
    db.delete(target)
    db.commit()
    scheduler.remove_target(target_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{target_id}/check", response_model=CheckResultRead, status_code=status.HTTP_201_CREATED)
def run_manual_check(target_id: TargetId, db: Session = Depends(get_db)):
    """Run one check on the target right now and store the result.

    A failed check (timeout, DNS error...) is still a valid result: it is stored
    with is_up=false and an error_message, and returned with status 201.
    """
    target = _get_target_or_404(target_id, db)
    try:
        result = run_check_and_store(db, target)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
    if result is None:  # the target was deleted while the check was running
        raise HTTPException(status_code=404, detail=f"Target {target_id} was deleted during the check")
    return result


@router.get("/{target_id}/results", response_model=list[CheckResultRead])
def list_results(
    target_id: TargetId,
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Most recent check results for a target, newest first."""
    _get_target_or_404(target_id, db)
    query = (
        select(CheckResult)
        .where(CheckResult.target_id == target_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
    )
    return db.scalars(query).all()
