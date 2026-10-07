"""
health.py - Response schema for the health endpoint (Phase 0, Task 4).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by GET /health."""

    status: Literal["ok"] = Field(description="Always 'ok' when the service is responding.")
    app_name: str = Field(description="Application name from settings.")
    version: str = Field(description="Application version from settings.")
    environment: str = Field(description="Deployment environment (development, testing, production).")
    uptime_seconds: float = Field(description="Seconds since the application started.")
    timestamp: datetime = Field(description="Current server time in UTC.")