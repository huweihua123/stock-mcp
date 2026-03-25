from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.server.capabilities.news.schemas import (
    NewsSearchResponse,
    NewsSentimentSummary,
    StockNewsResponse,
)
from src.server.capabilities.news.service import get_news_capability_service
from src.server.runtime.models import RuntimeContext
from src.server.utils.logger import logger


def build_router(runtime: RuntimeContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/news", tags=["News"])
    service = get_news_capability_service(runtime)

    @router.get("/stock", summary="获取个股新闻", response_model=StockNewsResponse)
    async def get_stock_news(
        symbol: str = Query(..., description="资产代码，如 NASDAQ:AAPL / SSE:600519"),
        days_back: int = Query(7, ge=1, le=30, description="回溯天数"),
        limit: int = Query(10, ge=1, le=20, description="结果数量"),
    ) -> StockNewsResponse:
        try:
            logger.info("Capability API: get_stock_news", symbol=symbol, days_back=days_back, limit=limit)
            return StockNewsResponse.model_validate(
                await service.get_stock_news(symbol, days_back=days_back, limit=limit)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(f"Capability API error in get_stock_news: {exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch stock news: {exc}",
            ) from exc

    @router.get("/search", summary="搜索新闻", response_model=NewsSearchResponse)
    async def search_news(
        query: str = Query(..., description="搜索关键词"),
        days_back: int = Query(7, ge=1, le=30, description="回溯天数"),
        limit: int = Query(10, ge=1, le=20, description="结果数量"),
    ) -> NewsSearchResponse:
        try:
            logger.info("Capability API: search_news", query=query, days_back=days_back, limit=limit)
            return NewsSearchResponse.model_validate(
                await service.search_news(query, days_back=days_back, limit=limit)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(f"Capability API error in search_news: {exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to search news: {exc}",
            ) from exc

    @router.get("/sentiment/us", summary="获取美股新闻情感", response_model=NewsSentimentSummary)
    async def get_us_news_sentiment(
        symbol: str = Query(..., description="美股代码，如 NASDAQ:AAPL"),
        days_back: int = Query(3, ge=1, le=14, description="回溯天数"),
    ) -> NewsSentimentSummary:
        try:
            logger.info("Capability API: get_us_news_sentiment", symbol=symbol, days_back=days_back)
            return NewsSentimentSummary.model_validate(
                await service.get_us_news_sentiment(symbol, days_back=days_back)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(f"Capability API error in get_us_news_sentiment: {exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to analyze news sentiment: {exc}",
            ) from exc

    return router
