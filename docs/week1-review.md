# Week 1 Review Notes

**Period:** 19 - 25 October 2026 (planned). **Completed:** 28 October 2026 (updated 29 October 2026 after debugging and optimization).
**Theme:** Foundation and data model (plus Week 2 Mon-Wed background scheduler).

## Milestone

> A target can be added, one check can be run by hand and its result appears in the database.

**Met.** Demonstration steps:

1. Start the server (`python -m uvicorn app.main:app --reload`) and open `/docs`.
2. `POST /targets` with `{"name": "Example", "host_or_url": "https://example.com"}`.
3. `POST /targets/1/check`, which stores a result with latency (`latency_ms`), HTTP status (`http_status`) and a UTC timestamp (`checked_at` ending in `Z`).
4. `GET /targets/1/results` shows the stored row.
5. Send invalid data (for example `warning_ms` above `critical_ms` or an unknown field) to show the clear 422 validation message.

## Tasks against the plan

| Planned task | Status | Output |
|--------------|--------|--------|
| Discussion with the project lead; confirm scope, stack and plan | To be confirmed by the author | Record any scope changes here |
| Requirements and scope document | Done | `docs/requirements.md` (22 functional requirements, 8 validation rules, 9 non-functional requirements) |
| Database schema and architecture design | Done | `docs/design.md` (ER, architecture, check flow, state diagrams) |
| Repository, virtual environment, FastAPI skeleton, SQLite | Done | `app/main.py`, `app/database.py`, `app/models.py`, `requirements.txt` |
| Target API with input validation | Done | `app/routers/targets.py`, `app/schemas.py`, `app/errors.py` |
| HTTP check with timeout and error handling; store one result | Done | `app/checker.py`, `POST /targets/{id}/check` |
| Background scheduler (Week 2 Mon-Wed) | Done | `app/scheduler.py` (APScheduler `BackgroundScheduler`, per-target intervals, live sync) |
| Automated test suite & hardening | Done | `tests/` (`122` automated `pytest` tests across API, checker and scheduler) |
| README draft, review notes and PDF progress report | Done | `README.md`, `docs/week1-review.md`, `docs/week1-progress-report.pdf` |

## Definition of done

| Criterion | Result |
|-----------|--------|
| The API starts without errors | Met |
| Invalid data returns a clear error message | Met (field and message) |
| The stored check result matches the manual check | Met (verified against the database) |
| Schema and architecture diagrams are in the repository | Met (`docs/design.md`) |
| Work is committed and tagged `week-1` | Met |

## Testing, debugging and optimization performed

An automated test suite (`python -m pytest`) with **122 tests** runs in ~5 seconds against a throwaway SQLite database without touching the network:

- **Input validation (`tests/test_api.py`):** Valid and invalid HTTP/HTTPS URLs, Ping hostnames and IPv4/IPv6 addresses, IDNA/Punycode domains (rejecting leading/trailing hyphens, leading combining marks, symbol numbers, numeric hosts/TLDs and `%` scope IDs), URL userinfo rejection, control-character and Unicode whitespace rejection, strict integer/boolean typing, threshold bounds (`1-3,600,000 ms`), unknown fields (`extra="forbid"`), malformed/empty JSON bodies, and 32-bit `TargetId` path bounds.
- **Target API & concurrency (`tests/test_api.py`):** Create, list, get, partial edit (`PATCH`), cascade delete, `404` handling, no-op `PATCH` fast path, and `BEGIN IMMEDIATE` serialization preventing concurrent `PATCH` requests from violating `warning_ms < critical_ms`.
- **HTTP checker (`tests/test_checker.py`):** 200 OK, redirects, 404/503 responses, headers-only streaming (without downloading response bodies), timeout, hung OS DNS deadline abandonment (`DEADLINE_GRACE_SECONDS`), redirect loops, DNS/TLS/refused connection error classification, connection release during network I/O, and stale in-flight check rejection (`409` / `404`) when a target is edited, disabled or deleted mid-check.
- **Background scheduler (`tests/test_scheduler.py`):** Immediate check on startup/enable, per-target intervals, skipping disabled and unsupported (`ping`) targets, live rescheduling on URL/interval/enabled edits without restarting on name/threshold edits, failure isolation, and thread-safe concurrent schedule updates.

## Problems encountered and resolutions

| Problem | Resolution |
|---------|------------|
| Week 1 finished three days late (started on the 2nd week) | Remaining tasks were completed in order, one at a time, on 28-29 October. |
| Measured latency included HTTP client TLS/CA setup (~690 ms) and Windows proxy registry lookups | Pre-created a shared `_SSL_CONTEXT`, set `trust_env=False` (11.3x faster client init), and streamed headers only; local checks now read ~3-4 ms. |
| Hung OS DNS lookups could block beyond `httpx` timeouts | Wrapped live checks in a daemon worker thread joined with `timeout_seconds + DEADLINE_GRACE_SECONDS`. |
| Slow checks held SQLite connections and could exhaust the pool under concurrency | Released the session transaction (`db.rollback()`, `db.expunge_all()`) before network I/O and sized `QueuePool` to `pool_size=20, max_overflow=20`. |
| SQLite returned naive timestamps and ignored foreign keys by default | Added `UTCDateTime` type decorator (timestamps always end in `Z`) and enabled `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, and `busy_timeout=10000`. |

## Decisions made

- HTTP status 400 or above counts as **down** (`is_up=False`), while still recording `latency_ms` and `http_status`.
- A failed check is still a valid stored result (`HTTP 201` from `POST /targets/{id}/check`).
- Moving from `Critical` to `Warning` logs a `warning` event, and returning to `Normal` logs a `recovered` event (to be implemented with the threshold evaluator in Week 2 Thu-Fri).
- Ping targets can be created but return `501 Not Implemented` on check until optional ping support is added.
- Each feature and fix branch was merged (`--no-ff`) into `main` after testing.

## Remaining plan for Week 2 (Thu - Sun)

1. Threshold evaluation (`normal`, `warning`, `critical`, `recovered`) and storing `events` rows only on state changes.
2. Uptime percentage calculation and API endpoints for history, events and summaries.
3. Unit tests for the threshold evaluator, Week 2 review notes, and tagging `week-2`.
