# src/server/api/routes/news.py
"""News API routes.

Provides RESTful HTTP endpoints for stock and market news.
"""

from fastapi import APIRouter, HTTPException, status, Query
from typing import Dict, Any, List
from src.server.utils.logger import logger
from src.server.core.dependencies import Container

router = APIRouter(prefix="/api/v1/news", tags=["News"])


@router.get(
    "/stock",
    summary="获取个股新闻",
    description="""
    获取指定股票的相关新闻
    
    **参数说明:**
    - `symbol`: 资产代码 (格式: EXCHANGE:SYMBOL 或纯代码)
    - `days_back`: 回溯天数 (1-30)
    
    **返回数据:**
    - 新闻标题、摘要、链接、发布时间等
    """,
)
async def get_stock_news(
    symbol: str = Query(..., description="资产代码 (如: NASDAQ:AAPL, SSE:600519)"),
    days_back: int = Query(7, ge=1, le=30, description="回溯天数")
) -> Dict[str, Any]:
    """获取个股新闻"""
    try:
        logger.info(
            "API: get_stock_news called",
            symbol=symbol,
            days_back=days_back
        )
        
        service = Container.news_service()
        result = await service.fetch_latest_news(symbol, days_back)
        
        return result
        
    except Exception as e:
        logger.error(f"API error in get_stock_news: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stock news: {str(e)}"
        )


@router.get(
    "/market",
    summary="获取市场新闻",
    description="获取市场整体新闻动态",
)
async def get_market_news(
    category: str = Query("general", description="新闻类别 (general, breaking, financial)"),
    limit: int = Query(20, ge=1, le=50, description="返回数量")
) -> List[Dict[str, Any]]:
    """获取市场新闻"""
    try:
        logger.info(
            "API: get_market_news called",
            category=category,
            limit=limit
        )
        
        service = Container.news_service()
        
        if category == "breaking":
            result = await service.get_breaking_news()
        elif category == "financial":
            result = await service.get_financial_news(None, None)
        else:
            # 默认通用新闻
            result = await service.web_search("stock market news today")
        
        # 限制返回数量
        if isinstance(result, list):
            return result[:limit]
        return result
        
    except Exception as e:
        logger.error(f"API error in get_market_news: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch market news: {str(e)}"
        )
