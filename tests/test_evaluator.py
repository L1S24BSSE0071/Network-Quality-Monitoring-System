"""Unit and integration tests for threshold evaluation and state-change events."""
from datetime import timezone

import pytest
from sqlalchemy import select

from app import checker, database
from app.checker import CheckOutcome
from app.evaluator import (
    evaluate_state,
    evaluate_transition,
    record_state_change,
    state_from_last_severity,
)
from app.models import CheckResult, Event, Target


# ---------------------------------------------------------------------------
# Pure unit tests (no database, no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("last_severity", "expected"),
    [
        (None, "normal"),
        ("recovered", "normal"),
        ("warning", "warning"),
        ("critical", "critical"),
    ],
)
def test_state_from_last_severity(last_severity, expected):
    assert state_from_last_severity(last_severity) == expected


@pytest.mark.parametrize(
    ("is_up", "latency_ms", "expected"),
    [
        (True, 0.1, "normal"),
        (True, 500.0, "normal"),  # exactly at warning_ms is still normal ("above" raises warning)
        (True, 500.01, "warning"),
        (True, 1000.0, "warning"),  # exactly at critical_ms is warning ("above" raises critical)
        (True, 1000.01, "critical"),
        (False, None, "critical"),  # timeout / connection failure
        (False, 45.0, "critical"),  # HTTP 500 fast failure is still down -> critical
        (True, None, "critical"),  # defensive: missing latency counts as critical
    ],
)
def test_evaluate_state_boundaries(is_up, latency_ms, expected):
    assert evaluate_state(is_up, latency_ms, warning_ms=500, critical_ms=1000) == expected


