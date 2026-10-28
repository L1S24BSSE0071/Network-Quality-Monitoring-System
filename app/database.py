"""SQLite database connection and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = "sqlite:///./monitoring.db"

# check_same_thread=False lets FastAPI and (later) the scheduler threads share SQLite.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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
