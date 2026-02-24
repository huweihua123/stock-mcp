# src/server/core/use_cases/fundamental.py
"""Fundamental data use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from typing import Any, Dict

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def get_financials(ticker: str) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_financials", symbol=ticker)
    return await manager.get_financials(ticker)


async def get_mainbz_info(ticker: str) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_mainbz_info", symbol=ticker)
    return await manager.get_mainbz_info(ticker)


async def get_shareholder_info(ticker: str) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_shareholder_info", symbol=ticker)
    return await manager.get_shareholder_info(ticker)


async def get_dividend_info(ticker: str) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_dividend_info", symbol=ticker)
    return await manager.get_dividend_info(ticker)


async def get_valuation_metrics(ticker: str, days: int = 250) -> Dict[str, Any]:
    manager = Container.market_gateway()
    logger.info("UseCase: get_valuation_metrics", symbol=ticker, days=days)
    return await manager.get_valuation_metrics(ticker, days)


async def get_fundamental_analysis(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    gateway = Container.market_gateway()
    resolved = await gateway.resolve_ticker(ticker)
    logger.info(
        "UseCase: get_fundamental_analysis", symbol=ticker, resolved_ticker=resolved
    )
    return await service.get_fundamental_analysis(resolved)
