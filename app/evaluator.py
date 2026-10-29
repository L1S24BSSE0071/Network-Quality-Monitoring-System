"""Threshold evaluation and state-change event generation."""
import math
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckResult, Event, Target, utc_now
from app.schemas import MAX_THRESHOLD_MS

TargetState = Literal["normal", "warning", "critical"]
EventSeverity = Literal["warning", "critical", "recovered"]

_VALID_STATES = {"normal", "warning", "critical"}
_VALID_SEVERITIES = {None, "recovered", "warning", "critical"}
_UNSET = object()


@dataclass
class EventDecision:
    """An event to record when a target changes state."""

    severity: EventSeverity
    message: str
    latency_ms: Optional[float]


def _format_ms(value: float) -> str:
    """Format a millisecond value up to 2 decimal places without trailing zeros or scientific notation."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def state_from_last_severity(last_severity: Optional[str]) -> TargetState:
    """Infer the target's state before a check from its most recent event severity.

    A new target (no events yet) or a target whose latest event is 'recovered'
    is in the 'normal' state.
    """
    if last_severity not in _VALID_SEVERITIES:
        raise ValueError(f"Invalid event severity: {last_severity!r}")
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

    A target is critical if the check failed (is_up is False or latency_ms is missing/non-finite)
    or if latency_ms is strictly above critical_ms. It is warning if latency_ms is
    strictly above warning_ms (and at or below critical_ms). Otherwise it is normal.
    """
    if (
        type(warning_ms) is not int
        or type(critical_ms) is not int
        or warning_ms <= 0
        or warning_ms >= critical_ms
        or critical_ms > MAX_THRESHOLD_MS
    ):
        raise ValueError(
            f"warning_ms and critical_ms must be whole numbers between 1 and {MAX_THRESHOLD_MS} "
            "with warning_ms < critical_ms"
        )
    if not is_up or latency_ms is None or not math.isfinite(latency_ms) or latency_ms < 0:
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
    if previous_state not in _VALID_STATES:
        raise ValueError(f"Invalid previous_state: {previous_state!r}")

    has_valid_latency = (
        latency_ms is not None and math.isfinite(latency_ms) and latency_ms >= 0
    )
    clean_latency = round(latency_ms, 2) if has_valid_latency else None

    new_state = evaluate_state(is_up, clean_latency, warning_ms, critical_ms)
    if new_state == previous_state:
        return new_state, None

    if new_state == "critical":
        if not is_up or clean_latency is None:
            clean_error = error_message.strip() if error_message else ""
            detail = f"Target is down: {clean_error}" if clean_error else "Target is down"
        else:
            detail = (
                f"Latency {_format_ms(clean_latency)} ms exceeded critical threshold ({critical_ms} ms)"
            )
        return new_state, EventDecision(
            severity="critical",
            message=detail[:500],
            latency_ms=clean_latency,
        )

    assert clean_latency is not None
    formatted = _format_ms(clean_latency)

    if new_state == "warning":
        if previous_state == "critical":
            detail = (
                f"Target improved from critical to warning: latency {formatted} ms "
                f"exceeded warning threshold ({warning_ms} ms)"
            )
        else:
            detail = f"Latency {formatted} ms exceeded warning threshold ({warning_ms} ms)"
        return new_state, EventDecision(
            severity="warning",
            message=detail[:500],
            latency_ms=clean_latency,
        )

    # new_state == "normal" (from "warning" or "critical")
    detail = f"Target recovered (latency {formatted} ms, threshold {warning_ms} ms)"
    return new_state, EventDecision(
        severity="recovered",
        message=detail[:500],
        latency_ms=clean_latency,
    )


def record_state_change(
    db: Session,
    target: Target,
    result: CheckResult,
    *,
    last_severity: object = _UNSET,
) -> Optional[Event]:
    """Evaluate result against target's thresholds and add an Event to db if the state changed.

    Caller must hold the write transaction (and commit afterwards) so the CheckResult
    and any resulting Event are saved atomically.
    """
    target_id = result.target_id if result.target_id is not None else target.id
    if last_severity is _UNSET:
        last_severity = db.scalar(
            select(Event.severity)
            .where(Event.target_id == target_id)
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(1)
        )
    previous_state = state_from_last_severity(last_severity)  # type: ignore[arg-type]
    if result.latency_ms is not None:
        if not math.isfinite(result.latency_ms) or result.latency_ms < 0:
            result.latency_ms = None
        else:
            result.latency_ms = round(result.latency_ms, 2)
    if result.latency_ms is None and result.is_up:
        result.is_up = False
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

    if result.checked_at is None:
        result.checked_at = utc_now()
    event = Event(
        target_id=target_id,
        occurred_at=result.checked_at,
        severity=decision.severity,
        message=decision.message,
        latency_ms=decision.latency_ms,
    )
    db.add(event)
    return event
