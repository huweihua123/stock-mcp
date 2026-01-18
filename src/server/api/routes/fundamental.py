# src/server/api/routes/fundamental.py
"""Fundamental analysis API routes.

Provides RESTful HTTP endpoints for fundamental/financial data.
"""

from fastapi import APIRouter, HTTPException, status, Query
from typing import Dict, Any
from src.server.utils.logger import logger
from src.server.core.dependencies import Container

router = APIRouter(prefix="/api/v1/fundamental", tags=["Fundamental Analysis"])


def _normalize_ticker(symbol: str) -> str:
    """Normalize ticker to internal EXCHANGE:SYMBOL format."""
    # Already in correct format
    if ":" in symbol:
        return symbol.upper()

    symbol = symbol.upper().strip()

    # Detect A-share (Chinese stocks)
    if len(symbol) == 6 and symbol.isdigit():
        # 6开头 = 上交所 (SSE)
        if symbol.startswith("6"):
            return f"SSE:{symbol}"
        # 0或3开头 = 深交所 (SZSE)
        elif symbol.startswith(("0", "3")):
            return f"SZSE:{symbol}"
        # 8开头 = 北交所 (BSE)
        elif symbol.startswith("8"):
            return f"BSE:{symbol}"

    # Default to NASDAQ for US stocks (most common)
    return f"NASDAQ:{symbol}"


@router.post(
    "/report",
    summary="获取财务报告分析",
    description="""
    获取指定股票的财务报告分析
    
    **支持的代码格式:**
    - 美股: `AAPL` 或 `NASDAQ:AAPL`
    - A股: `600519` 或 `SSE:600519`
    
    **返回数据:**
    - 营收、利润、现金流等关键财务指标
    - 财务健康度评估
    """,
)
async def get_financial_report(
    symbol: str = Query(..., description="股票代码")
) -> Dict[str, Any]:
    """获取财务报告分析"""
    try:
        # 标准化 ticker
        ticker = _normalize_ticker(symbol)
        
        logger.info(
            "API: get_financial_report called",
            symbol=symbol,
            normalized_ticker=ticker
        )
        
        service = Container.fundamental_service()
        result = await service.get_fundamental_analysis(ticker)
        
        return result
        
    except Exception as e:
        logger.error(f"API error in get_financial_report: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get financial report: {str(e)}"
        )


@router.post(
    "/ratios",
    summary="获取财务比率",
    description="获取关键财务比率（PE、PB、ROE等）",
)
async def get_financial_ratios(
    symbol: str = Query(..., description="股票代码")
) -> Dict[str, Any]:
    """获取财务比率"""
    try:
        ticker = _normalize_ticker(symbol)
        
        logger.info("API: get_financial_ratios called", symbol=symbol)
        
        service = Container.fundamental_service()
        
        # 获取完整分析并提取比率部分
        result = await service.get_fundamental_analysis(ticker)
        
        # 如果有比率数据，返回它；否则返回整体结果
        if "ratios" in result:
            return {
                "symbol": ticker,
                "ratios": result["ratios"]
            }
        
        return result
        
    except Exception as e:
        logger.error(f"API error in get_financial_ratios: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get financial ratios: {str(e)}"
        )
