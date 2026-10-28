# System Design

This document contains the database schema (ER diagram) and the system architecture. The diagrams are written in Mermaid, which GitHub renders automatically.

## 1. Architecture

The system has four components. The **scheduler** triggers a check for each enabled target at its own interval. The **checker** performs the HTTP check and measures the result. The **API** exposes targets, results, events and summaries. The **dashboard** reads from the API and displays status and history. All data is stored in SQLite.

```mermaid
flowchart LR
    User(["User / Reviewer"])
    Target[("Monitored website or server")]

    subgraph App["FastAPI application"]
        Dashboard["Dashboard (HTML, CSS, JavaScript, Chart.js)"]
        API["REST API (routers)"]
        Scheduler["Scheduler (APScheduler)"]
        Checker["Checker (httpx)"]
        Evaluator["Threshold evaluator"]
    end

    DB[("SQLite database")]

    User --> Dashboard
    Dashboard -->|"HTTP requests"| API
    API -->|"read and write targets, read results and events"| DB
    API -->|"manual check"| Checker
    Scheduler -->|"runs each target at its interval"| Checker
    Checker -->|"HTTP request with timeout"| Target
    Checker -->|"store check result"| DB
    Checker --> Evaluator
    Evaluator -->|"event on state change"| DB
```

### Components and status

| Component | Module | Responsibility | Status |
|-----------|--------|----------------|--------|
| API | `app/main.py`, `app/routers/targets.py` | Target CRUD, manual check, results listing, readable validation errors | Built (Week 1) |
| Data layer | `app/database.py`, `app/models.py` | SQLite connection and ORM tables | Built (Week 1) |
| Validation | `app/schemas.py`, `app/errors.py` | Input rules and response shapes | Built (Week 1) |
| Checker | `app/checker.py` | HTTP check with timeout and error handling; stores a result | Built (Week 1) |
| Scheduler | `app/scheduler.py` | Runs checks in the background per target interval; follows target add, edit and delete | Built (Week 2) |
| Threshold evaluator | planned | Compares latency to thresholds and creates events on state change | Week 2 |
| Dashboard | planned | Status, history chart, forms, events, report export | Week 3 |

### Check flow

```mermaid
sequenceDiagram
    participant S as Scheduler or API
    participant C as Checker
    participant T as Target
    participant D as Database
    participant E as Evaluator

    S->>C: check(target)
    C->>T: HTTP GET (timeout 10 s)
    alt response received
        T-->>C: status code and response time
    else timeout, DNS, TLS or connection error
        T--xC: error
    end
    C->>D: store check_results row
    C->>E: evaluate(target, result)
    E->>D: store event only if the state changed
```

The manual check endpoint (`POST /targets/{id}/check`) uses the same checker. The evaluator step is added in Week 2.

## 2. Database schema

```mermaid
erDiagram
    TARGETS ||--o{ CHECK_RESULTS : "has"
    TARGETS ||--o{ EVENTS : "has"

    TARGETS {
        int id PK
        string name
        string host_or_url
        string check_type "http or ping"
        int interval_seconds "10 to 3600"
        int warning_ms
        int critical_ms
        bool enabled
        datetime created_at
    }

    CHECK_RESULTS {
        int id PK
        int target_id FK
        datetime checked_at
        bool is_up
        float latency_ms "null if the check failed"
        int http_status "null if no response"
        string error_message "null if successful"
    }

    EVENTS {
        int id PK
        int target_id FK
        datetime occurred_at
        string severity "warning, critical or recovered"
        string message
        float latency_ms
    }
```

### Table notes

- **targets**: one row per monitored website or server. `warning_ms` must be lower than `critical_ms`.
- **check_results**: one row per check, successful or not. A failed check has `is_up = false`, no latency, and an `error_message`. A response with status 400 or above is also `is_up = false`, but keeps its latency and `http_status`.
- **events**: one row per state change, not per check.
- Deleting a target deletes its check results and events (cascade).
- `check_results.target_id`, `check_results.checked_at`, `events.target_id` and `events.occurred_at` are indexed, since history and uptime queries filter on them.
- Timestamps are stored in UTC.

## 3. Target state model

Events are created only when the state changes, so repeated failures produce one event, not one per check.

```mermaid
stateDiagram-v2
    [*] --> Normal
    Normal --> Warning : latency above warning_ms
    Normal --> Critical : down or latency above critical_ms
    Warning --> Critical : down or latency above critical_ms
    Warning --> Normal : latency back below warning_ms
    Critical --> Warning : up, latency between warning_ms and critical_ms
    Critical --> Normal : up, latency below warning_ms
```

Entering **Warning** or **Critical** creates a `warning` or `critical` event. Returning to **Normal** from either creates a `recovered` event. Moving from Critical to Warning is treated as an improvement and also creates a `warning` event, so the log shows the target is still degraded. The exact rules are finalised with the evaluator in Week 2.
