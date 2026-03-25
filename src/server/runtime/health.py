"""Runtime health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from src.server.utils.logger import logger


def build_health_router(runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["Health"])
    async def health_check():
        redis_ok = await runtime.container.redis().is_healthy()
        status = "ok" if redis_ok else "degraded"
        logger.info("Health check executed", redis=redis_ok)
        return {
            "status": status,
            "components": {
                "redis": redis_ok,
            },
        }

    return router
