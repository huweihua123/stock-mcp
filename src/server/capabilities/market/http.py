from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.server.capabilities.market.schemas import (
    GetHistoricalPricesRequest,
    GetMultiplePricesRequest,
    MarketReportResponse,
)
from src.server.capabilities.market.service import get_market_capability_service
from src.server.runtime.models import RuntimeContext
from src.server.utils.logger import logger


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/market", tags=["Market"])
    service = get_market_capability_service(runtime)

    @router.post("/prices/batch", summary="批量获取实时价格")
    async def get_multiple_prices(request: GetMultiplePricesRequest):
        try:
            logger.info("Capability API: get_multiple_prices", count=len(request.tickers))
            return await service.get_multiple_prices(request.tickers)
        except Exception as exc:
            logger.error(f"Capability API error in get_multiple_prices: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch prices: {exc}") from exc

    @router.post("/prices/history", summary="获取历史价格/K线数据")
    async def get_historical_prices(request: GetHistoricalPricesRequest):
        try:
            logger.info("Capability API: get_historical_prices", symbol=request.symbol, period=request.period, interval=request.interval)
            return await service.get_historical_prices(request.symbol, request.period, request.interval)
        except Exception as exc:
            logger.error(f"Capability API error in get_historical_prices: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch historical prices: {exc}") from exc

    @router.get("/asset/info", summary="获取资产详情")
    async def get_asset_info(symbol: str = Query(..., description="资产代码")):
        try:
            logger.info("Capability API: get_asset_info", symbol=symbol)
            asset = await service.get_asset_info(symbol)
            return asset or {"error": f"Asset not found: {symbol}"}
        except Exception as exc:
            logger.error(f"Capability API error in get_asset_info: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get asset info: {exc}") from exc

    @router.get("/report", summary="获取市场综合报告", response_model=MarketReportResponse)
    async def get_market_report(symbol: str = Query(..., description="资产代码")):
        try:
            logger.info("Capability API: get_market_report", symbol=symbol)
            return MarketReportResponse.model_validate(await service.get_market_report(symbol))
        except Exception as exc:
            logger.error(f"Capability API error in get_market_report: {exc}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get market report: {exc}") from exc

    return router
