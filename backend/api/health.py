"""
health.py - Health check route (Phase 0, Task 4).

Used by operators, monitoring tools and the dashboard to confirm the
backend is up and responding.
"""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from backend.config import get_settings
from backend.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
)
async def health(request: Request) -> HealthResponse:
    """Return basic service status, version and uptime."""
    settings = get_settings()
    started_at: float = request.app.state.started_at

    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - started_at, 2),
        timestamp=datetime.now(timezone.utc),
    )