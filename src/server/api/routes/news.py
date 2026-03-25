# src/server/api/routes/news.py
"""Thin news API routes.

This module intentionally exposes only two shapes:
- stock-specific news search
- generic news search
"""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, status

from src.server.core.dependencies import Container
from src.server.utils.logger import logger

router = APIRouter(prefix="/api/v1/news", tags=["News"])


@router.get("/stock", summary="获取个股新闻")
async def get_stock_news(
    symbol: str = Query(..., description="资产代码，如 NASDAQ:AAPL / SSE:600519"),
    days_back: int = Query(7, ge=1, le=30, description="回溯天数"),
    limit: int = Query(10, ge=1, le=20, description="结果数量"),
) -> Dict[str, Any]:
    try:
        logger.info("API: get_stock_news", symbol=symbol, days_back=days_back, limit=limit)
        service = Container.news_service()
        return await service.fetch_latest_news(symbol, days_back=days_back, limit=limit)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(f"API error in get_stock_news: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stock news: {str(e)}",
        ) from e


@router.get("/search", summary="搜索新闻")
async def search_news(
    query: str = Query(..., description="搜索关键词"),
    days_back: int = Query(7, ge=1, le=30, description="回溯天数"),
    limit: int = Query(10, ge=1, le=20, description="结果数量"),
) -> Dict[str, List[Dict[str, Any]]]:
    try:
        logger.info("API: search_news", query=query, days_back=days_back, limit=limit)
        service = Container.news_service()
        results = await service.search_news(
            query,
            days_back=days_back,
            max_results=limit,
        )
        return {"query": query, "items": results}
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(f"API error in search_news: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search news: {str(e)}",
        ) from e
