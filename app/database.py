"""SQLite database connection and session handling."""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# The default database lives in the project folder, wherever the server is started from
# (a relative path would silently create a second, empty database in the current folder).
# Can be overridden with the NQMS_DATABASE_URL environment variable (used by tests).
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "monitoring.db"
DATABASE_URL = os.getenv("NQMS_DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}")

# check_same_thread=False lets FastAPI and the scheduler threads share SQLite.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


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


def init_db() -> None:
    """Create missing tables and indexes.

    create_all only creates missing tables, so indexes added in later versions are
    created explicitly for databases that already exist.
    """
    Base.metadata.create_all(bind=engine)
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


def get_db():
    """FastAPI dependency: yield a session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
