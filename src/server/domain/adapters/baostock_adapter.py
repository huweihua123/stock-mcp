# src/server/domain/adapters/baostock_adapter.py
"""Baostock adapter for Chinese A-share market data.

Baostock is a free, open-source securities data platform providing:
- Historical K-line data (daily, weekly, monthly, minute-level)
- Financial statements (balance sheet, income statement, cash flow)
- Stock basic information
- Macroeconomic data

All methods are async via asyncio.run_in_executor to avoid blocking.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import baostock as bs
import pandas as pd

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
    MarketInfo,
    MarketStatus,
)
from src.server.utils.logger import logger


class BaostockAdapter(BaseDataAdapter):
    """Adapter for Baostock data source.
    
    Baostock uses ticker format: sh.600000 (Shanghai) or sz.000001 (Shenzhen)
    Internal format: SSE:600000 or SZSE:000001
    """
    
    name = "baostock"

    def __init__(self, cache):
        super().__init__(DataSource.BAOSTOCK)
        self.cache = cache
        self.logger = logger

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare Baostock's capabilities.
        
        Baostock supports A-share stocks and indices only.
        """
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.SSE, Exchange.SZSE},
            ),
            AdapterCapability(
                asset_type=AssetType.INDEX,
                exchanges={Exchange.SSE, Exchange.SZSE},
            ),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert EXCHANGE:SYMBOL to Baostock format.
        
        Examples:
            SSE:600000 -> sh.600000
            SZSE:000001 -> sz.000001
        
        Args:
            internal_ticker: Ticker in internal format
            
        Returns:
            Ticker in Baostock format
        """
        if ":" not in internal_ticker:
            return internal_ticker
            
        exchange, symbol = internal_ticker.split(":", 1)
        
        if exchange == "SSE":
            return f"sh.{symbol}"
        elif exchange == "SZSE":
            return f"sz.{symbol}"
        else:
            # Fallback: assume Shanghai
            return f"sh.{symbol}"

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert Baostock format to EXCHANGE:SYMBOL.
        
        Examples:
            sh.600000 -> SSE:600000
            sz.000001 -> SZSE:000001
        
        Args:
            source_ticker: Ticker in Baostock format
            default_exchange: Not used for Baostock (format is explicit)
            
        Returns:
            Ticker in internal format
        """
        if "." in source_ticker:
            prefix, symbol = source_ticker.split(".", 1)
            if prefix.lower() == "sh":
                return f"SSE:{symbol}"
            elif prefix.lower() == "sz":
                return f"SZSE:{symbol}"
        
        # Fallback: try to infer from symbol
        if source_ticker.startswith("6"):
            return f"SSE:{source_ticker}"
        elif source_ticker.startswith(("0", "3")):
            return f"SZSE:{source_ticker}"
        
        # Last resort
        return f"SSE:{source_ticker}"

    async def _run(self, func, *args, **kwargs):
        """Run synchronous function in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _to_bs_code(self, ticker: str) -> str:
        """Convert internal ticker to Baostock code."""
        return self.convert_to_source_ticker(ticker)

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch detailed asset information.
        
        Uses Baostock's query_stock_basic to get stock information.
        
        Args:
            ticker: Asset ticker in internal format
            
        Returns:
            Asset object or None if not found
        """
        cache_key = f"baostock:info:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        bs_code = self._to_bs_code(ticker)
        
        try:
            # Query stock basic info
            rs = await self._run(bs.query_stock_basic, code=bs_code)
            
            if rs.error_code != '0':
                self.logger.warning(
                    f"Baostock query_stock_basic failed for {ticker}: {rs.error_msg}"
                )
                return None
            
            # Convert result to DataFrame
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return None
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            
            if df.empty:
                return None
            
            row = df.iloc[0]
            
            # Parse exchange
            exchange = ticker.split(":")[0]
            
            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.STOCK,
                name=str(row.get("code_name", ticker)),
                market_info=MarketInfo(
                    exchange=exchange,
                    country="CN",
                    currency="CNY",
                    timezone="Asia/Shanghai",
                    market_status=MarketStatus.UNKNOWN,
                ),
                source_mappings={DataSource.BAOSTOCK: bs_code},
                properties={
                    "ipoDate": str(row.get("ipoDate", "")),
                    "outDate": str(row.get("outDate", "")),
                    "type": str(row.get("type", "")),
                    "status": str(row.get("status", "")),
                },
            )

            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
            
        except Exception as e:
            self.logger.warning(f"Failed to fetch asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price.
        
        Note: Baostock doesn't provide real-time data. This method returns
        the latest available daily data, which may be delayed.
        
        Args:
            ticker: Asset ticker in internal format
            
        Returns:
            Latest price data or None if not available
        """
        cache_key = f"baostock:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        bs_code = self._to_bs_code(ticker)
        
        try:
            # Get latest daily data (last 5 days to ensure we get data)
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now().replace(day=1)).strftime("%Y-%m-%d")
            
            rs = await self._run(
                bs.query_history_k_data_plus,
                code=bs_code,
                fields="date,code,open,high,low,close,volume,amount,turn",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",  # 3: 后复权
            )
            
            if rs.error_code != '0':
                self.logger.warning(
                    f"Baostock query failed for {ticker}: {rs.error_msg}"
                )
                return None
            
            # Convert to DataFrame
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return None
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            
            if df.empty:
                return None
            
            # Get the latest row
            row = df.iloc[-1]
            
            # Parse date
            date_str = row["date"]
            timestamp = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Create AssetPrice
            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(row["close"])),
                currency="CNY",
                timestamp=timestamp,
                volume=Decimal(str(row["volume"])) if row["volume"] else None,
                open_price=Decimal(str(row["open"])) if row["open"] else None,
                high_price=Decimal(str(row["high"])) if row["high"] else None,
                low_price=Decimal(str(row["low"])) if row["low"] else None,
                close_price=Decimal(str(row["close"])) if row["close"] else None,
                source=DataSource.BAOSTOCK,
            )

            await self.cache.set(cache_key, asset_price.to_dict(), ttl=300)
            return asset_price
            
        except Exception as e:
            self.logger.warning(f"Failed to fetch price for {ticker}: {e}")
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Fetch historical prices.
        
        Args:
            ticker: Asset ticker in internal format
            start_date: Start date
            end_date: End date
            interval: Data interval (1d, 1w, 1m supported)
            
        Returns:
            List of historical price data
        """
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        cache_key = f"baostock:history:{ticker}:{start_str}:{end_str}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        bs_code = self._to_bs_code(ticker)
        
        # Map interval to Baostock frequency
        frequency_map = {
            "1d": "d",
            "1w": "w",
            "1m": "m",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "60m": "60",
        }
        frequency = frequency_map.get(interval, "d")

        try:
            rs = await self._run(
                bs.query_history_k_data_plus,
                code=bs_code,
                fields="date,code,open,high,low,close,volume,amount,turn",
                start_date=start_str,
                end_date=end_str,
                frequency=frequency,
                adjustflag="3",  # 后复权
            )
            
            if rs.error_code != '0':
                self.logger.error(
                    f"Baostock query failed for {ticker}: {rs.error_msg}"
                )
                return []
            
            # Convert to DataFrame
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return []
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            
            if df.empty:
                return []

            prices = []
            for _, row in df.iterrows():
                date_str = row["date"]
                
                # Handle different date formats
                if frequency in ["5", "15", "30", "60"]:
                    # Minute data: YYYY-MM-DD HH:MM:SS
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                else:
                    # Daily/Weekly/Monthly: YYYY-MM-DD
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d")

                price = AssetPrice(
                    ticker=ticker,
                    price=Decimal(str(row["close"])),
                    currency="CNY",
                    timestamp=timestamp,
                    volume=Decimal(str(row["volume"])) if row["volume"] else None,
                    open_price=Decimal(str(row["open"])) if row["open"] else None,
                    high_price=Decimal(str(row["high"])) if row["high"] else None,
                    low_price=Decimal(str(row["low"])) if row["low"] else None,
                    close_price=Decimal(str(row["close"])) if row["close"] else None,
                    source=DataSource.BAOSTOCK,
                )
                prices.append(price)

            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=3600)
            return prices

        except Exception as e:
            self.logger.error(f"Failed to fetch history for {ticker}: {e}")
            return []

    async def _get_all_stocks_cached(self) -> List[Dict]:
        """Helper to get all stocks with caching."""
        cache_key = "baostock:all_stocks"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            # Query all stocks for today (or a recent date)
            today = datetime.now().strftime("%Y-%m-%d")
            rs = await self._run(bs.query_all_stock, day=today)
            
            if rs.error_code != '0':
                self.logger.warning(
                    f"Baostock query_all_stock failed: {rs.error_msg}"
                )
                return []
            
            # Convert to DataFrame
            data_list = []
            while (rs.error_code == '0') and rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return []
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            
            if df.empty:
                return []

            stocks = []
            for _, row in df.iterrows():
                bs_code = str(row.get("code", ""))
                name = str(row.get("code_name", ""))
                
                if not bs_code:
                    continue

                # Convert to internal format
                ticker = self.convert_to_internal_ticker(bs_code)
                
                # Extract symbol
                symbol = bs_code.split(".")[-1] if "." in bs_code else bs_code

                stocks.append({
                    "ticker": ticker,
                    "code": symbol,
                    "name": name,
                })

            await self.cache.set(cache_key, stocks, ttl=3600)
            return stocks
            
        except Exception as e:
            self.logger.warning(f"Failed to fetch all stocks: {e}")
            return []

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Fetch financial statements and metrics.
        
        Baostock provides comprehensive financial data including:
        - Profitability indicators
        - Operation capability
        - Growth ability
        - Debt repayment ability
        - Cash flow
        
        Args:
            ticker: Asset ticker in internal format
            
        Returns:
            Dictionary containing financial data
        """
        cache_key = f"baostock:financials:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        bs_code = self._to_bs_code(ticker)

        try:
            # Get latest year and quarter
            now = datetime.now()
            year = now.year
            quarter = (now.month - 1) // 3 + 1
            
            # If we're early in the quarter, use previous quarter
            if now.month % 3 == 1 and now.day < 15:
                quarter -= 1
                if quarter == 0:
                    quarter = 4
                    year -= 1

            # Fetch profitability data
            rs_profit = await self._run(
                bs.query_profit_data,
                code=bs_code,
                year=year,
                quarter=quarter
            )
            
            # Fetch operation capability data
            rs_operation = await self._run(
                bs.query_operation_data,
                code=bs_code,
                year=year,
                quarter=quarter
            )
            
            # Fetch growth ability data
            rs_growth = await self._run(
                bs.query_growth_data,
                code=bs_code,
                year=year,
                quarter=quarter
            )
            
            # Fetch balance data
            rs_balance = await self._run(
                bs.query_balance_data,
                code=bs_code,
                year=year,
                quarter=quarter
            )
            
            # Fetch cash flow data
            rs_cash = await self._run(
                bs.query_cash_flow_data,
                code=bs_code,
                year=year,
                quarter=quarter
            )

            result = {
                "profitability": self._result_to_dict(rs_profit),
                "operation": self._result_to_dict(rs_operation),
                "growth": self._result_to_dict(rs_growth),
                "balance": self._result_to_dict(rs_balance),
                "cash_flow": self._result_to_dict(rs_cash),
                "year": year,
                "quarter": quarter,
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")

    def _result_to_dict(self, rs) -> Optional[Dict[str, Any]]:
        """Convert Baostock result to dictionary."""
        if rs.error_code != '0':
            return None
        
        data_list = []
        while (rs.error_code == '0') and rs.next():
            data_list.append(rs.get_row_data())
        
        if not data_list:
            return None
        
        df = pd.DataFrame(data_list, columns=rs.fields)
        
        if df.empty:
            return None
        
        # Return first row as dict
        return df.iloc[0].to_dict()
