"""Application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers the tables with Base)
from app import scheduler
from app.database import init_db
from app.errors import format_errors
from app.routers import targets

# Show the scheduler's messages (uvicorn only configures its own loggers), but keep
# the libraries quiet: httpx and APScheduler would otherwise log every single check.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
for _noisy in ("httpx", "httpcore", "apscheduler"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the tables, start the background scheduler, and stop it on shutdown."""
    init_db()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="Network Quality Monitoring System",
    description="Monitors availability and latency of user-added websites and servers.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(targets.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return validation problems as a simple list of field + message."""
    return JSONResponse(status_code=422, content={"detail": format_errors(exc.errors())})


@app.get("/health", tags=["system"])
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}
