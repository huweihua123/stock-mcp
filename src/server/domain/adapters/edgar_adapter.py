# src/server/domain/adapters/edgar_adapter.py
"""EDGAR adapter for SEC filings.

Provides direct access to SEC filings using edgartools.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from edgar import Company

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
)
from src.server.utils.logger import logger
from src.server.utils.sec_utils import get_company, get_cik_or_symbol


class EdgarAdapter(BaseDataAdapter):
    """EDGAR adapter for SEC filings."""

    name = "edgar"

    def __init__(self, cache=None):
        super().__init__(DataSource.EDGAR)
        self.cache = cache
        self.logger = logger

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare EDGAR's capabilities - US exchanges only, mainly for filings."""
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.NASDAQ, Exchange.NYSE, Exchange.AMEX},
            ),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert EXCHANGE:SYMBOL to pure symbol (e.g. 'NASDAQ:AAPL' -> 'AAPL')."""
        if ":" in internal_ticker:
            return internal_ticker.split(":", 1)[1]
        return internal_ticker

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert pure symbol to EXCHANGE:SYMBOL."""
        if ":" in source_ticker:
            return source_ticker
        
        exchange = default_exchange or "NASDAQ"
        return f"{exchange}:{source_ticker}"

    def _extract_symbol(self, ticker: str) -> str:
        """Extract pure symbol from ticker (e.g. 'NASDAQ:AAPL' -> 'AAPL')."""
        return self.convert_to_source_ticker(ticker)

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch filings from SEC EDGAR."""
        
        def _do_fetch():
            pure_symbol = self._extract_symbol(ticker)
            try:
                company = get_company(pure_symbol)
                
                # 1. Get Filings Container
                # We can use year to filter at source if start_date is provided and close enough
                # But for simplicity and correctness with date ranges, we fetch latest
                filings = company.get_filings()
                
                # 2. Apply Filters (Form, Date)
                results = []
                
                # Optimization: If only one form is requested, filter at source
                if filing_types and len(filing_types) == 1:
                    filings = filings.filter(form=filing_types[0])
                
                count = 0
                # Iterate through filings (edgartools returns latest first by default)
                for filing in filings:
                    # Filter by Form (if multiple forms requested)
                    if filing_types and len(filing_types) > 1 and filing.form not in filing_types:
                        continue
                    
                    # Filter by Date
                    # filing.filing_date is already a date object (or string, handle both)
                    f_date = filing.filing_date
                    if isinstance(f_date, str):
                        f_date = datetime.strptime(f_date, "%Y-%m-%d").date()
                    
                    # Convert start_date/end_date to date for comparison
                    start_date_date = start_date.date() if start_date else None
                    end_date_date = end_date.date() if end_date else None

                    if end_date_date and f_date > end_date_date:
                        continue
                    if start_date_date and f_date < start_date_date:
                        # Assuming descending order, we can stop if we go past start_date
                        break
                    
                    # Construct Description
                    description = ""
                    # Priority 1: Items (common in 8-K)
                    if hasattr(filing, "items") and filing.items:
                        # filing.items might be a list of strings, ensure clean join
                        items_list = filing.items if isinstance(filing.items, list) else [str(filing.items)]
                        description = ", ".join(items_list)
                    # Priority 2: Primary Doc Description (common in 6-K/10-Q)
                    elif hasattr(filing, "primary_doc_description") and filing.primary_doc_description:
                        description = filing.primary_doc_description
                    # Priority 3: Form Type
                    else:
                        description = f"Form {filing.form}"
                        
                    # Map to result
                    results.append({
                        "accessionNumber": filing.accession_no,
                        "symbol": pure_symbol,
                        "filingDate": filing.filing_date,
                        "reportDate": filing.filing_date,
                        "form": filing.form,
                        "filingUrl": filing.homepage_url, # Index page URL
                        "description": description
                    })
                    
                    count += 1
                    if limit and count >= limit:
                        break
                        
                    # Safety break
                    if count >= (limit * 5 if limit else 100):
                        break
                
                return results
            except Exception as e:
                self.logger.error(f"Failed to fetch SEC filings via edgartools for {ticker}: {e}")
                return []

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_fetch)

    # Implement other abstract methods with empty/None returns as EDGAR is mainly for filings
    
    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        return []

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        return {}
