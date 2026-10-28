"""SQLite database connection and session handling."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Can be overridden with the NQMS_DATABASE_URL environment variable (used by tests).
DATABASE_URL = os.getenv("NQMS_DATABASE_URL", "sqlite:///./monitoring.db")

# check_same_thread=False lets FastAPI and the scheduler threads share SQLite.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Allow readers and a writer at the same time and wait briefly on a locked DB."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def get_db():
    """FastAPI dependency: yield a session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