@pytest.mark.parametrize(
    ("previous_state", "is_up", "latency_ms"),
    [
        ("normal", True, 120.0),
        ("warning", True, 650.0),
        ("critical", True, 1400.0),
        ("critical", False, None),
    ],
)
def test_same_state_produces_no_event(previous_state, is_up, latency_ms):
    new_state, decision = evaluate_transition(
        previous_state,
        is_up=is_up,
        latency_ms=latency_ms,
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == previous_state
    assert decision is None


def test_transition_normal_to_warning():
    new_state, decision = evaluate_transition(
        "normal",
        is_up=True,
        latency_ms=625.5,
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "warning"
    assert decision is not None
    assert decision.severity == "warning"
    assert decision.latency_ms == 625.5
    assert "625.5 ms" in decision.message
    assert "500 ms" in decision.message


def test_transition_normal_to_critical_high_latency():
    new_state, decision = evaluate_transition(
        "normal",
        is_up=True,
        latency_ms=1500.0,
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "critical"
    assert decision is not None
    assert decision.severity == "critical"
    assert decision.latency_ms == 1500.0
    assert "1500 ms" in decision.message
    assert "1000 ms" in decision.message


def test_transition_normal_to_critical_down():
    new_state, decision = evaluate_transition(
        "normal",
        is_up=False,
        latency_ms=None,
        error_message="Connection refused",
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "critical"
    assert decision is not None
    assert decision.severity == "critical"
    assert decision.latency_ms is None
    assert decision.message == "Target is down: Connection refused"


def test_transition_warning_to_critical():
    new_state, decision = evaluate_transition(
        "warning",
        is_up=False,
        latency_ms=30.0,
        error_message="HTTP 503 Service Unavailable",
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "critical"
    assert decision is not None
    assert decision.severity == "critical"
    assert decision.latency_ms == 30.0
    assert "HTTP 503 Service Unavailable" in decision.message


def test_transition_critical_to_warning():
    new_state, decision = evaluate_transition(
        "critical",
        is_up=True,
        latency_ms=700.0,
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "warning"
    assert decision is not None
    assert decision.severity == "warning"
    assert decision.latency_ms == 700.0
    assert "improved from critical to warning" in decision.message
    assert "700 ms" in decision.message


@pytest.mark.parametrize("previous_state", ["warning", "critical"])
def test_transition_to_normal_creates_recovered_event(previous_state):
    new_state, decision = evaluate_transition(
        previous_state,
        is_up=True,
        latency_ms=85.0,
        warning_ms=500,
        critical_ms=1000,
    )
    assert new_state == "normal"
    assert decision is not None
    assert decision.severity == "recovered"
    assert decision.latency_ms == 85.0
    assert "recovered" in decision.message.lower()


def test_event_message_is_truncated_to_500_chars():
    _, decision = evaluate_transition(
        "normal",
        is_up=False,
        latency_ms=None,
        error_message="x" * 600,
        warning_ms=500,
        critical_ms=1000,
    )
    assert decision is not None
    assert len(decision.message) == 500


@pytest.mark.parametrize(
    ("latency_ms", "expected_substr"),
    [
        (10000.25, "10000.25 ms"),
        (1000000.0, "1000000 ms"),
        (600.0, "600 ms"),
    ],
)
def test_large_and_fractional_latency_formatting_avoids_scientific_notation(
    latency_ms, expected_substr
):
    _, decision = evaluate_transition(
        "normal",
        is_up=True,
        latency_ms=latency_ms,
        warning_ms=500,
        critical_ms=2_000_000,
    )
    assert decision is not None
    assert expected_substr in decision.message
    assert "e+" not in decision.message


@pytest.mark.parametrize("bad_latency", [float("nan"), float("inf"), float("-inf"), -5.0])
def test_non_finite_or_negative_latency_is_treated_as_critical_without_nan_in_message(bad_latency):
    state, decision = evaluate_transition(
        "normal",
        is_up=True,
        latency_ms=bad_latency,
        warning_ms=500,
        critical_ms=1000,
    )
    assert state == "critical"
    assert decision is not None
    assert decision.severity == "critical"
    assert decision.latency_ms is None
    assert decision.message == "Target is down"


def test_record_state_change_sets_matching_timestamp_when_checked_at_is_none(make_target):
    target_json = make_target(warning_ms=100, critical_ms=200)
    with database.SessionLocal() as db:
        target = db.get(Target, target_json["id"])
        result = CheckResult(target_id=target.id, is_up=False, error_message="Timeout")
        assert result.checked_at is None
        event = record_state_change(db, target, result)
        db.add(result)
        db.commit()
        assert event is not None
        assert result.checked_at is not None
        assert event.occurred_at == result.checked_at


@pytest.mark.parametrize(
    ("warning_ms", "critical_ms"),
    [
        (1000, 1000),
        (1500, 1000),
        (0, 1000),
        (-10, 1000),
        (True, 1000),
        (500, False),
        (500.0, 1000),
        (500, 3_600_001),
    ],
)
def test_evaluate_state_rejects_invalid_thresholds(warning_ms, critical_ms):
    with pytest.raises(ValueError, match="warning_ms"):
        evaluate_state(True, 100.0, warning_ms=warning_ms, critical_ms=critical_ms)


@pytest.mark.parametrize("bad_severity", ["normal", "unknown", ""])
def test_state_from_last_severity_rejects_invalid_severity(bad_severity):
    with pytest.raises(ValueError, match="Invalid event severity"):
        state_from_last_severity(bad_severity)


@pytest.mark.parametrize("bad_previous", ["recovered", "unknown", ""])
def test_evaluate_transition_rejects_invalid_previous_state(bad_previous):
    with pytest.raises(ValueError, match="Invalid previous_state"):
        evaluate_transition(
            bad_previous,  # type: ignore[arg-type]
            is_up=True,
            latency_ms=50.0,
            warning_ms=500,
            critical_ms=1000,
        )


def test_blank_error_message_falls_back_to_clean_down_message():
    _, decision = evaluate_transition(
        "normal",
        is_up=False,
        latency_ms=None,
        error_message="   ",
        warning_ms=500,
        critical_ms=1000,
    )
    assert decision is not None
    assert decision.message == "Target is down"


# ---------------------------------------------------------------------------
# Integration tests with run_check_and_store & manual check API
# ---------------------------------------------------------------------------


def _fetch_events(target_id: int) -> list[Event]:
    with database.SessionLocal() as db:
        return list(
            db.scalars(
                select(Event)
                .where(Event.target_id == target_id)
                .order_by(Event.occurred_at.asc(), Event.id.asc())
            ).all()
        )


def test_full_state_lifecycle_creates_events_only_on_changes(client, make_target, monkeypatch):
    """Walk through Normal -> Warning -> Critical -> Warning -> Normal with repeated checks."""
    target = make_target(warning_ms=200, critical_ms=500)
    target_id = target["id"]

    outcomes = iter(
        [
            # 1-2: Normal -> no events
            CheckOutcome(is_up=True, latency_ms=50.0, http_status=200),
            CheckOutcome(is_up=True, latency_ms=120.0, http_status=200),
            # 3-4: Warning -> exactly 1 'warning' event
            CheckOutcome(is_up=True, latency_ms=300.0, http_status=200),
            CheckOutcome(is_up=True, latency_ms=450.0, http_status=200),
            # 5-7: Critical (slow, then down, then HTTP 500) -> exactly 1 'critical' event
            CheckOutcome(is_up=True, latency_ms=800.0, http_status=200),
            CheckOutcome(is_up=False, error_message="Timed out after 10 seconds"),
            CheckOutcome(is_up=False, latency_ms=25.0, http_status=500, error_message="HTTP 500"),
            # 8-9: Degraded improvement back to Warning -> 1 'warning' event
            CheckOutcome(is_up=True, latency_ms=250.0, http_status=200),
            CheckOutcome(is_up=True, latency_ms=350.0, http_status=200),
            # 10-11: Back to Normal -> 1 'recovered' event
            CheckOutcome(is_up=True, latency_ms=80.0, http_status=200),
            CheckOutcome(is_up=True, latency_ms=60.0, http_status=200),
        ]
    )
    monkeypatch.setattr(checker, "perform_http_check", lambda url: next(outcomes))

    for _ in range(11):
        resp = client.post(f"/targets/{target_id}/check")
        assert resp.status_code == 201

    events = _fetch_events(target_id)
    assert [(e.severity, e.latency_ms) for e in events] == [
        ("warning", 300.0),
        ("critical", 800.0),
        ("warning", 250.0),
        ("recovered", 80.0),
    ]
    for event in events:
        assert event.occurred_at.tzinfo == timezone.utc


def test_offline_then_online_milestone_scenario(client, make_target, monkeypatch):
    """Week 2 milestone: taking a target offline logs 1 critical event; bringing it back logs 1 recovered event."""
    target = make_target(warning_ms=500, critical_ms=1000)
    target_id = target["id"]

    outcomes = iter(
        [
            CheckOutcome(is_up=False, error_message="Connection refused"),
            CheckOutcome(is_up=False, error_message="Connection refused"),
            CheckOutcome(is_up=True, latency_ms=42.0, http_status=200),
            CheckOutcome(is_up=True, latency_ms=38.0, http_status=200),
        ]
    )
    monkeypatch.setattr(checker, "perform_http_check", lambda url: next(outcomes))

    for _ in range(4):
        resp = client.post(f"/targets/{target_id}/check")
        assert resp.status_code == 201

    events = _fetch_events(target_id)
    assert [e.severity for e in events] == ["critical", "recovered"]
    assert events[0].latency_ms is None
    assert "Connection refused" in events[0].message
    assert events[1].latency_ms == 42.0
