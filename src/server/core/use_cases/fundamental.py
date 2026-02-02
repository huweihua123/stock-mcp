# src/server/core/use_cases/fundamental.py
"""Fundamental data use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from typing import Any, Dict

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def get_financials(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    logger.info("UseCase: get_financials", ticker=ticker)
    return await service.adapter_manager.get_financials(ticker)


async def get_mainbz_info(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    logger.info("UseCase: get_mainbz_info", ticker=ticker)
    return await service.adapter_manager.get_mainbz_info(ticker)


async def get_shareholder_info(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    logger.info("UseCase: get_shareholder_info", ticker=ticker)
    return await service.adapter_manager.get_shareholder_info(ticker)


async def get_dividend_info(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    logger.info("UseCase: get_dividend_info", ticker=ticker)
    return await service.adapter_manager.get_dividend_info(ticker)


async def get_fundamental_analysis(ticker: str) -> Dict[str, Any]:
    service = Container.fundamental_service()
    logger.info("UseCase: get_fundamental_analysis", ticker=ticker)
    return await service.get_fundamental_analysis(ticker)
