# src/server/core/use_cases/market.py
"""Market-related use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def get_multiple_prices(tickers: List[str]) -> Dict[str, Any]:
    """Get real-time prices for multiple assets."""
    manager = Container.adapter_manager()
    logger.info("UseCase: get_multiple_prices", count=len(tickers))

    prices = await manager.get_multiple_prices(tickers)
    result: Dict[str, Any] = {}
    for ticker, price in prices.items():
        result[ticker] = price.to_dict() if price else None

    return result


async def get_real_time_price(ticker: str) -> Optional[Dict[str, Any]]:
    """Get real-time price for a single asset."""
    manager = Container.adapter_manager()
    price = await manager.get_real_time_price(ticker)
    return price.to_dict() if price else None


async def get_asset_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Get asset info for a ticker."""
    manager = Container.adapter_manager()
    asset = await manager.get_asset_info(ticker)
    return asset.model_dump(mode="json") if asset else None


async def get_historical_prices(
    ticker: str,
    start_date: datetime,
    end_date: datetime,
    interval: str = "1d",
) -> List[Dict[str, Any]]:
    """Get historical price data for a ticker."""
    manager = Container.adapter_manager()
    prices = await manager.get_historical_prices(ticker, start_date, end_date, interval)
    return [p.to_dict() for p in prices]

