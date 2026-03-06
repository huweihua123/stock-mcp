# src/server/api/routes/market_data.py
"""Market data API routes.

Provides RESTful HTTP endpoints for:
- Batch price queries
- Technical indicators calculation
- Historical prices (K-line data)
 - Asset info
- Trading signals
- Support/resistance levels
"""

from fastapi import APIRouter, HTTPException, status, Query
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from src.server.utils.logger import logger
from src.server.core.use_cases import market as market_use_cases
from src.server.core.use_cases import technical as technical_use_cases

# 导入请求模型
from src.server.api.models.requests import (
    GetMultiplePricesRequest,
    CalculateTechnicalIndicatorsRequest,
    GetHistoricalPricesRequest,
    GenerateTradingSignalRequest,
    CalculateSupportResistanceRequest,
)

router = APIRouter(prefix="/api/v1/market", tags=["Market Data"])


@router.post(
    "/prices/batch",
    summary="批量获取实时价格",
    description="""
    批量获取多个资产的实时价格数据
    
    **支持的资产类型:**
    - 加密货币: `BINANCE:BTCUSDT`, `BINANCE:ETHUSDT`, `BINANCE:BNBUSDT`
    - 美股: `NASDAQ:AAPL`, `NYSE:TSLA`, `NASDAQ:MSFT`
    - A股: `SSE:600519` (贵州茅台), `SZSE:000001` (平安银行)
    
    **返回数据包含:**
    - 当前价格 (price)
    - 开高低收 (open_price, high_price, low_price, close_price)
    - 成交量 (volume)
    - 涨跌幅 (change_percent)
    - 买卖盘价格 (bid/ask, 如果可用)
    - 时间戳 (timestamp, ISO 8601 格式)
    
    **错误处理:**
    - 如果某个 ticker 获取失败,对应的 value 为 `null`
    - 如果整体请求失败,返回 500 错误
    """,
    response_description="返回 ticker 到价格数据的映射字典",
    responses={
        200: {
            "description": "成功返回价格数据",
            "content": {
                "application/json": {
                    "example": {
                        "BINANCE:BTCUSDT": {
                            "ticker": "BINANCE:BTCUSDT",
                            "price": 45000.50,
                            "currency": "USDT",
                            "timestamp": "2025-11-28T14:27:57+08:00",
                            "volume": 123456.78,
                            "open_price": 44800.00,
                            "high_price": 45200.00,
                            "low_price": 44500.00,
                            "close_price": 45000.50,
                            "change_percent": 0.45
                        },
                        "NASDAQ:AAPL": {
                            "ticker": "NASDAQ:AAPL",
                            "price": 185.50,
                            "currency": "USD",
                            "timestamp": "2025-11-28T14:27:57+08:00"
                        }
                    }
                }
            }
        },
        400: {"description": "请求参数错误 (ticker 格式不正确)"},
        500: {"description": "服务器内部错误"}
    }
)
async def get_multiple_prices(
    request: GetMultiplePricesRequest
) -> Dict[str, Any]:
    """
    批量获取实时价格
    
    Args:
        request: 包含 tickers 列表的请求体
        
    Returns:
        Dict[ticker, price_data]: ticker 到价格数据的映射
        
    Raises:
        HTTPException: 当数据获取失败时
    """
    try:
        logger.info(
            "API: get_multiple_prices called",
            tickers=request.tickers,
            count=len(request.tickers)
        )
        
        # 调用核心实现
        result = await market_use_cases.get_multiple_prices(request.tickers)
        
        # 统计成功/失败数量
        success_count = len([v for v in result.values() if v and "error" not in v])
        failed_count = len(request.tickers) - success_count
        
        logger.info(
            "API: get_multiple_prices completed",
            success=success_count,
            failed=failed_count,
            total=len(request.tickers)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"API error in get_multiple_prices: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch prices: {str(e)}"
        )


