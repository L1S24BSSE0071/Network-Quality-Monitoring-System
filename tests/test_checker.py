"""HTTP checker and result storage (no real network access)."""
import httpx
import pytest

from app import checker, database
from app.models import CheckResult, Target


def check(handler, **kwargs):
    return checker.perform_http_check("http://site.test/", transport=httpx.MockTransport(handler), **kwargs)


def test_success():
    outcome = check(lambda request: httpx.Response(200))
    assert outcome.is_up and outcome.http_status == 200
    assert outcome.latency_ms is not None and outcome.error_message is None


def test_redirect_is_followed():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "http://site.test/final"})
        return httpx.Response(200)

    outcome = check(handler)
    assert outcome.is_up and outcome.http_status == 200


@pytest.mark.parametrize("status, text", [(404, "HTTP 404 Not Found"), (503, "HTTP 503 Service Unavailable")])
def test_error_status_is_down_but_keeps_latency(status, text):
    outcome = check(lambda request: httpx.Response(status))
    assert not outcome.is_up
    assert outcome.http_status == status and outcome.latency_ms is not None
    assert outcome.error_message == text


def test_timeout_is_a_failure_not_an_exception():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    outcome = check(handler, timeout_seconds=3)
    assert not outcome.is_up and outcome.latency_ms is None
    assert outcome.error_message == "Timed out after 3 seconds"


def test_redirect_loop():
    outcome = check(lambda request: httpx.Response(302, headers={"location": str(request.url)}))
    assert not outcome.is_up and outcome.error_message == "Too many redirects"


def test_unexpected_error_never_escapes():
    def handler(request):
        raise RuntimeError("boom")

    outcome = check(handler)
    assert not outcome.is_up and "boom" in outcome.error_message


def test_body_is_not_downloaded():
    """Response time is time-to-headers: a body that fails or is huge must not matter."""

    class FailingBody(httpx.SyncByteStream):
        def __iter__(self):
            raise RuntimeError("body must not be read")
            yield b""

    outcome = check(lambda request: httpx.Response(200, stream=FailingBody()))
    assert outcome.is_up


def test_manual_check_is_stored_with_utc_timestamp(client, make_target, monkeypatch):
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: checker.CheckOutcome(is_up=True, latency_ms=12.5, http_status=200),
    )
    target = make_target()
    response = client.post(f"/targets/{target['id']}/check")
    assert response.status_code == 201
    body = response.json()
    assert body["is_up"] and body["latency_ms"] == 12.5 and body["http_status"] == 200
    assert body["checked_at"].endswith("Z")  # timezone-aware UTC, not a naive time

    results = client.get(f"/targets/{target['id']}/results").json()
    assert [r["id"] for r in results] == [body["id"]]
    assert client.get(f"/targets/{target['id']}").json()["created_at"].endswith("Z")


def test_failed_check_is_stored(client, make_target, monkeypatch):
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: checker.CheckOutcome(is_up=False, error_message="DNS lookup failed"),
    )
    target = make_target()
    body = client.post(f"/targets/{target['id']}/check").json()
    assert body["is_up"] is False and body["latency_ms"] is None
    assert body["error_message"] == "DNS lookup failed"


def test_ping_check_is_not_supported_yet(client, make_target):
    target = make_target(host_or_url="8.8.8.8", check_type="ping")
    assert client.post(f"/targets/{target['id']}/check").status_code == 501


def test_results_are_newest_first_and_limited(client, make_target, monkeypatch):
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: checker.CheckOutcome(is_up=True, latency_ms=1.0, http_status=200),
    )
    target = make_target()
    ids = [client.post(f"/targets/{target['id']}/check").json()["id"] for _ in range(3)]
    listed = [r["id"] for r in client.get(f"/targets/{target['id']}/results").json()]
    assert listed == ids[::-1]
    assert len(client.get(f"/targets/{target['id']}/results?limit=2").json()) == 2


def test_deleting_a_target_removes_its_results(client, make_target, monkeypatch):
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: checker.CheckOutcome(is_up=True, latency_ms=1.0, http_status=200),
    )
    target = make_target()
    client.post(f"/targets/{target['id']}/check")
    client.delete(f"/targets/{target['id']}")
    with database.SessionLocal() as db:
        assert db.query(CheckResult).count() == 0


def test_target_deleted_during_a_check_leaves_no_orphan(client, make_target, monkeypatch):
    """Regression: a check finishing after its target was deleted used to leave an orphan row."""
    target = make_target()

    def slow_check_during_which_target_is_deleted(url, **kw):
        client.delete(f"/targets/{target['id']}")
        return checker.CheckOutcome(is_up=True, latency_ms=1.0, http_status=200)

    monkeypatch.setattr(checker, "perform_http_check", slow_check_during_which_target_is_deleted)
    with database.SessionLocal() as db:
        stored = checker.run_check_and_store(db, db.get(Target, target["id"]))
    assert stored is None
    with database.SessionLocal() as db:
        assert db.query(CheckResult).count() == 0

    # and through the manual endpoint
    target2 = make_target(name="Second")
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: (client.delete(f"/targets/{target2['id']}"),
                           checker.CheckOutcome(is_up=True, latency_ms=1.0, http_status=200))[1],
    )
    assert client.post(f"/targets/{target2['id']}/check").status_code == 404
