# src/server/mcp/tools/technical_tools.py
"""MCP tools for technical analysis.
Provides technical indicators calculation and trading signals.
Returns structured data (JSON).
"""

from typing import Any, Dict

from fastmcp import FastMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


# ============================================================
# 核心业务逻辑实现 (可被 MCP 和 FastAPI 共享)
# ============================================================

async def calculate_technical_indicators_impl(
    symbol: str, 
    period: str = "30d", 
    interval: str = "1d"
) -> Dict[str, Any]:
    """
    计算技术指标 (核心实现)
    
    Args:
        symbol: 资产代码 (格式: EXCHANGE:SYMBOL)
        period: 数据周期 (30d, 90d, 1y)
        interval: K线间隔 (1d, 1h, 15m)
        
    Returns:
        包含技术指标的字典
        
    Raises:
        Exception: 计算失败时抛出异常
    """
    service = Container.technical_service()
    logger.info(
        "Calculating technical indicators",
        symbol=symbol,
        period=period,
        interval=interval,
    )
    
    result = await service.calculate_indicators(
        symbol=symbol, period=period, interval=interval
    )
    
    logger.info("Successfully calculated indicators", symbol=symbol)
    return result


# ============================================================
# MCP 工具注册 (保持原有接口不变)
# ============================================================

def register_technical_tools(mcp: FastMCP):
    """Register technical analysis tools."""

    @mcp.tool(tags={"technical-indicators", "technical-extended"})
    async def calculate_technical_indicators(
        symbol: str, period: str = "30d", interval: str = "1d"
    ) -> Dict[str, Any]:
        """Calculate technical indicators.

        Supported indicators:
        - SMA (20, 50, 200)
        - EMA (12, 26)
        - RSI (14)
        - MACD
        - Bollinger Bands
        - KDJ
        - ATR

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Data period (30d, 90d, 1y)
            interval: Data interval (1d)

        Returns:
            Dictionary containing calculated indicators
        """
        try:
            return await calculate_technical_indicators_impl(symbol, period, interval)
        except Exception as e:
            logger.error(f"MCP tool error in calculate_technical_indicators: {e}", exc_info=True)
            return {"error": str(e)}

    @mcp.tool(tags={"technical-signal", "technical-extended"})
    async def generate_trading_signal(
        symbol: str, period: str = "30d", interval: str = "1d"
    ) -> Dict[str, Any]:
        """Generate trading signals based on technical indicators.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)
            interval: Data interval (1d)

        Returns:
            Dictionary containing trading signals
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: generate_trading_signal", symbol=symbol, period=period
            )

            result = await service.generate_trading_signal(
                symbol=symbol, period=period, interval=interval
            )

            return result

        except Exception as e:
            logger.error(f"Generate trading signal failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"technical-pattern", "technical-extended"})
    async def analyze_price_patterns(symbol: str, period: str = "90d") -> Dict[str, Any]:
        """Analyze price patterns.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)

        Returns:
            Dictionary containing pattern analysis
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: analyze_price_patterns", symbol=symbol, period=period
            )

            result = await service.analyze_price_patterns(symbol=symbol, period=period)

            return result

        except Exception as e:
            logger.error(f"Analyze price patterns failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"technical-support", "technical-extended"})
    async def calculate_support_resistance(symbol: str, period: str = "90d") -> Dict[str, Any]:
        """Calculate support and resistance levels.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)

        Returns:
            Dictionary containing support/resistance levels
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: calculate_support_resistance",
                symbol=symbol,
                period=period,
            )

            result = await service.calculate_support_resistance(
                symbol=symbol, period=period
            )

            return result

        except Exception as e:
            logger.error(f"Calculate support/resistance failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"technical-volume", "technical-extended"})
    async def analyze_volume_profile(symbol: str, period: str = "90d") -> Dict[str, Any]:
        """Analyze volume profile.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
                - 加密货币: CRYPTO:BTC, CRYPTO:ETH
            period: Analysis period (30d, 90d, 1y)

        Returns:
            Dictionary containing volume analysis
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: analyze_volume_profile", symbol=symbol, period=period
            )

            result = await service.analyze_volume_profile(symbol=symbol, period=period)

            return result

        except Exception as e:
            logger.error(f"Analyze volume profile failed: {e}")
            return {"error": str(e)}
