# src/server/core/use_cases/filings.py
"""Filings use cases shared by MCP tools and REST routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


async def fetch_periodic_sec_filings(
    ticker: str,
    forms: Optional[list[str]] = None,
    year: Optional[int] = None,
    quarter: Optional[int] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    service = Container.filings_service()
    logger.info("UseCase: fetch_periodic_sec_filings", ticker=ticker, year=year, quarter=quarter)
    return await service.fetch_periodic_sec_filings(
        ticker=ticker,
        forms=forms,
        year=year,
        quarter=quarter,
        limit=limit,
    )


async def fetch_event_sec_filings(
    ticker: str,
    forms: Optional[list[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    service = Container.filings_service()
    logger.info("UseCase: fetch_event_sec_filings", ticker=ticker, start_date=start_date, end_date=end_date)
    return await service.fetch_event_sec_filings(
        ticker=ticker,
        forms=forms,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


async def fetch_ashare_filings(
    symbol: str,
    filing_types: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    service = Container.filings_service()
    logger.info("UseCase: fetch_ashare_filings", symbol=symbol, start_date=start_date, end_date=end_date)
    return await service.fetch_ashare_filings(
        symbol=symbol,
        filing_types=filing_types,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


async def fetch_ashare_regulatory_filings(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    service = Container.filings_service()
    logger.info("UseCase: fetch_ashare_regulatory_filings", ticker=ticker)
    return await service.fetch_ashare_regulatory_filings(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


async def process_document(
    doc_id: str,
    url: str,
    doc_type: str,
    ticker: Optional[str] = None,
) -> Dict[str, Any]:
    service = Container.filings_service()
    logger.info("UseCase: process_document", doc_id=doc_id, doc_type=doc_type, ticker=ticker)
    return await service.process_document(
        doc_id=doc_id,
        url=url,
        doc_type=doc_type,
        ticker=ticker,
    )


async def get_filing_markdown(ticker: str, doc_id: str) -> Dict[str, Any]:
    service = Container.filings_service()
    logger.info("UseCase: get_filing_markdown", ticker=ticker, doc_id=doc_id)
    return await service.get_filing_markdown(ticker=ticker, doc_id=doc_id)
