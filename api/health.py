"""Liveness/readiness HTTP endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe: process is up. Does not check dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    """Readiness probe: verifies Redis connectivity."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "redis not initialized"}

    try:
        await redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": f"redis unreachable: {exc}"}

    return {"status": "ready"}
