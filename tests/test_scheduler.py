"""Scheduler: jobs follow target changes and checks are stored (no real network access)."""
import time

import pytest

from app import checker, scheduler


# Captured at import time, before the `client` fixture replaces scheduler.start with a no-op.
_REAL_START = scheduler.start


@pytest.fixture
def running_scheduler(client, monkeypatch):
    """Start the real scheduler with a fake, instant check instead of network access."""
    monkeypatch.setattr(
        checker, "perform_http_check",
        lambda url, **kw: checker.CheckOutcome(is_up=True, latency_ms=1.0, http_status=200),
    )
    scheduler.shutdown()
    scheduler.start = _REAL_START
    scheduler.start()
    yield
    scheduler.shutdown()
    scheduler.start = lambda: None

def job_ids():
    return {job.id for job in scheduler._scheduler.get_jobs()}


def wait_for(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def result_count(client, target_id):
    return len(client.get(f"/targets/{target_id}/results?limit=1000").json())


def test_enabled_http_target_is_scheduled_and_checked_immediately(client, make_target, running_scheduler):
    target = make_target(enabled=True, interval_seconds=3600)
    assert f"target-{target['id']}" in job_ids()
    assert wait_for(lambda: result_count(client, target["id"]) == 1)


def test_disabled_and_ping_targets_are_not_scheduled(client, make_target, running_scheduler):
    disabled = make_target(enabled=False)
    ping = make_target(enabled=True, host_or_url="8.8.8.8", check_type="ping")
    assert f"target-{disabled['id']}" not in job_ids()
    assert f"target-{ping['id']}" not in job_ids()


def test_disable_enable_and_delete_update_the_schedule(client, make_target, running_scheduler):
    target = make_target(enabled=True, interval_seconds=3600)
    job = f"target-{target['id']}"
    assert wait_for(lambda: job in job_ids())

    client.patch(f"/targets/{target['id']}", json={"enabled": False})
    assert job not in job_ids()
    client.patch(f"/targets/{target['id']}", json={"enabled": True})
    assert job in job_ids()
    client.delete(f"/targets/{target['id']}")
    assert job not in job_ids()


def test_interval_edit_reschedules(client, make_target, running_scheduler):
    target = make_target(enabled=True, interval_seconds=3600)
    job = f"target-{target['id']}"
    assert scheduler._scheduler.get_job(job).trigger.interval.total_seconds() == 3600
    client.patch(f"/targets/{target['id']}", json={"interval_seconds": 30})
    assert scheduler._scheduler.get_job(job).trigger.interval.total_seconds() == 30


def test_rename_does_not_trigger_a_new_check(client, make_target, running_scheduler):
    """Regression: every edit used to restart the job and run an extra check."""
    target = make_target(enabled=True, interval_seconds=3600)
    assert wait_for(lambda: result_count(client, target["id"]) == 1)
    client.patch(f"/targets/{target['id']}", json={"name": "Renamed", "warning_ms": 200})
    client.patch(f"/targets/{target['id']}", json={"interval_seconds": 3600, "enabled": True})
    client.patch(f"/targets/{target['id']}", json={})
    time.sleep(0.5)
    assert result_count(client, target["id"]) == 1


def test_check_failure_does_not_break_the_scheduler(client, make_target, running_scheduler, monkeypatch):
    def explode(url, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(checker, "perform_http_check", explode)
    target = make_target(enabled=True, interval_seconds=3600)
    time.sleep(0.5)  # the job ran and raised; it must have been swallowed and logged
    assert scheduler.is_running()
    assert f"target-{target['id']}" in job_ids()


def test_job_for_a_deleted_target_removes_itself(client, make_target, running_scheduler):
    from app import database

    target = make_target(enabled=True, interval_seconds=3600)
    job = f"target-{target['id']}"
    assert wait_for(lambda: job in job_ids())
    client.delete(f"/targets/{target['id']}")
    scheduler.run_scheduled_check(target["id"])  # must not raise and must leave no job
    assert job not in job_ids()
    assert database.engine.pool.checkedout() == 0


def test_sync_applies_the_latest_stored_state_whatever_order_calls_arrive(client, make_target, running_scheduler):
    """Regression: sync used the caller's possibly stale copy, so two simultaneous edits
    could leave an enabled target without a job."""
    from app import database
    from app.models import Target

    target = make_target(enabled=False, interval_seconds=3600)
    job = f"target-{target['id']}"

    def set_enabled(value):
        with database.SessionLocal() as db:
            db.get(Target, target["id"]).enabled = value
            db.commit()

    set_enabled(True)
    scheduler.sync_target(target["id"])
    assert job in job_ids()
    set_enabled(False)
    scheduler.sync_target(target["id"])
    assert job not in job_ids()
    set_enabled(True)
    scheduler.sync_target(target["id"])
    scheduler.sync_target(target["id"])  # repeating it changes nothing
    assert job in job_ids() and len(job_ids()) == 1


def test_sync_of_a_missing_target_is_harmless(client, running_scheduler):
    scheduler.sync_target(424242)
    assert not job_ids()


def test_concurrent_edits_leave_the_schedule_matching_the_database(client, make_target, running_scheduler):
    from concurrent.futures import ThreadPoolExecutor
    import random

    ids = [make_target(enabled=True, interval_seconds=3600, name=f"t{i}")["id"] for i in range(6)]

    def hammer(_):
        for _ in range(25):
            client.patch(f"/targets/{random.choice(ids)}", json={"enabled": random.random() < 0.5})

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(hammer, range(8)))

    stored = {t["id"]: t["enabled"] for t in client.get("/targets").json()}
    assert {i for i in ids if stored[i]} == {int(j.split("-")[1]) for j in job_ids()}
