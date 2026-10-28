# Requirements and Scope

**Project:** Network Quality Monitoring System
**Author:** Zohaib Akmal
**Version:** 1.0 (Week 1)

## 1. Problem statement

Website and server owners often learn about an outage or slowdown only when users complain. This project provides a small monitoring system that regularly checks whether chosen websites and servers are reachable, records how fast they respond, and raises warning and critical events when configurable limits are crossed. A web dashboard shows current status, history and alerts.

## 2. Objectives

1. Measure whether configured hosts and services are reachable.
2. Record latency and availability over time and identify repeated failures.
3. Display current status, historical trends and alerts when thresholds are crossed.

## 3. Scope

### 3.1 In scope

- Manually adding, editing and removing monitoring targets.
- HTTP/HTTPS checks with response-time measurement.
- Scheduled background checks at each target's own interval.
- Storing every check result with a timestamp in a database.
- Warning, critical and recovery events on state changes.
- A REST API and a browser dashboard.
- CSV export of a basic report.

### 3.2 Out of scope (excluded)

- Scanning networks or probing ranges of addresses.
- Monitoring systems the author does not own and that are not public websites.
- Packet-level analysis, bandwidth measurement or traffic capture.
- Automatic discovery of hosts.
- Multi-user accounts and role-based access (optional extra only).
- Email or SMS delivery of alerts (optional extra only).

### 3.3 Authorization statement

Only the author's own systems and publicly available websites are monitored. Every target is added manually by the user. The system never discovers, scans or probes addresses on its own.

## 4. Functional requirements

Priority follows the project plan: **M** = Must have, **S** = Should have, **O** = Optional.

### 4.1 Target management

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | The user can add a target with a name, host or URL, check type, check interval, warning threshold and critical threshold. | M |
| FR-02 | The user can list targets and view a single target. | M |
| FR-03 | The user can edit any field of a target, and can enable or disable it. | M |
| FR-04 | The user can delete a target; its results and events are deleted with it. | M |
| FR-05 | Invalid input is rejected with a clear message and nothing is stored (see 4.5). | M |

### 4.2 Checking

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-06 | The system performs an HTTP check and records up/down, response time and HTTP status. | M |
| FR-07 | A check has a strict timeout. Timeouts, DNS failures, refused connections, invalid URLs and certificate errors are recorded as failures with an error message and never crash the system. | M |
| FR-08 | The user can run a check on demand for any target. | M |
| FR-09 | Checks run automatically in the background, each enabled target at its own interval, without blocking the API. | M |
| FR-10 | Every check result is stored with a timestamp. | M |
| FR-11 | The system supports a ping check where the operating system allows it. | S |

### 4.3 Thresholds and events

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-12 | A target is *normal*, *warning* (latency above the warning threshold), *critical* (latency above the critical threshold, or down) or *recovered*. | M |
| FR-13 | An event is created only when a target changes state, not on every check. | M |
| FR-14 | The event log stores severity, message, latency and time. | M |
| FR-15 | The system shows in-app alert notifications for new warning and critical events. | S |

### 4.4 Dashboard and reporting

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-16 | The dashboard shows, per target, a status indicator, last check time, latest latency and uptime summary. | M |
| FR-17 | The dashboard shows a latency history chart with a selectable time range. | M |
| FR-18 | Targets can be added, edited and removed from the dashboard. | M |
| FR-19 | The dashboard shows a clear empty state when there are no targets. | M |
| FR-20 | Targets can be filtered by status. | S |
| FR-21 | The dashboard shows the event log. | M |
| FR-22 | A CSV report can be exported (target, uptime, average latency, failures). | S |

### 4.5 Validation rules

| ID | Rule |
|----|------|
| VR-01 | The name is required and cannot be blank (maximum 100 characters). |
| VR-02 | For HTTP targets the value must be a well-formed `http://` or `https://` URL with a valid host and port. |
| VR-03 | For ping targets the value must be a host name or IP address (no scheme, port or path). |
| VR-04 | The check interval must be between 10 and 3600 seconds. |
| VR-05 | The warning threshold must be lower than the critical threshold; both must be greater than 0. |
| VR-06 | The check type must be `http` or `ping`. |
| VR-07 | A URL must not contain a user name or password, so credentials are never stored or shown. |
| VR-08 | Unknown field names are rejected, so a misspelled field is reported instead of silently ignored. |

## 5. Non-functional requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-01 | Performance | Slow checks must not block the API or dashboard; checks run in background workers. |
| NFR-02 | Reliability | A failing or unreachable target must never stop checks on other targets. |
| NFR-03 | Accuracy | Measured latency covers the request and response only, not local set-up work. |
| NFR-04 | Usability | Error messages state which field is wrong and why. |
| NFR-05 | Portability | Runs on Windows, Linux and macOS with Python 3.10 or later and no database server to install. |
| NFR-06 | Maintainability | Code is split into database, models, schemas, checker and routers; names are meaningful; configuration is in one place. |
| NFR-07 | Documentation | The API is documented automatically at `/docs`; a README explains installation and use. |
| NFR-08 | Security and ethics | Only manually added own or public targets are checked (see 3.3). |
| NFR-09 | Testability | Threshold and check logic can be tested without real network access. |

## 6. Assumptions and constraints

- Python with FastAPI, SQLite, APScheduler and httpx (see the development plan for the reasons).
- A single user on a single machine; no authentication in the base version.
- SQLite is sufficient for a prototype and is not intended for heavy production load.
- Latency is measured from the machine running the system, so it includes that machine's network path.
- Ping may need operating-system permissions and can behave differently across systems; HTTP is the core check.

## 7. Known limitations

- A state change is detected only at check intervals, so very short outages between checks can be missed.
- Timestamps are stored and returned in UTC.
- Response time is measured until the response headers arrive; the page body is not downloaded.
- The 10-second timeout applies to each stage of a request (connect, wait for response), not to the whole check, and DNS look-ups are not covered by it.
- Check history is not pruned automatically, so the database grows over time.- Ping support depends on the operating system.
