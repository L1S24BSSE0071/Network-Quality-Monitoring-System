# Week 2 Review Notes

**Period:** 26 October - 1 November 2026 (planned). **Completed:** 1 November 2026.
**Theme:** Automatic checks and alerting.

## Milestone

> A test target is taken offline and a critical event appears in the log, followed by a recovery event when it returns.

**Met.** Demonstration steps:

1. Start the server (`python -m uvicorn app.main:app --reload`) and open `/docs`.
2. `POST /targets` with `{"name": "Local Test", "host_or_url": "http://127.0.0.1:9999"}` (an offline port).
3. The background scheduler (or `POST /targets/1/check`) checks the target, records `is_up: false` in `check_results`, and creates **one** `critical` event in `events` (`GET /targets/1/events` or `GET /events`).
4. Subsequent failed checks record new `check_results` rows without creating duplicate `critical` events.
5. Start a local server on port `9999` (or point the target at a reachable URL) and run another check: a single `recovered` event is logged, and `GET /targets/1/summary` reports `status: "normal"` along with `uptime_pct` and `avg_latency_ms`.

## Tasks against the plan

| Planned task | Status | Output |
|--------------|--------|--------|
| Add the scheduler so each target runs at its own interval and results are stored with timestamps | Done | `app/scheduler.py` (`BackgroundScheduler`, `ThreadPoolExecutor(20)`, live sync on create/edit/delete) |
| Buffer day: finish unfinished work; optional ping check if time allows | Done | Caught up from Week 1 delay; hardened scheduler concurrency and DB pooling (HTTP kept as core check) |
| Handle timeouts, DNS failures, invalid URLs and certificate errors without crashing the scheduler | Done | `app/checker.py` (`_describe_connect_error`, daemon thread deadline for hung OS DNS, exception isolation in `run_scheduled_check`) |
| Evaluate warning and critical thresholds and create events on state changes, including recovery | Done | `app/evaluator.py` (`evaluate_state`, `state_from_last_severity`, `evaluate_transition`, `record_state_change`) |
| Calculate uptime percentage and build endpoints for history, events and summaries | Done | `calculate_uptime_pct`, `GET /targets/summaries`, `GET /targets/{id}/summary`, `GET /targets/{id}/results?hours=...`, `GET /targets/{id}/events`, `GET /events` |
| Write unit tests for threshold logic and prepare the Week 2 review | Done | `tests/test_evaluator.py`, `tests/test_scheduler.py`, `docs/week2-review.md` (`193` passing `pytest` tests) |

## Definition of done

| Criterion | Result |
|-----------|--------|
| Scheduled checks run for at least two targets without blocking the API | Met (`ThreadPoolExecutor(20)`, connection released before network I/O) |
| Taking a test target offline produces exactly one critical event and bringing it back produces one recovery event | Met (verified in `test_offline_then_online_milestone_scenario` and `test_full_state_lifecycle_creates_events_only_on_changes`) |
| The threshold unit tests pass | Met (`193 passed` across API, checker, evaluator and scheduler suites) |
| Work is committed and tagged `week-2` | Met |

## Testing, debugging and optimization performed

The automated test suite (`python -m pytest`) now comprises **193 tests** running in ~6 seconds against an isolated SQLite database without network access:

- **Threshold evaluator & uptime math (`tests/test_evaluator.py` — 66 tests):** Pure unit tests for `evaluate_state`, `state_from_last_severity`, `evaluate_transition`, and `calculate_uptime_pct` covering exact threshold boundaries (`latency_ms == warning_ms` vs `> warning_ms`), all 9 state transitions (`Normal <-> Warning <-> Critical`), 500-character message truncation, large/fractional latency formatting without scientific notation (`_format_ms`), rejection of boolean/negative/non-finite (`NaN`, `±inf`) latencies and invalid thresholds, full multi-step state lifecycles, and API tests for `/events`, `/targets/{id}/events`, `/targets/summaries`, `/targets/{id}/summary`, and `?hours=...` time-window filtering.
- **Target API & concurrency (`tests/test_api.py` — 75 tests):** CRUD, input validation, IDNA/Punycode rules, `BEGIN IMMEDIATE` write serialization, no-op `PATCH` fast path (`db.expunge(target)` before rollback), and bulk `DELETE`.
- **HTTP checker (`tests/test_checker.py` — 35 tests):** 200 OK, redirects, 4xx/5xx responses, headers-only streaming, timeouts, hung OS DNS deadline abandonment, connection release during network I/O, and mid-check target edit/delete handling.
- **Background scheduler (`tests/test_scheduler.py` — 17 tests):** Immediate first check, per-target intervals, live rescheduling on URL/interval/enabled edits, mid-flight URL change retry (`for _ in range(3)` in `run_scheduled_check`), failure isolation, and thread-safe concurrent schedule updates.

## Key optimizations in Week 2

| Area | Optimization |
|------|--------------|
| Single-query post-check verification | Combined the post-check `Target` verification and latest `Event.severity` lookup inside `run_check_and_store` into a single pre-built query (`_POST_CHECK_TARGET_QUERY`) with a correlated scalar subquery (`_LAST_SEVERITY_SUBQ`), cutting SQL `SELECT` statements inside the `BEGIN IMMEDIATE` write lock from 2 to 1. |
| Pre-built parameterized SQLAlchemy queries | Pre-built module-level `select()` and `delete()` statements with `bindparam()` across `app/checker.py`, `app/evaluator.py`, `app/scheduler.py`, and `app/routers/targets.py`, achieving a **2.46x speedup** on query execution overhead (`663.9 ms` down to `269.8 ms` per 2,000 queries). |
| Lightweight pre-check target lookup | Replaced full 9-column `Target` ORM hydration (`db.get`) before manual and scheduled checks with a 2-column tuple query (`host_or_url, check_type`), cutting pre-check DB overhead nearly in half (`1273.3 ms` to `674.7 ms` per 2,000 checks). |
| Covering-index summary join | Used a correlated scalar subquery over the composite covering index `ix_check_results_target_checked (target_id, checked_at)` to join each target's latest check result by primary key in `O(log M)` without scanning historical rows. |

## Plan for Week 3 (2 - 8 November 2026: Dashboard)

1. Build the browser dashboard layout (HTML, CSS, JavaScript) served by FastAPI and connected to `/targets/summaries`, `/targets/{id}/results`, and `/events`.
2. Display per-target status indicators (`normal`, `warning`, `critical`, `unknown`), last check time, latest latency, and uptime summary cards/table.
3. Add the Chart.js latency history chart with a time-range selector (`?hours=...`).
4. Add forms to add, edit, enable/disable, and delete targets from the UI with inline validation messages.
5. Add status filtering, the event log view, in-app alert banner, and CSV report export.
