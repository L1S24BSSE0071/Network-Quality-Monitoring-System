"""API endpoints for managing monitoring targets."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import format_errors
from app.models import Target
from app.schemas import TargetCreate, TargetRead, TargetUpdate

router = APIRouter(prefix="/targets", tags=["targets"])


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
    return target


@router.get("", response_model=list[TargetRead])
def list_targets(db: Session = Depends(get_db)):
    """List all targets, oldest first."""
    return db.scalars(select(Target).order_by(Target.id)).all()


@router.get("/{target_id}", response_model=TargetRead)
def get_target(target_id: int, db: Session = Depends(get_db)):
    """Get one target by id."""
    return _get_target_or_404(target_id, db)


@router.patch("/{target_id}", response_model=TargetRead)
def update_target(target_id: int, payload: TargetUpdate, db: Session = Depends(get_db)):
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
    return target


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(target_id: int, db: Session = Depends(get_db)):
    """Delete a target together with its check results and events."""
    target = _get_target_or_404(target_id, db)
    db.delete(target)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
