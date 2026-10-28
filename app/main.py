"""Application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers the tables with Base)
from app.database import Base, engine
from app.errors import format_errors
from app.routers import targets


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

app.include_router(targets.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return validation problems as a simple list of field + message."""
    return JSONResponse(status_code=422, content={"detail": format_errors(exc.errors())})


@app.get("/health", tags=["system"])
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}
