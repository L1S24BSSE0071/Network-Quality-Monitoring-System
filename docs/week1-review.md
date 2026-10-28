# Week 1 Review Notes

**Period:** 19 - 25 October 2026 (planned). **Completed:** 28 October 2026.
**Theme:** Foundation and data model.

## Milestone

> A target can be added, one check can be run by hand and its result appears in the database.

**Met.** Demonstration steps:

1. Start the server and open `/docs`.
2. `POST /targets` with `{"name": "Example", "host_or_url": "https://example.com"}`.
3. `POST /targets/1/check`, which stores a result with latency and HTTP status.
4. `GET /targets/1/results` shows the stored row.
5. Send invalid data (for example `warning_ms` above `critical_ms`) to show the clear validation message.

## Tasks against the plan

| Planned task | Status | Output |
|--------------|--------|--------|
| Discussion with the project lead; confirm scope, stack and plan | To be confirmed by the author | Record any scope changes here |
| Requirements and scope document | Done | `docs/requirements.md` |
| Database schema and architecture design | Done | `docs/design.md` (ER, architecture, check flow, state diagrams) |
| Repository, virtual environment, FastAPI skeleton, SQLite | Done | `app/main.py`, `app/database.py`, `requirements.txt` |
| Target API with input validation | Done | `app/routers/targets.py`, `app/schemas.py` |
| HTTP check with timeout and error handling; store one result | Done | `app/checker.py`, `POST /targets/{id}/check` |
| README draft and review notes | Done | `README.md`, this file |

## Definition of done

| Criterion | Result |
|-----------|--------|
| The API starts without errors | Met |
| Invalid data returns a clear error message | Met (field and message) |
| The stored check result matches the manual check | Met (verified against the database) |
| Schema and architecture diagrams are in the repository | Met (`docs/design.md`) |
| Work is committed and tagged `week-1` | Met |

## Testing performed

Checks were run by hand with throwaway scripts; automated unit tests are planned for Week 2.

- 17 valid and invalid target inputs against the validation rules.
- 19 API scenarios: create, list, get, edit, delete, 404 handling and cross-field validation on edit.
- Checker: 200 response, redirect, 404, 503, timeout, redirect loop, refused connection, DNS failure, self-signed certificate, expired certificate and a real website.
- End to end: check stored, listed with a limit, and removed when the target is deleted.

## Problems encountered

| Problem | Resolution |
|---------|------------|
| Week 1 finished three days late (started on the 2nd week) | Remaining tasks were completed in order, one at a time, on 28 October. |
| Measured latency included HTTP client start-up (about 760 ms for a local server) | The timer now starts after the client is created; local checks read about 4 ms. |
| Line-ending warnings between Windows and Git | Harmless; no change needed. |

## Decisions made

- HTTP status 400 or above counts as **down**, but latency and status code are still stored.
- A failed check is still a valid stored result (HTTP 201 from the manual check endpoint).
- Ping targets can be created but return 501 on check until the optional ping check is added.
- Each feature was developed on its own branch and merged into `main` after testing.

## Plan for Week 2 (26 Oct - 1 Nov, running late)

1. Scheduler: each enabled target runs at its own interval; results stored with timestamps.
2. Buffer day: catch up; ping check only if time allows.
3. Make sure timeouts, DNS failures, invalid URLs and certificate errors never crash the scheduler.
4. Threshold evaluation and events on state changes, including recovery (rules in `docs/design.md`, section 3).
5. Uptime percentage, history, events and summary endpoints.
6. Unit tests for the threshold logic.

If time is short, optional ping support is dropped first, as set out in the plan.
