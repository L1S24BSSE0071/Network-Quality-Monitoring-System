"""SQLite database connection and session handling."""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# The default database lives in the project folder, wherever the server is started from
# (a relative path would silently create a second, empty database in the current folder).
# Can be overridden with the NQMS_DATABASE_URL environment variable (used by tests).
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "monitoring.db"
DATABASE_URL = os.getenv("NQMS_DATABASE_URL", "").strip() or f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"

# check_same_thread=False lets FastAPI and the scheduler threads share SQLite.
# pool_size=20 matches MAX_PARALLEL_CHECKS so burst waves of checks reuse connections
# instead of opening, running PRAGMAs on, and closing overflow connections each cycle.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_size=20,
    max_overflow=20,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Allow readers and a writer at the same time, wait briefly on a locked DB,
    and enforce foreign keys (SQLite ignores them unless asked)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# expire_on_commit=False avoids a redundant SELECT after INSERT/UPDATE just to
# read back attributes that are already in memory.
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


_LEGACY_INDEXES = (
    "ix_check_results_target_id",
    "ix_check_results_checked_at",
    "ix_events_target_id",
    "ix_events_occurred_at",
)


def init_db() -> None:
    """Create missing tables and indexes, and remove obsolete single-column indexes.

    create_all only creates missing tables, so indexes added in later versions are
    created explicitly for databases that already exist, and the superseded Week 1
    single-column indexes are dropped so writes do not maintain duplicate B-trees.
    """
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(bind=conn, checkfirst=True)
        for name in _LEGACY_INDEXES:
            conn.exec_driver_sql(f"DROP INDEX IF EXISTS {name}")


def get_db():
    """FastAPI dependency: yield a session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
