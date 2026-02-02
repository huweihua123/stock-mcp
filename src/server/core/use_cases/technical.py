# src/server/core/use_cases/technical.py
"""Technical analysis use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from typing import Any, Dict

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def calculate_technical_indicators(
    symbol: str, period: str = "30d", interval: str = "1d", limit: int | None = None
) -> Dict[str, Any]:
    service = Container.technical_service()
    logger.info(
        "UseCase: calculate_technical_indicators",
        symbol=symbol,
        period=period,
        interval=interval,
        limit=limit,
    )
    result = await service.calculate_indicators(
        symbol=symbol, period=period, interval=interval, limit=limit
    )
    return result


async def analyze_price_patterns(symbol: str, period: str = "90d") -> Dict[str, Any]:
    service = Container.technical_service()
    logger.info("UseCase: analyze_price_patterns", symbol=symbol, period=period)
    return await service.analyze_price_patterns(symbol=symbol, period=period)


async def calculate_support_resistance(symbol: str, period: str = "90d") -> Dict[str, Any]:
    service = Container.technical_service()
    logger.info("UseCase: calculate_support_resistance", symbol=symbol, period=period)
    return await service.calculate_support_resistance(symbol=symbol, period=period)


async def analyze_volume_profile(symbol: str, period: str = "90d") -> Dict[str, Any]:
    service = Container.technical_service()
    logger.info("UseCase: analyze_volume_profile", symbol=symbol, period=period)
    return await service.analyze_volume_profile(symbol=symbol, period=period)


async def generate_trading_signal(
    symbol: str, period: str = "90d", interval: str = "1d"
) -> Dict[str, Any]:
    service = Container.technical_service()
    logger.info(
        "UseCase: generate_trading_signal", symbol=symbol, period=period, interval=interval
    )
    return await service.generate_trading_signal(
        symbol=symbol, period=period, interval=interval
    )
