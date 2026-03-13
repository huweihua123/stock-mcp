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


async def get_money_supply(months: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_money_supply", months=months)
    return await manager.get_money_supply(months)


async def get_inflation_data(months: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_inflation_data", months=months)
    return await manager.get_inflation_data(months)


async def get_pmi_data(months: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_pmi_data", months=months)
    return await manager.get_pmi_data(months)


async def get_gdp_data(quarters: int = 20) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_gdp_data", quarters=quarters)
    return await manager.get_gdp_data(quarters)


async def get_social_financing(months: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_social_financing", months=months)
    return await manager.get_social_financing(months)


async def get_interest_rates(
    shibor_days: int = 252, lpr_months: int = 60
) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info(
        "UseCase: get_interest_rates",
        shibor_days=shibor_days,
        lpr_months=lpr_months,
    )
    return await manager.get_interest_rates(shibor_days, lpr_months)


async def get_market_liquidity(days: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_market_liquidity", days=days)
    return await manager.get_market_liquidity(days)


async def get_market_money_flow(
    trade_date: Optional[str] = None,
    top_n: int = 20,
    include_outflow: bool = True,
) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info(
        "UseCase: get_market_money_flow",
        trade_date=trade_date,
        top_n=top_n,
        include_outflow=include_outflow,
    )
    return await manager.get_market_money_flow(
        trade_date=trade_date,
        top_n=top_n,
        include_outflow=include_outflow,
    )


async def get_sector_trend(
    sector_name: str = "",
    days: int = 10,
    sector_id: Optional[str] = None,
) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info(
        "UseCase: get_sector_trend",
        sector=sector_name,
        sector_id=sector_id,
        days=days,
    )
    return await manager.get_sector_trend(
        sector_name=sector_name,
        days=days,
        sector_id=sector_id,
    )


async def resolve_sector(query_text: str, intent: str = "trend") -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: resolve_sector", query=query_text, intent=intent)
    return await manager.resolve_sector(query_text=query_text, intent=intent)


async def get_sector_money_flow_history(
    sector_name: str = "",
    days: int = 20,
    sector_id: Optional[str] = None,
) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info(
        "UseCase: get_sector_money_flow_history",
        sector=sector_name,
        sector_id=sector_id,
        days=days,
    )
    return await manager.get_sector_money_flow_history(
        sector_name=sector_name,
        days=days,
        sector_id=sector_id,
    )


async def get_sector_valuation_metrics(
    sector_name: str = "",
    days: int = 250,
    sample_size: int = 60,
    sector_id: Optional[str] = None,
) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info(
        "UseCase: get_sector_valuation_metrics",
        sector=sector_name,
        sector_id=sector_id,
        days=days,
        sample_size=sample_size,
    )
    return await manager.get_sector_valuation_metrics(
        sector_name=sector_name,
        days=days,
        sample_size=sample_size,
        sector_id=sector_id,
    )


async def get_ggt_daily(days: int = 60) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_ggt_daily", days=days)
    return await manager.get_ggt_daily(days)


async def get_us_economic_growth(quarters: int = 20) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_us_economic_growth", quarters=quarters)
    return await manager.get_us_economic_growth(quarters)


async def get_us_inflation_employment(months: int = 24) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_us_inflation_employment", months=months)
    return await manager.get_us_inflation_employment(months)


async def get_us_interest_rates(days: int = 180) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_us_interest_rates", days=days)
    return await manager.get_us_interest_rates(days)