@router.post(
    "/indicators/calculate",
    summary="计算技术指标",
    description="""
    计算指定资产的技术指标
    
    **支持的指标:**
    - **SMA** (简单移动平均): 20/50/200 周期
    - **EMA** (指数移动平均): 12/26 周期
    - **RSI** (相对强弱指标): 14 周期,包含信号判断 (overbought/oversold/neutral)
    - **MACD**: 指标线 (macd)、信号线 (signal)、柱状图 (histogram)
    - **Bollinger Bands** (布林带): 上轨 (upper)、中轨 (middle)、下轨 (lower)
    - **KDJ** (随机指标): K 值、D 值
    - **ATR** (平均真实波幅): 波动率指标
    
    **参数说明:**
    - `symbol`: 资产代码 (必填)
    - `period`: 数据周期,支持格式:
      - `30d` = 30天
      - `90d` = 90天
      - `1y` = 1年
    - `interval`: K线间隔,支持:
      - `1d` = 日线
      - `1h` = 小时线
      - `15m` = 15分钟线
      - `5m` = 5分钟线
    
    **使用场景:**
    - 策略回测: 获取历史技术指标
    - 实时交易: 获取最新技术信号
    - 技术分析: 多指标综合判断
    """,
    response_description="返回完整的技术指标数据",
    responses={
        200: {
            "description": "成功返回技术指标",
            "content": {
                "application/json": {
                    "example": {
                        "symbol": "BINANCE:BTCUSDT",
                        "timestamp": "2025-11-28T14:27:57+08:00",
                        "price": {
                            "current": 45000.50,
                            "open": 44800.00,
                            "high": 45200.00,
                            "low": 44500.00,
                            "volume": 123456.78
                        },
                        "indicators": {
                            "sma": {
                                "sma_20": 44500.25,
                                "sma_50": 43800.50,
                                "sma_200": 42000.75
                            },
                            "rsi": {
                                "value": 65.5,
                                "signal": "neutral"
                            },
                            "macd": {
                                "macd": 250.30,
                                "signal": 200.50,
                                "histogram": 49.80
                            }
                        }
                    }
                }
            }
        },
        400: {"description": "请求参数错误"},
        500: {"description": "服务器内部错误"}
    }
)
async def calculate_technical_indicators(
    request: CalculateTechnicalIndicatorsRequest
) -> Dict[str, Any]:
    """
    计算技术指标
    
    Args:
        request: 包含 symbol, period, interval 的请求体
        
    Returns:
        Dict: 包含价格和技术指标的完整数据
        
    Raises:
        HTTPException: 当指标计算失败时
    """
    try:
        logger.info(
            "API: calculate_technical_indicators called",
            symbol=request.symbol,
            period=request.period,
            interval=request.interval
        )
        
        # 调用核心实现
        result = await technical_use_cases.calculate_technical_indicators(
            symbol=request.symbol,
            period=request.period,
            interval=request.interval,
        )
        
        logger.info(
            "API: calculate_technical_indicators completed",
            symbol=request.symbol,
            has_indicators=("indicators" in result)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"API error in calculate_technical_indicators: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to calculate indicators: {str(e)}"
        )


# ============================================================
# 新增 HTTP 端点
# ============================================================

@router.post(
    "/prices/history",
    summary="获取历史价格/K线数据",
    description="""
    获取指定资产的历史价格数据（K线数据）
    
    **参数说明:**
    - `symbol`: 资产代码 (格式: EXCHANGE:SYMBOL)
    - `period`: 时间周期 (7d, 30d, 90d, 1y)
    - `interval`: K线间隔 (1d, 1h, 15m, 5m)
    
    **返回数据:**
    - OHLCV 数据列表
    """,
)
async def get_historical_prices(
    request: GetHistoricalPricesRequest
) -> Dict[str, Any]:
    """获取历史价格数据"""
    try:
        logger.info(
            "API: get_historical_prices called",
            symbol=request.symbol,
            period=request.period,
            interval=request.interval
        )
        
        # 解析 period 为日期范围
        period_days = _parse_period_to_days(request.period)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)
        
        prices = await market_use_cases.get_historical_prices(
            ticker=request.symbol,
            start_date=start_date,
            end_date=end_date,
            interval=request.interval,
        )
        
        result = {
            "symbol": request.symbol,
            "period": request.period,
            "interval": request.interval,
            "count": len(prices),
            "data": prices
        }
        
        logger.info(
            "API: get_historical_prices completed",
            symbol=request.symbol,
            count=len(prices)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"API error in get_historical_prices: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch historical prices: {str(e)}"
        )


