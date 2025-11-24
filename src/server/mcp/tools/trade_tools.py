# src/server/mcp/tools/trade_tools.py
"""MCP tools for trade execution.
Provides capabilities to execute orders via CCXT or other adapters.
"""

from typing import Any, Dict, Optional

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_trade_tools(mcp: NacosMCP):
    """Register trade-related tools for Nacos MCP.

    Args:
        mcp: NacosMCP instance
    """

    @mcp.tool()
    def execute_order(
        symbol: str,
        side: str,
        type: str,
        quantity: float,
        price: Optional[float] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a trading order.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT", "AAPL")
            side: "buy" or "sell"
            type: "market" or "limit"
            quantity: Amount to trade
            price: Limit price (required for limit orders)
            exchange_id: Optional exchange ID (e.g., "binance",
                "interactive_brokers")

        Returns:
            Order execution result
        """
        logger.info(
            "MCP tool called: execute_order",
            symbol=symbol,
            side=side,
            type=type,
            quantity=quantity,
            price=price,
        )

        # TODO: Implement actual trade execution logic
        # via AdapterManager -> CCXTAdapter
        # For now, return a simulated success response

        return {
            "status": "simulated_success",
            "order_id": "sim_123456789",
            "symbol": symbol,
            "side": side,
            "type": type,
            "quantity": quantity,
            "price": price,
            "message": "Order executed in simulation mode "
            "(Real trading not yet enabled)",
        }

    @mcp.tool()
    def get_account_balance(exchange_id: Optional[str] = None) -> Dict[str, Any]:
        """Get account balance.

        Args:
            exchange_id: Optional exchange ID

        Returns:
            Account balance information
        """
        logger.info("MCP tool called: get_account_balance", exchange_id=exchange_id)

        # TODO: Implement actual balance fetch
        return {
            "status": "simulated_success",
            "balances": {
                "USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0},
                "BTC": {"free": 0.5, "used": 0.0, "total": 0.5},
            },
        }

    logger.info("✅ Registered 2 trade tools for Nacos MCP")
