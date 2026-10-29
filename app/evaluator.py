"""Threshold evaluation and state-change event generation."""
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckResult, Event, Target, utc_now

TargetState = Literal["normal", "warning", "critical"]
EventSeverity = Literal["warning", "critical", "recovered"]


@dataclass
class EventDecision:
    """An event to record when a target changes state."""

    severity: EventSeverity
    message: str
    latency_ms: Optional[float]


def state_from_last_severity(last_severity: Optional[str]) -> TargetState:
    """Infer the target's state before a check from its most recent event severity.

    A new target (no events yet) or a target whose latest event is 'recovered'
    is in the 'normal' state.
    """
    if last_severity == "warning":
        return "warning"
    if last_severity == "critical":
        return "critical"
    return "normal"


def evaluate_state(
    is_up: bool,
    latency_ms: Optional[float],
    warning_ms: int,
    critical_ms: int,
) -> TargetState:
    """Classify a single check result as 'normal', 'warning' or 'critical'.

    A target is critical if the check failed (is_up is False or latency_ms is None)
    or if latency_ms is strictly above critical_ms. It is warning if latency_ms is
    strictly above warning_ms (and at or below critical_ms). Otherwise it is normal.
    """
    if not is_up or latency_ms is None:
        return "critical"
    if latency_ms > critical_ms:
        return "critical"
    if latency_ms > warning_ms:
        return "warning"
    return "normal"


def evaluate_transition(
    previous_state: TargetState,
    *,
    is_up: bool,
    latency_ms: Optional[float],
    error_message: Optional[str] = None,
    warning_ms: int,
    critical_ms: int,
) -> tuple[TargetState, Optional[EventDecision]]:
    """Compare the new check state against previous_state and decide whether to log an event.

    Events are created only when the state changes:
    - Entering 'warning' (from 'normal' or 'critical') -> severity 'warning'
    - Entering 'critical' (from 'normal' or 'warning') -> severity 'critical'
    - Returning to 'normal' (from 'warning' or 'critical') -> severity 'recovered'
    """
    new_state = evaluate_state(is_up, latency_ms, warning_ms, critical_ms)
    if new_state == previous_state:
        return new_state, None

    if new_state == "critical":
        if not is_up or latency_ms is None:
            detail = f"Target is down: {error_message}" if error_message else "Target is down"
        else:
            detail = (
                f"Latency {latency_ms:g} ms exceeded critical threshold ({critical_ms} ms)"
            )
        return new_state, EventDecision(
            severity="critical",
            message=detail[:500],
            latency_ms=latency_ms,
        )

    if new_state == "warning":
        if previous_state == "critical":
            detail = (
                f"Target improved from critical to warning: latency {latency_ms:g} ms "
                f"exceeded warning threshold ({warning_ms} ms)"
            )
        else:
            detail = f"Latency {latency_ms:g} ms exceeded warning threshold ({warning_ms} ms)"
        return new_state, EventDecision(
            severity="warning",
            message=detail[:500],
            latency_ms=latency_ms,
        )

    # new_state == "normal" (from "warning" or "critical")
    detail = f"Target recovered (latency {latency_ms:g} ms, threshold {warning_ms} ms)"
    return new_state, EventDecision(
        severity="recovered",
        message=detail[:500],
        latency_ms=latency_ms,
    )


def record_state_change(
    db: Session, target: Target, result: CheckResult
) -> Optional[Event]:
    """Evaluate result against target's thresholds and add an Event to db if the state changed.

    Caller must hold the write transaction (and commit afterwards) so the CheckResult
    and any resulting Event are saved atomically.
    """
    last_severity = db.scalar(
        select(Event.severity)
        .where(Event.target_id == target.id)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    )
    previous_state = state_from_last_severity(last_severity)
    _, decision = evaluate_transition(
        previous_state,
        is_up=result.is_up,
        latency_ms=result.latency_ms,
        error_message=result.error_message,
        warning_ms=target.warning_ms,
        critical_ms=target.critical_ms,
    )
    if decision is None:
        return None

    timestamp = result.checked_at or utc_now()
    event = Event(
        target_id=target.id,
        occurred_at=timestamp,
        severity=decision.severity,
        message=decision.message,
        latency_ms=decision.latency_ms,
    )
    db.add(event)
    return event