@router.post(
    "/asset/info",
    summary="获取资产详情",
    description="获取资产的详细信息，包括公司名称、行业、市值等",
)
async def get_asset_info(
    symbol: str = Query(..., description="资产代码 (格式: EXCHANGE:SYMBOL)")
) -> Dict[str, Any]:
    """获取资产详细信息"""
    try:
        logger.info("API: get_asset_info called", symbol=symbol)
        
        asset = await market_use_cases.get_asset_info(symbol)
        
        if asset:
            return asset.model_dump(mode="json")
        return {"error": f"Asset not found: {symbol}"}
        
    except Exception as e:
        logger.error(f"API error in get_asset_info: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get asset info: {str(e)}"
        )



@router.post(
    "/signals/trading",
    summary="生成交易信号",
    description="基于技术指标生成买入/卖出信号",
)
async def generate_trading_signal(
    request: GenerateTradingSignalRequest
) -> Dict[str, Any]:
    """生成交易信号"""
    try:
        logger.info(
            "API: generate_trading_signal called",
            symbol=request.symbol,
            period=request.period
        )
        
        result = await technical_use_cases.generate_trading_signal(
            symbol=request.symbol,
            period=request.period,
            interval=request.interval
        )
        
        return result
        
    except Exception as e:
        logger.error(f"API error in generate_trading_signal: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate trading signal: {str(e)}"
        )


@router.post(
    "/analysis/support-resistance",
    summary="计算支撑阻力位",
    description="计算资产的支撑位和阻力位",
)
async def calculate_support_resistance(
    request: CalculateSupportResistanceRequest
) -> Dict[str, Any]:
    """计算支撑阻力位"""
    try:
        logger.info(
            "API: calculate_support_resistance called",
            symbol=request.symbol,
            period=request.period
        )
        
        result = await technical_use_cases.calculate_support_resistance(
            symbol=request.symbol,
            period=request.period
        )
        
        return result
        
    except Exception as e:
        logger.error(f"API error in calculate_support_resistance: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to calculate support/resistance: {str(e)}"
        )


@router.post(
    "/report",
    summary="获取市场综合报告",
    description="获取资产的综合市场报告，包括价格和基本信息",
)
async def get_market_report(
    symbol: str = Query(..., description="资产代码")
) -> Dict[str, Any]:
    """获取综合市场报告"""
    try:
        logger.info("API: get_market_report called", symbol=symbol)
        
        import asyncio
        # 并行获取信息和价格
        info, price = await asyncio.gather(
            market_use_cases.get_asset_info(symbol),
            market_use_cases.get_real_time_price(symbol),
        )
        
        return {
            "symbol": symbol,
            "info": info,
            "price": price,
            "timestamp": datetime.now().isoformat(),
        }
        
    except Exception as e:
        logger.error(f"API error in get_market_report: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get market report: {str(e)}"
        )


# ============================================================
# 辅助函数
# ============================================================

def _parse_period_to_days(period: str) -> int:
    """解析 period 字符串为天数"""
    if period.endswith("d"):
        return int(period[:-1])
    elif period.endswith("m"):
        return int(period[:-1]) * 30
    elif period.endswith("y"):
        return int(period[:-1]) * 365
    else:
        return 30  # 默认 30 天
