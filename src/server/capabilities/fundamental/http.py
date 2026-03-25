from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.server.capabilities.fundamental.service import get_fundamental_capability_service
from src.server.runtime.models import RuntimeContext
from src.server.utils.logger import logger


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/fundamental", tags=["Fundamental"])
    service = get_fundamental_capability_service(runtime)

    @router.post("/report", summary="获取财务报告分析")
    async def get_financial_report(symbol: str = Query(..., description="股票代码")):
        try:
            logger.info("Capability API: get_financial_report", symbol=symbol)
            return await service.get_fundamental_analysis(symbol)
        except Exception as exc:
            logger.error(f"Capability API error in get_financial_report: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get financial report: {exc}") from exc

    @router.post("/ratios", summary="获取财务比率")
    async def get_financial_ratios(symbol: str = Query(..., description="股票代码")):
        try:
            logger.info("Capability API: get_financial_ratios", symbol=symbol)
            result = await service.get_fundamental_analysis(symbol)
            if "ratios" in result:
                return {"symbol": result.get("ticker", symbol), "ratios": result["ratios"]}
            return result
        except Exception as exc:
            logger.error(f"Capability API error in get_financial_ratios: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get financial ratios: {exc}") from exc

    return router
