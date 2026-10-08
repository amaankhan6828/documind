"""DocuMind API entry point."""

from fastapi import FastAPI

from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.documents import router as documents_router
from backend.app.api.v1.jobs import router as jobs_router

app = FastAPI(title="DocuMind API", version="0.1.0")
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(jobs_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Return a simple liveness response."""
    return {"status": "ok"}
