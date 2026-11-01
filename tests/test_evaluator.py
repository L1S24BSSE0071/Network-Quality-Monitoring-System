"""Unit and integration tests for threshold evaluation and state-change events."""
from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

from app import checker, database
from app.checker import CheckOutcome
from app.evaluator import (
    calculate_uptime_pct,
    evaluate_state,
    evaluate_transition,
    record_state_change,
    state_from_last_severity,
)
from app.models import CheckResult, Event, Target, utc_now


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


@pytest.mark.parametrize("bad_latency", [float("nan"), float("inf"), float("-inf"), -5.0, True, False])
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


@pytest.mark.parametrize("bad_is_up", [1, 0, None, "yes"])
def test_evaluate_state_rejects_non_boolean_is_up(bad_is_up):
    with pytest.raises(ValueError, match="is_up"):
        evaluate_state(bad_is_up, 50.0, warning_ms=500, critical_ms=1000)  # type: ignore[arg-type]


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


def test_threshold_edit_during_in_flight_check_applies_new_thresholds(client, make_target, monkeypatch):
    target = make_target(warning_ms=500, critical_ms=1000)
    target_id = target["id"]

    def check_while_lowering_thresholds(url, **kw):
        client.patch(f"/targets/{target_id}", json={"warning_ms": 100, "critical_ms": 300})
        return CheckOutcome(is_up=True, latency_ms=200.0, http_status=200)

    monkeypatch.setattr(checker, "perform_http_check", check_while_lowering_thresholds)
    resp = client.post(f"/targets/{target_id}/check")
    assert resp.status_code == 201
    events = _fetch_events(target_id)
    assert len(events) == 1
    assert events[0].severity == "warning"
    assert "100 ms" in events[0].message


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


# ---------------------------------------------------------------------------
# Uptime percentage, history window, events and summary endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "up", "expected"),
    [
        (0, 0, None),
        (5, 0, 0.0),
        (5, 5, 100.0),
        (3, 2, 66.67),
        (3, 1, 33.33),
        (4, 3, 75.0),
    ],
)
def test_calculate_uptime_pct(total, up, expected):
    assert calculate_uptime_pct(total, up) == expected


@pytest.mark.parametrize(
    ("total", "up"),
    [
        (-1, 0),
        (5, -1),
        (2, 3),
        (True, 1),
        (1, False),
        (1.0, 1),
    ],
)
def test_calculate_uptime_pct_rejects_invalid_counts(total, up):
    with pytest.raises(ValueError):
        calculate_uptime_pct(total, up)  # type: ignore[arg-type]


def test_events_endpoints_list_and_filter_by_severity_and_target(client, make_target, monkeypatch):
    t1 = make_target(name="One", warning_ms=100, critical_ms=300)
    t2 = make_target(name="Two", warning_ms=100, critical_ms=300)

    outcomes_t1 = iter(
        [
            CheckOutcome(is_up=True, latency_ms=200.0, http_status=200),  # warning
            CheckOutcome(is_up=False, error_message="Down"),              # critical
            CheckOutcome(is_up=True, latency_ms=40.0, http_status=200),   # recovered
        ]
    )
    monkeypatch.setattr(checker, "perform_http_check", lambda url: next(outcomes_t1))
    for _ in range(3):
        assert client.post(f"/targets/{t1['id']}/check").status_code == 201

    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url: CheckOutcome(is_up=False, error_message="Timeout"),
    )
    assert client.post(f"/targets/{t2['id']}/check").status_code == 201

    # Per-target events (newest first)
    r1 = client.get(f"/targets/{t1['id']}/events")
    assert r1.status_code == 200
    assert [e["severity"] for e in r1.json()] == ["recovered", "critical", "warning"]

    # Per-target filter by severity and limit
    only_crit = client.get(f"/targets/{t1['id']}/events?severity=critical").json()
    assert [e["severity"] for e in only_crit] == ["critical"]
    limited = client.get(f"/targets/{t1['id']}/events?limit=2").json()
    assert len(limited) == 2

    # Global /events endpoint
    all_events = client.get("/events").json()
    assert len(all_events) == 4
    crit_all = client.get("/events?severity=critical").json()
    assert len(crit_all) == 2
    by_target = client.get(f"/events?target_id={t2['id']}").json()
    assert len(by_target) == 1 and by_target[0]["target_id"] == t2["id"]
    by_target_sev = client.get(f"/events?target_id={t1['id']}&severity=recovered").json()
    assert len(by_target_sev) == 1 and by_target_sev[0]["severity"] == "recovered"

    # Unknown target on /targets/{id}/events is 404; invalid severity is 422
    assert client.get("/targets/99999/events").status_code == 404
    assert client.get(f"/targets/{t1['id']}/events?severity=bad").status_code == 422
    assert client.get("/events?severity=bad").status_code == 422


