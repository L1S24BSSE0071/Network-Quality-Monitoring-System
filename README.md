# Network Quality Monitoring System

A small monitoring system that checks whether chosen websites and servers are reachable, records how fast they respond, and (in later weeks) raises warning and critical events when thresholds are crossed. Built with Python, FastAPI and SQLite.

> **Status: Week 1 complete (foundation and data model).** Targets can be managed through the API and a check can be run by hand and stored. Scheduled checks, alerting and the dashboard are planned for Weeks 2 and 3.

## Authorization and scope

Only your own systems and publicly available websites should be monitored. Every target is added manually. The system does not scan networks or probe ranges of addresses.

## Features

| Feature | Status |
|---------|--------|
| Add, list, edit and delete targets with input validation | Done |
| HTTP check with timeout, response-time measurement and error handling | Done |
| Manual check on demand, stored in the database | Done |
| Scheduled background checks | Week 2 |
| Warning / critical / recovery events | Week 2 |
| Uptime, history and summary endpoints | Week 2 |
| Web dashboard with latency chart | Week 3 |
| CSV report export | Week 3 |

## Requirements

- Python 3.10 or later
- Git

## Installation

```powershell
git clone https://github.com/L1S24BSSE0071/Network-Quality-Monitoring-System.git
cd Network-Quality-Monitoring-System

# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate         # Linux / macOS

pip install -r requirements.txt
```

## Running

```powershell
python -m uvicorn app.main:app --reload
```

- Interactive API documentation: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

The SQLite database file `monitoring.db` is created automatically in the folder you start the server from.

## API overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/targets` | Add a target |
| GET | `/targets` | List targets |
| GET | `/targets/{id}` | Get one target |
| PATCH | `/targets/{id}` | Edit a target (only the fields you send change) |
| DELETE | `/targets/{id}` | Delete a target with its results and events |
| POST | `/targets/{id}/check` | Run one check now and store the result |
| GET | `/targets/{id}/results?limit=50` | Stored check results, newest first |
| GET | `/health` | Confirm the API is running |

### Target fields

| Field | Default | Rule |
|-------|---------|------|
| `name` | required | Not blank, up to 100 characters |
| `host_or_url` | required | HTTP: `http://` or `https://` URL. Ping: host name or IP address |
| `check_type` | `http` | `http` or `ping` (ping is not implemented yet) |
| `interval_seconds` | 60 | 10 to 3600 |
| `warning_ms` | 500 | Greater than 0 and lower than `critical_ms` |
| `critical_ms` | 1000 | Greater than `warning_ms` |
| `enabled` | true | true or false |

### Example (PowerShell)

```powershell
# Add a target
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/targets `
  -ContentType "application/json" `
  -Body '{"name": "Example", "host_or_url": "https://example.com"}'

# Run a check on it (use the id returned above)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/targets/1/check

# See stored results
Invoke-RestMethod -Uri http://127.0.0.1:8000/targets/1/results
```

A failed check (timeout, DNS failure, refused connection, certificate error, HTTP 400 or above) is still stored, with `is_up: false` and an `error_message`.

Invalid input returns status 422 with a list of problems, for example:

```json
{"detail": [{"field": "(request)", "message": "warning_ms must be lower than critical_ms"}]}
```

## Project structure

```
app/
  main.py          Application entry point, startup and error handling
  database.py      SQLite connection and sessions
  models.py        Database tables (targets, check_results, events)
  schemas.py       Input validation rules and response shapes
  errors.py        Readable validation error messages
  checker.py       HTTP check and result storage
  routers/
    targets.py     Target, check and results endpoints
docs/
  requirements.md  Requirements and scope
  design.md        Architecture, ER and state diagrams
  week1-review.md  Week 1 review notes
requirements.txt   Pinned dependencies
```

## Documentation

- [Requirements and scope](docs/requirements.md)
- [System design and diagrams](docs/design.md)
- [Week 1 review notes](docs/week1-review.md)

## Known limitations

- Ping checks are not implemented yet; a ping target returns HTTP 501 when checked.
- Checks run only when triggered by hand until the scheduler is added in Week 2.
- Timestamps are stored in UTC.
- SQLite is intended for a prototype, not heavy production load.
