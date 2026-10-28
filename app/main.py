"""Application entry point."""
from fastapi import FastAPI

app = FastAPI(
    title="Network Quality Monitoring System",
    description="Monitors availability and latency of user-added websites and servers.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}
