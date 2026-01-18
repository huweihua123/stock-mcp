# src/server/api/routes/money_flow.py
"""Money flow analysis API routes.

Provides RESTful HTTP endpoints for money flow and capital flow data.
"""

from fastapi import APIRouter, HTTPException, status, Query
from typing import Dict, Any, List, Optional
from src.server.utils.logger import logger
from src.server.core.dependencies import Container

router = APIRouter(prefix="/api/v1/money-flow", tags=["Money Flow Analysis"])


def _normalize_ticker(symbol: str) -> str:
    """Normalize ticker to internal EXCHANGE:SYMBOL format."""
    if ":" in symbol:
        return symbol.upper()

    if "." in symbol:
        code, suffix = symbol.split(".", 1)
        suffix = suffix.upper()
        if suffix == "SH":
            return f"SSE:{code}"
        elif suffix == "SZ":
            return f"SZSE:{code}"
        elif suffix == "BJ":
            return f"BSE:{code}"
        return symbol

    symbol = symbol.upper().strip()
    if len(symbol) == 6 and symbol.isdigit():
        if symbol.startswith("6"):
            return f"SSE:{symbol}"
        elif symbol.startswith(("0", "3")):
            return f"SZSE:{symbol}"
        elif symbol.startswith("8"):
            return f"BSE:{symbol}"

    return f"NASDAQ:{symbol}"


@router.get(
    "/stock/{symbol}",
    summary="获取个股资金流向",
    description="""
    获取个股主力资金和散户资金的流入流出情况
    
    **支持的代码格式:**
    - A股: `600519`, `600519.SH`, `SSE:600519`
    - A股: `000001`, `000001.SZ`, `SZSE:000001`
    
    **返回数据:**
    - 主力资金净流入/流出
    - 散户资金净流入/流出
    - 资金流向趋势分析
    """,
)
async def get_stock_money_flow(
    symbol: str, days: int = Query(20, ge=1, le=90, description="获取最近N天数据")
) -> Dict[str, Any]:
    """获取个股资金流向数据"""
    try:
        ticker = _normalize_ticker(symbol)

        logger.info(
            "API: get_stock_money_flow called",
            symbol=symbol,
            normalized_ticker=ticker,
            days=days,
        )

        service = Container.money_flow_service()
        result = await service.get_money_flow(ticker, days)

        return {"code": 0, "message": "success", "data": result}

    except Exception as e:
        logger.error(f"API error in get_stock_money_flow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get money flow: {str(e)}",
        )


@router.get(
    "/north-bound",
    summary="获取北向资金流向",
    description="""
    获取北向资金(沪深港通)流入A股市场的情况
    
    **数据说明:**
    - 沪股通: 香港投资者买卖上交所股票
    - 深股通: 香港投资者买卖深交所股票
    - 北向资金被称为"聪明钱"，对市场有重要参考价值
    
    **返回数据:**
    - 每日沪股通/深股通净流入
    - 北向资金合计
    - 流向趋势分析
    """,
)
async def get_north_bound_flow(
    days: int = Query(30, ge=1, le=120, description="获取最近N天数据")
) -> Dict[str, Any]:
    """获取北向资金流向数据"""
    try:
        logger.info("API: get_north_bound_flow called", days=days)

        service = Container.money_flow_service()
        result = await service.get_north_bound_flow(days)

        return {"code": 0, "message": "success", "data": result}

    except Exception as e:
        logger.error(f"API error in get_north_bound_flow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get north bound flow: {str(e)}",
        )


@router.get(
    "/chip-distribution/{symbol}",
    summary="获取筹码分布数据",
    description="""
    获取个股的筹码分布和成本分析数据
    
    **数据说明:**
    - 筹码分布反映投资者持仓成本分布
    - 获利比例显示当前价格下的盈利投资者占比
    - 成本集中度反映主力筹码的集中程度
    
    **返回数据:**
    - 筹码分布历史
    - 获利比例
    - 成本集中度指标
    """,
)
async def get_chip_distribution(
    symbol: str, days: int = Query(30, ge=1, le=90, description="获取最近N天数据")
) -> Dict[str, Any]:
    """获取筹码分布数据"""
    try:
        ticker = _normalize_ticker(symbol)

        logger.info(
            "API: get_chip_distribution called",
            symbol=symbol,
            normalized_ticker=ticker,
            days=days,
        )

        # 直接调用 adapter_manager
        adapter_manager = Container.adapter_manager()
        result = await adapter_manager.get_chip_distribution(ticker, days)

        return {"code": 0, "message": "success", "data": result}

    except Exception as e:
        logger.error(f"API error in get_chip_distribution: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get chip distribution: {str(e)}",
        )


@router.get(
    "/macro-indicators",
    summary="获取宏观经济指标",
    description="""
    获取影响股市的宏观经济数据
    
    **支持的指标:**
    - CPI: 消费者价格指数
    - PPI: 生产者价格指数
    - M2: 货币供应量
    - GDP: 国内生产总值
    
    **返回数据:**
    - 各指标的历史数据
    - 同比增长率
    - 最新值
    """,
)
async def get_macro_indicators(
    indicators: Optional[str] = Query(
        None, description="指标列表，逗号分隔，如: CPI,PPI,M2。不提供则返回所有"
    )
) -> Dict[str, Any]:
    """获取宏观经济指标"""
    try:
        indicator_list = None
        if indicators:
            indicator_list = [i.strip().upper() for i in indicators.split(",")]

        logger.info("API: get_macro_indicators called", indicators=indicator_list)

        adapter_manager = Container.adapter_manager()
        result = await adapter_manager.get_macro_data(indicator_list)

        return {"code": 0, "message": "success", "data": result}

    except Exception as e:
        logger.error(f"API error in get_macro_indicators: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get macro indicators: {str(e)}",
        )
