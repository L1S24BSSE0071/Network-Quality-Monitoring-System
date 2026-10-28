"""Target API: CRUD, validation and error messages."""
import pytest

VALID = {"name": "Example", "host_or_url": "https://example.com"}


def test_create_applies_defaults(client):
    response = client.post("/targets", json={**VALID, "enabled": False})
    assert response.status_code == 201
    body = response.json()
    assert body["check_type"] == "http"
    assert (body["interval_seconds"], body["warning_ms"], body["critical_ms"]) == (60, 500, 1000)


def test_list_get_edit_delete(client, make_target):
    first = make_target(name="First")
    make_target(name="Second")
    assert [t["name"] for t in client.get("/targets").json()] == ["First", "Second"]
    assert client.get(f"/targets/{first['id']}").json()["name"] == "First"

    edited = client.patch(f"/targets/{first['id']}", json={"name": "Renamed"})
    assert edited.status_code == 200 and edited.json()["name"] == "Renamed"
    assert edited.json()["host_or_url"] == "https://example.com"  # untouched fields stay

    assert client.delete(f"/targets/{first['id']}").status_code == 204
    assert client.get(f"/targets/{first['id']}").status_code == 404
    assert len(client.get("/targets").json()) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"host_or_url": "example.com"},
        {"host_or_url": "ftp://example.com"},
        {"host_or_url": "http://"},
        {"host_or_url": "http://bad_host!.com"},
        {"host_or_url": "http://example.com:99999"},
        {"check_type": "ping", "host_or_url": "http://example.com"},
        {"check_type": "dns"},
        {"interval_seconds": 9},
        {"interval_seconds": 3601},
        {"warning_ms": 1000, "critical_ms": 1000},
        {"warning_ms": 2000, "critical_ms": 1000},
        {"warning_ms": 0},
    ],
)
def test_invalid_input_is_rejected_and_not_stored(client, overrides):
    response = client.post("/targets", json={**VALID, **overrides})
    assert response.status_code == 422
    assert client.get("/targets").json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "DNS", "host_or_url": "8.8.8.8", "check_type": "ping"},
        {"name": "Local", "host_or_url": "http://localhost:8080/health"},
        {"name": "Plain IP", "host_or_url": "http://192.168.1.10"},
    ],
)
def test_valid_variants_are_accepted(client, overrides):
    assert client.post("/targets", json={**VALID, **overrides, "enabled": False}).status_code == 201


def test_error_messages_are_readable(client):
    body = client.post("/targets", json={"name": "", "host_or_url": "https://a.com", "interval_seconds": 5}).json()
    assert {"field": "name", "message": "Must not be empty"} in body["detail"]
    assert {"field": "interval_seconds", "message": "Must be at least 10"} in body["detail"]

    missing = client.post("/targets", json={}).json()["detail"]
    assert {"field": "name", "message": "This field is required"} in missing

    thresholds = client.post("/targets", json={**VALID, "warning_ms": 900, "critical_ms": 100}).json()["detail"]
    assert thresholds == [{"field": "(request)", "message": "warning_ms must be lower than critical_ms"}]


def test_edit_revalidates_against_stored_values(client, make_target):
    target = make_target(warning_ms=100, critical_ms=200)
    response = client.patch(f"/targets/{target['id']}", json={"warning_ms": 5000})
    assert response.status_code == 422
    assert client.get(f"/targets/{target['id']}").json()["warning_ms"] == 100  # unchanged

    response = client.patch(f"/targets/{target['id']}", json={"check_type": "ping"})
    assert response.status_code == 422  # stored value is a URL, not valid for ping


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_unknown_target_is_404(client, method):
    kwargs = {"json": {"name": "x"}} if method == "patch" else {}
    assert getattr(client, method)("/targets/12345", **kwargs).status_code == 404


@pytest.mark.parametrize("bad_id", ["0", "-1", "99999999999999999999", "abc"])
def test_invalid_ids_return_422_not_a_crash(client, bad_id):
    for path in (f"/targets/{bad_id}", f"/targets/{bad_id}/results"):
        assert client.get(path).status_code == 422
    assert client.post(f"/targets/{bad_id}/check").status_code == 422
    assert client.delete(f"/targets/{bad_id}").status_code == 422


def test_results_limit_is_validated(client, make_target):
    target = make_target()
    assert client.get(f"/targets/{target['id']}/results?limit=0").status_code == 422
    assert client.get(f"/targets/{target['id']}/results?limit=1001").status_code == 422


def test_delete_removes_history_and_events_but_not_other_targets(client, make_target):
    from app import database
    from app.models import CheckResult, Event

    doomed, kept = make_target(name="Doomed"), make_target(name="Kept")
    with database.SessionLocal() as db:
        for target in (doomed, kept):
            db.add(CheckResult(target_id=target["id"], is_up=True, latency_ms=1.0, http_status=200))
            db.add(Event(target_id=target["id"], severity="critical", message="down"))
        db.commit()

    assert client.delete(f"/targets/{doomed['id']}").status_code == 204
    with database.SessionLocal() as db:
        assert [r.target_id for r in db.query(CheckResult)] == [kept["id"]]
        assert [e.target_id for e in db.query(Event)] == [kept["id"]]


@pytest.mark.parametrize(
    "host_or_url",
    ["http://[::1]:8000/", "http://m\u00fcnchen.de", "http://example.com./", "HTTP://EXAMPLE.COM", "http://127.0.0.1:8080"],
)
def test_valid_hosts_are_accepted(client, host_or_url):
    assert client.post("/targets", json={**VALID, "host_or_url": host_or_url, "enabled": False}).status_code == 201


@pytest.mark.parametrize(
    "host_or_url",
    [
        "http://256.1.1.1",
        "http://999.999.999.999/",
        "http://1.2.3",
        "http://user:pass@example.com",
        "http://user@example.com",
        "http://-bad.example.com",
        "http://exa mple.com",
    ],
)
def test_invalid_hosts_are_rejected(client, host_or_url):
    assert client.post("/targets", json={**VALID, "host_or_url": host_or_url}).status_code == 422


def test_ping_target_accepts_ips_and_rejects_bad_ones(client):
    ok = {**VALID, "check_type": "ping", "enabled": False}
    assert client.post("/targets", json={**ok, "host_or_url": "8.8.8.8"}).status_code == 201
    assert client.post("/targets", json={**ok, "host_or_url": "::1"}).status_code == 201
    assert client.post("/targets", json={**ok, "host_or_url": "256.256.256.256"}).status_code == 422


def test_unknown_fields_are_rejected_on_create_and_edit(client, make_target):
    response = client.post("/targets", json={**VALID, "nmae": "typo"})
    assert response.status_code == 422
    assert {"field": "nmae", "message": "Unknown field"} in response.json()["detail"]
    assert client.get("/targets").json() == []

    target = make_target()
    response = client.patch(f"/targets/{target['id']}", json={"interval": 30})
    assert response.status_code == 422
    assert client.get(f"/targets/{target['id']}").json()["interval_seconds"] == 60


def test_credentials_in_url_have_a_clear_message(client):
    body = client.post("/targets", json={**VALID, "host_or_url": "http://user:pass@example.com"}).json()
    assert body["detail"] == [{"field": "(request)", "message": "URL must not contain a user name or password"}]
