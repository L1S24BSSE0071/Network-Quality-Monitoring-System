"""Application entry point."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (registers the tables with Base)
from app import scheduler
from app.database import init_db
from app.errors import format_errors
from app.routers import targets

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<circle cx="50" cy="50" r="46" fill="#1f3a5f"/>'
    '<circle cx="50" cy="50" r="20" fill="#34d399"/>'
    "</svg>"
)

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
app.include_router(targets.events_router)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return validation problems as a simple list of field + message."""
    return JSONResponse(status_code=422, content={"detail": format_errors(exc.errors())})


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard():
    """Serve the monitoring dashboard web page."""
    return FileResponse(_INDEX_HTML, headers={"Cache-Control": "no-cache"})


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon():
    """Serve the tab icon for direct browser requests."""
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/health", tags=["system"])
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}
