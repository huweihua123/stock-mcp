# src/server/mcp/tools/technical_tools.py
"""MCP tools for technical analysis.
Provides technical indicators calculation and trading signals.
Returns structured data (JSON).
"""

from typing import Any, Dict

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_technical_tools(mcp: NacosMCP):
    """Register technical analysis tools for Nacos MCP.

    Args:
        mcp: NacosMCP instance
    """

    @mcp.tool()
    def calculate_technical_indicators(
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
            symbol: Stock symbol (e.g., AAPL, 600519)
            period: Data period (30d, 90d, 1y)
            interval: Data interval (1d)

        Returns:
            Dictionary containing calculated indicators
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: calculate_technical_indicators",
                symbol=symbol,
                period=period,
                interval=interval,
            )

            result = service.calculate_indicators(
                    symbol=symbol, period=period, interval=interval
            )

            return result

        except Exception as e:
            logger.error(f"Calculate technical indicators failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def generate_trading_signal(
        symbol: str, period: str = "30d", interval: str = "1d"
    ) -> Dict[str, Any]:
        """Generate trading signals based on technical indicators.

        Args:
            symbol: Stock symbol
            period: Analysis period
            interval: Data interval

        Returns:
            Dictionary containing trading signals
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: generate_trading_signal", symbol=symbol, period=period
            )

            result = service.generate_trading_signal(
                    symbol=symbol, period=period, interval=interval
            )

            return result

        except Exception as e:
            logger.error(f"Generate trading signal failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def analyze_price_patterns(symbol: str, period: str = "90d") -> Dict[str, Any]:
        """Analyze price patterns.

        Args:
            symbol: Stock symbol
            period: Analysis period

        Returns:
            Dictionary containing pattern analysis
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: analyze_price_patterns", symbol=symbol, period=period
            )

            result = service.analyze_price_patterns(symbol=symbol, period=period
            )

            return result

        except Exception as e:
            logger.error(f"Analyze price patterns failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def calculate_support_resistance(
        symbol: str, period: str = "90d"
    ) -> Dict[str, Any]:
        """Calculate support and resistance levels.

        Args:
            symbol: Stock symbol
            period: Analysis period

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

            result = service.calculate_support_resistance(symbol=symbol, period=period
            )

            return result

        except Exception as e:
            logger.error(f"Calculate support/resistance failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def analyze_volume_profile(symbol: str, period: str = "90d") -> Dict[str, Any]:
        """Analyze volume profile.

        Args:
            symbol: Stock symbol
            period: Analysis period

        Returns:
            Dictionary containing volume analysis
        """
        try:
            service = Container.technical_service()
            logger.info(
                "MCP tool called: analyze_volume_profile", symbol=symbol, period=period
            )

            result = service.analyze_volume_profile(symbol=symbol, period=period
            )

            return result

        except Exception as e:
            logger.error(f"Analyze volume profile failed: {e}")
            return {"error": str(e)}

    logger.info("✅ Registered 5 technical tools for Nacos MCP")
