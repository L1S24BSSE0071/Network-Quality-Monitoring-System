"""Shared test setup: a throwaway database and a client without background checking."""
import os
import shutil
import tempfile
from pathlib import Path

# Must be set before the app is imported so the app uses the throwaway database.
_TEST_DIR = tempfile.mkdtemp(prefix="nqms-tests-")
os.environ["NQMS_DATABASE_URL"] = f"sqlite:///{(Path(_TEST_DIR) / 'test.db').as_posix()}"

import pytest
from fastapi.testclient import TestClient

from app import database, scheduler
from app.main import app


@pytest.fixture(scope="session")
def client():
    """API client. The real scheduler is not started, so tests never touch the network."""
    original_start = scheduler.start
    scheduler.start = lambda: None
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        scheduler.start = original_start
        database.engine.dispose()
        shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database(client):
    """Start every test with empty tables."""
    yield
    with database.engine.begin() as connection:
        for table in reversed(database.Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def make_target(client):
    """Create a target through the API and return its JSON. Disabled by default."""

    def _make(**overrides):
        body = {"name": "Example", "host_or_url": "https://example.com", "enabled": False, **overrides}
        response = client.post("/targets", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make
