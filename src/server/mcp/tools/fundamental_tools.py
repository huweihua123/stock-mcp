# src/server/mcp/tools/fundamental_tools.py
"""MCP tools for fundamental (financial) data.
Implements `get_financial_report`.
Returns structured data (JSON).
"""

from typing import Any, Dict

from fastmcp import FastMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_fundamental_tools(mcp: FastMCP):
    @mcp.tool(tags={"fundamental-financial", "fundamental-core"})
    async def get_financial_report(symbol: str) -> Dict[str, Any]:
        """Get financial report for the given ticker.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA

        Returns:
            Dictionary containing financial analysis
        """
        service = Container.fundamental_service()
        logger.info("MCP tool called: get_financial_report", symbol=symbol)

        # Normalize ticker to EXCHANGE:SYMBOL format
        ticker = _normalize_ticker(symbol)
        logger.info(f"Normalized ticker: {symbol} -> {ticker}")

        return await service.get_fundamental_analysis(ticker)


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