def test_summaries_and_time_window_filtering(client, make_target):
    assert client.get("/targets/summaries").json() == []

    unchecked = make_target(name="Unchecked", warning_ms=100, critical_ms=300)
    checked = make_target(name="Checked", warning_ms=100, critical_ms=300)

    # Unchecked target has status='unknown' and None uptime/latency
    s_unchecked = client.get(f"/targets/{unchecked['id']}/summary").json()
    assert s_unchecked["status"] == "unknown"
    assert s_unchecked["total_checks"] == 0
    assert s_unchecked["uptime_pct"] is None
    assert s_unchecked["avg_latency_ms"] is None

    now = utc_now()
    with database.SessionLocal() as db:
        db.add_all(
            [
                # Old failure 5 hours ago
                CheckResult(
                    target_id=checked["id"],
                    checked_at=now - timedelta(hours=5),
                    is_up=False,
                    latency_ms=None,
                    error_message="Old outage",
                ),
                # Recent healthy check 30 minutes ago (50 ms)
                CheckResult(
                    target_id=checked["id"],
                    checked_at=now - timedelta(minutes=30),
                    is_up=True,
                    latency_ms=50.0,
                    http_status=200,
                ),
                # Latest check 5 minutes ago (150 ms -> warning)
                CheckResult(
                    target_id=checked["id"],
                    checked_at=now - timedelta(minutes=5),
                    is_up=True,
                    latency_ms=150.0,
                    http_status=200,
                ),
            ]
        )
        db.commit()

    # All-time summary for `checked`: 2 up out of 3 (66.67%), avg latency (50+150)/2 = 100.0, latest status='warning'
    s_all = client.get(f"/targets/{checked['id']}/summary").json()
    assert s_all["status"] == "warning"
    assert s_all["last_latency_ms"] == 150.0
    assert s_all["total_checks"] == 3
    assert s_all["up_checks"] == 2
    assert s_all["failed_checks"] == 1
    assert s_all["uptime_pct"] == 66.67
    assert s_all["avg_latency_ms"] == 100.0

    # Last 1 hour summary: 2 up out of 2 (100.0%)
    s_1h = client.get(f"/targets/{checked['id']}/summary?hours=1").json()
    assert s_1h["status"] == "warning"
    assert s_1h["total_checks"] == 2
    assert s_1h["up_checks"] == 2
    assert s_1h["failed_checks"] == 0
    assert s_1h["uptime_pct"] == 100.0

    # List summaries returns both targets in order
    all_summaries = client.get("/targets/summaries").json()
    assert [s["target_id"] for s in all_summaries] == [unchecked["id"], checked["id"]]
    assert [s["status"] for s in all_summaries] == ["unknown", "warning"]

    all_summaries_1h = client.get("/targets/summaries?hours=1").json()
    assert all_summaries_1h[1]["uptime_pct"] == 100.0

    # Results time-window filter (?hours=1)
    r_all = client.get(f"/targets/{checked['id']}/results").json()
    r_1h = client.get(f"/targets/{checked['id']}/results?hours=1").json()
    assert len(r_all) == 3
    assert len(r_1h) == 2

    # 404 and 422 checks
    assert client.get("/targets/99999/summary").status_code == 404
    assert client.get(f"/targets/{checked['id']}/summary?hours=0").status_code == 422
    assert client.get(f"/targets/{checked['id']}/summary?hours=721").status_code == 422
    assert client.get(f"/targets/{checked['id']}/results?hours=0").status_code == 422


def test_summary_hours_window_when_all_checks_are_older_than_window(client, make_target):
    target = make_target(name="OldChecksOnly", warning_ms=100, critical_ms=300)
    with database.SessionLocal() as db:
        db.add(
            CheckResult(
                target_id=target["id"],
                checked_at=utc_now() - timedelta(hours=5),
                is_up=True,
                latency_ms=42.5,
                http_status=200,
            )
        )
        db.commit()

    s_one = client.get(f"/targets/{target['id']}/summary?hours=1").json()
    assert s_one["status"] == "normal"
    assert s_one["last_latency_ms"] == 42.5
    assert s_one["total_checks"] == 0
    assert s_one["up_checks"] == 0
    assert s_one["failed_checks"] == 0
    assert s_one["uptime_pct"] is None
    assert s_one["avg_latency_ms"] is None

    s_list = client.get("/targets/summaries?hours=1").json()
    assert len(s_list) == 1
    assert s_list[0]["total_checks"] == 0
    assert s_list[0]["uptime_pct"] is None
    assert s_list[0]["avg_latency_ms"] is None


def test_events_and_summary_queries_use_indexes_without_temp_btree():
    from app.routers import targets as targets_router

    with database.SessionLocal() as db:
        conn = db.connection()
        for query, params, expected_idx in (
            (targets_router._LIST_ALL_EVENTS_QUERY, {"limit": 50}, "ix_events_occurred_at"),
            (
                targets_router._LIST_ALL_EVENTS_BY_SEVERITY_QUERY,
                {"severity": "critical", "limit": 50},
                "ix_events_severity_occurred",
            ),
            (
                targets_router._LIST_TARGET_EVENTS_QUERY,
                {"target_id": 1, "limit": 50},
                "ix_events_target_occurred",
            ),
            (
                targets_router._LIST_TARGET_EVENTS_BY_SEVERITY_QUERY,
                {"target_id": 1, "severity": "critical", "limit": 50},
                "ix_events_target_sev_occurred",
            ),
        ):
            compiled = query.compile(dialect=database.engine.dialect)
            bound = tuple(compiled.construct_params(params).values())
            plan = " ".join(
                row[-1]
                for row in conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}", bound).fetchall()
            )
            assert expected_idx in plan
            assert "TEMP B-TREE" not in plan

        compiled_agg = targets_router._AGG_ALL_SINCE_QUERY.compile(
            dialect=database.engine.dialect
        )
        bound_agg = tuple(
            compiled_agg.construct_params({"since": utc_now().isoformat()}).values()
        )
        plan_agg = " ".join(
            row[-1]
            for row in conn.exec_driver_sql(
                f"EXPLAIN QUERY PLAN {compiled_agg}", bound_agg
            ).fetchall()
        )
        assert "SEARCH check_results USING INDEX ix_check_results_target_checked" in plan_agg
        assert "TEMP B-TREE" not in plan_agg
