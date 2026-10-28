"""Application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  (registers the tables with Base)
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the database tables on startup if they do not exist yet."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Network Quality Monitoring System",
    description="Monitors availability and latency of user-added websites and servers.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}
