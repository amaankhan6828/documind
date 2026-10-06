"""DocuMind API entry point."""

from fastapi import FastAPI

app = FastAPI(title="DocuMind API", version="0.1.0")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Return a simple liveness response."""
    return {"status": "ok"}
