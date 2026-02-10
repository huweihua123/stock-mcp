# src/server/core/use_cases/money_flow.py
"""Money flow use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def get_money_flow(ticker: str, days: int = 20) -> Dict[str, Any]:
    service = Container.money_flow_service()
    logger.info("UseCase: get_money_flow", ticker=ticker, days=days)
    return await service.get_money_flow(ticker, days)


async def get_north_bound_flow(days: int = 30) -> Dict[str, Any]:
    service = Container.money_flow_service()
    logger.info("UseCase: get_north_bound_flow", days=days)
    return await service.get_north_bound_flow(days)


async def get_chip_distribution(ticker: str, days: int = 30) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_chip_distribution", ticker=ticker, days=days)
    return await manager.get_chip_distribution(ticker, days)


async def get_chip_distribution_detail(
    symbol: str, period_days: int = 30, price_bins: int = 100
) -> Dict[str, Any]:
    service = Container.chip_service()
    gateway = Container.market_gateway()
    resolved_symbol = await gateway.resolve_ticker(symbol)
    logger.info(
        "UseCase: get_chip_distribution_detail",
        symbol=symbol,
        resolved_symbol=resolved_symbol,
        period_days=period_days,
        price_bins=price_bins,
    )
    return await service.get_chip_distribution(resolved_symbol, period_days, price_bins)



async def get_money_supply() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_money_supply")
    return await manager.get_money_supply()


async def get_inflation_data() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_inflation_data")
    return await manager.get_inflation_data()


async def get_pmi_data() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_pmi_data")
    return await manager.get_pmi_data()


async def get_gdp_data() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_gdp_data")
    return await manager.get_gdp_data()


async def get_social_financing() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_social_financing")
    return await manager.get_social_financing()


async def get_interest_rates() -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_interest_rates")
    return await manager.get_interest_rates()


async def get_market_liquidity(days: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_market_liquidity", days=days)
    return await manager.get_market_liquidity(days)


async def get_market_money_flow(trade_date: Optional[str] = None) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_market_money_flow", trade_date=trade_date)
    return await manager.get_market_money_flow(trade_date)


async def get_sector_trend(sector_name: str, days: int = 10) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_sector_trend", sector=sector_name, days=days)
    return await manager.get_sector_trend(sector_name, days)


async def get_ggt_daily(days: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_ggt_daily", days=days)
    return await manager.get_ggt_daily(days)
