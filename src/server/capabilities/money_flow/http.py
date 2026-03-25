from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.server.capabilities.money_flow.service import get_money_flow_capability_service
from src.server.runtime.models import RuntimeContext
from src.server.utils.logger import logger


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/money-flow", tags=["Money Flow"])
    service = get_money_flow_capability_service(runtime)

    @router.get("/stock/{symbol}", summary="获取个股资金流向")
    async def get_stock_money_flow(symbol: str, days: int = Query(20, ge=1, le=90)):
        try:
            logger.info("Capability API: get_stock_money_flow", symbol=symbol, days=days)
            return {"code": 0, "message": "success", "data": await service.get_money_flow(symbol, days)}
        except Exception as exc:
            logger.error(f"Capability API error in get_stock_money_flow: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get money flow: {exc}") from exc

    @router.get("/north-bound", summary="获取北向资金流向")
    async def get_north_bound_flow(days: int = Query(30, ge=1, le=120)):
        try:
            logger.info("Capability API: get_north_bound_flow", days=days)
            return {"code": 0, "message": "success", "data": await service.get_north_bound_flow(days)}
        except Exception as exc:
            logger.error(f"Capability API error in get_north_bound_flow: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get north bound flow: {exc}") from exc

    @router.get("/chip-distribution/{symbol}", summary="获取筹码分布数据")
    async def get_chip_distribution(symbol: str, days: int = Query(30, ge=1, le=90)):
        try:
            logger.info("Capability API: get_chip_distribution", symbol=symbol, days=days)
            return {"code": 0, "message": "success", "data": await service.get_chip_distribution(symbol, days)}
        except Exception as exc:
            logger.error(f"Capability API error in get_chip_distribution: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get chip distribution: {exc}") from exc

    return router
