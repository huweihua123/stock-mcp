# src/server/domain/adapters/yahoo_adapter.py
"""YahooFinance adapter using yfinance.

All methods are async via asyncio.run_in_executor to avoid blocking.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetSearchQuery,
    AssetSearchResult,
    AssetType,
    DataSource,
    Exchange,
    MarketInfo,
    MarketStatus,
)
from src.server.utils.logger import logger


class YahooAdapter(BaseDataAdapter):
    name = "yahoo"

    def __init__(self, cache, proxy_url: str = None):
        super().__init__(DataSource.YAHOO)
        self.cache = cache
        self.logger = logger
        self.proxy_url = proxy_url or "http://127.0.0.1:7890"

        # Configure proxy
        # yfinance 1.0+ uses curl_cffi which requires proxy as dict format
        if self.proxy_url:
            try:
                proxy_dict = {
                    "http": self.proxy_url,
                    "https": self.proxy_url,
                }
                yf.config.network.proxy = proxy_dict
                self.logger.info(
                    f"✅ Yahoo adapter configured with proxy (yf.config.network.proxy): {self.proxy_url}"
                )
            except Exception as e:
                self.logger.warning(f"⚠️  Failed to set proxy via yf.config: {e}, trying env vars...")
                import os
                os.environ["HTTP_PROXY"] = self.proxy_url
                os.environ["HTTPS_PROXY"] = self.proxy_url
                os.environ["http_proxy"] = self.proxy_url
                os.environ["https_proxy"] = self.proxy_url
                self.logger.info(
                    f"✅ Yahoo adapter configured with proxy (env vars): {self.proxy_url}"
                )
        else:
            self.logger.info("ℹ️  Yahoo adapter running without proxy")

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare Yahoo Finance's capabilities."""
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={
                    Exchange.NASDAQ,
                    Exchange.NYSE,
                    Exchange.AMEX,
                    Exchange.HKEX,
                },
            ),
            AdapterCapability(
                asset_type=AssetType.ETF, exchanges={Exchange.NASDAQ, Exchange.NYSE}
            ),
            AdapterCapability(
                asset_type=AssetType.INDEX, exchanges={Exchange.NASDAQ, Exchange.NYSE}
            ),
            AdapterCapability(
                asset_type=AssetType.CRYPTO, exchanges={Exchange.CRYPTO}
            ),
        ]

    def get_supported_asset_types(self) -> List[AssetType]:
        """Get list of supported asset types."""
        return [
            AssetType.STOCK,
            AssetType.ETF,
            AssetType.INDEX,
            AssetType.FOREX,
            AssetType.CRYPTO,  # Add support for Crypto
        ]

    def convert_to_source_ticker(self, ticker: str) -> str:
        """Convert internal ticker to Yahoo Finance format.
        
        Internal format: EXCHANGE:SYMBOL
        Yahoo format: SYMBOL.EXCHANGE_SUFFIX
        """
        if ":" not in ticker:
            return ticker
            
        exchange, symbol = ticker.split(":", 1)
        
        # Handle Crypto
        if exchange == "CRYPTO":
            # Convert BTC/USDT -> BTC-USD
            if "/" in symbol:
                base, quote = symbol.split("/")
                # Yahoo uses USD for most crypto pairs
                if quote in ["USDT", "USDC", "USD"]:
                    return f"{base}-USD"
                return f"{base}-{quote}"
            return f"{symbol}-USD"

        # Handle US stocks (no suffix)
        if exchange in ["NASDAQ", "NYSE", "AMEX", "US"]:
            return symbol
            
        # Handle HK stocks
        if exchange == "HKEX":
            return f"{symbol}.HK"
            
        # Handle A-shares
        if exchange == "SSE":  # Shanghai
            return f"{symbol}.SS"

        elif exchange == "SZSE":
            return f"{symbol}.SZ"

        elif exchange == "CRYPTO":
            return f"{symbol}-USD"

        elif exchange in ["NASDAQ", "NYSE", "AMEX"]:
            return symbol

        else:
            return symbol

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert Yahoo Finance format to EXCHANGE:SYMBOL."""
        # Special handling for indices from yfinance - remove ^ prefix
        if source_ticker.startswith("^"):
            symbol = source_ticker[1:]  # Remove ^ prefix
            if default_exchange:
                return f"{default_exchange}:{symbol}"
            return f"UNKNOWN:{symbol}"

        # Special handling for crypto from yfinance - remove currency suffix
        if "-USD" in source_ticker:
            crypto_symbol = source_ticker.split("-")[0].upper()
            return f"CRYPTO:{crypto_symbol}"

        # Special handling for Hong Kong stocks from yfinance
        if ".HK" in source_ticker:
            symbol = source_ticker.replace(".HK", "")
            if symbol.isdigit():
                symbol = symbol.zfill(5)
            return f"HKEX:{symbol}"

        # Special handling for Shanghai stocks from yfinance
        if ".SS" in source_ticker:
            symbol = source_ticker.replace(".SS", "")
            return f"SSE:{symbol}"

        # Special handling for Shenzhen stocks from yfinance
        if ".SZ" in source_ticker:
            symbol = source_ticker.replace(".SZ", "")
            return f"SZSE:{symbol}"

        if default_exchange:
            return f"{default_exchange}:{source_ticker}"

        # Default to NASDAQ if no exchange info
        return f"NASDAQ:{source_ticker}"

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
            except Exception as e:
                error_msg = str(e).lower()
                if (
                    "too many requests" in error_msg
                    or "429" in error_msg
                    or "rate limited" in error_msg
                ):
                    if attempt == max_retries - 1:
                        raise e

                    delay = base_delay * (2**attempt) + (
                        0.1 * (asyncio.get_event_loop().time() % 1)
                    )
                    self.logger.warning(
                        f"Rate limited (attempt {attempt + 1}/{max_retries}), retrying in {delay:.2f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise e

    def _to_yf_ticker(self, ticker: str) -> str:
        return self.convert_to_source_ticker(ticker)

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch detailed asset information."""
        ticker_norm = self._to_yf_ticker(ticker)
        cache_key = f"yahoo:info:{ticker_norm}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            info = await self._run(lambda: ticker_obj.info)

            # yfinance 1.0 may return string or None instead of dict
            if not info or not isinstance(info, dict):
                self.logger.warning(f"Invalid info type for {ticker}: {type(info)}")
                return None

            if "symbol" not in info:
                self.logger.warning(f"No symbol in info for {ticker}")
                return None

            # Map Yahoo info to Asset model
            exchange_map = {
                "NMS": "NASDAQ",
                "NYQ": "NYSE",
                "ASE": "AMEX",
                "HKG": "HKEX",
            }
            yf_exchange = info.get("exchange", "")
            exchange = exchange_map.get(yf_exchange, yf_exchange)

            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.STOCK,  # Default to STOCK
                name=info.get("longName") or info.get("shortName") or ticker,
                market_info=MarketInfo(
                    exchange=exchange,
                    country=info.get("country", "US"),
                    currency=info.get("currency", "USD"),
                    timezone=info.get("timeZoneShortName", "UTC"),
                    market_status=MarketStatus.UNKNOWN,
                ),
                source_mappings={DataSource.YAHOO: ticker_norm},
                properties={
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "website": info.get("website"),
                    "description": info.get("longBusinessSummary"),
                },
            )

            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
        except Exception as e:
            self.logger.warning(f"Failed to fetch asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price."""
        ticker_norm = self._to_yf_ticker(ticker)
        cache_key = f"yahoo:price:{ticker_norm}"
        cached = await self.cache.get(cache_key)
        if cached:
            # Reconstruct AssetPrice from cached dict
            return AssetPrice.from_dict(cached)

        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            
            # Try fast_info first (more stable in yfinance 1.0)
            price = None
            currency = "USD"
            volume = 0
            open_price = None
            high_price = None
            low_price = None
            close_price = None
            market_cap = None
            
            try:
                fast_info = await self._run(lambda: ticker_obj.fast_info)
                if fast_info:
                    price = getattr(fast_info, 'last_price', None)
                    currency = getattr(fast_info, 'currency', 'USD') or 'USD'
                    volume = getattr(fast_info, 'last_volume', 0) or 0
                    open_price = getattr(fast_info, 'open', None)
                    high_price = getattr(fast_info, 'day_high', None)
                    low_price = getattr(fast_info, 'day_low', None)
                    close_price = getattr(fast_info, 'previous_close', None)
                    market_cap = getattr(fast_info, 'market_cap', None)
            except Exception as fast_info_error:
                self.logger.debug(f"fast_info failed for {ticker}, trying info: {fast_info_error}")
            
            # Fallback to info if fast_info didn't get price
            if price is None:
                info = await self._run(lambda: ticker_obj.info)
                
                # yfinance 1.0 may return string or None instead of dict
                if info and isinstance(info, dict):
                    price = (
                        info.get("currentPrice")
                        or info.get("regularMarketPrice")
                        or info.get("ask")
                    )
                    if price is not None:
                        currency = info.get("currency", "USD") or "USD"
                        volume = info.get("volume", 0) or 0
                        open_price = info.get("open")
                        high_price = info.get("dayHigh")
                        low_price = info.get("dayLow")
                        close_price = info.get("previousClose")
                        market_cap = info.get("marketCap")

            if price is None:
                self.logger.warning(f"No price available for {ticker}")
                return None

            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(price)),
                currency=currency,
                timestamp=datetime.utcnow(),
                volume=Decimal(str(volume)) if volume else Decimal("0"),
                open_price=Decimal(str(open_price)) if open_price else None,
                high_price=Decimal(str(high_price)) if high_price else None,
                low_price=Decimal(str(low_price)) if low_price else None,
                close_price=Decimal(str(close_price)) if close_price else None,
                change=None,  # Calculate if needed
                change_percent=None,
                market_cap=Decimal(str(market_cap)) if market_cap else None,
                source=DataSource.YAHOO,
            )

            # Calculate change if possible
            if asset_price.close_price and asset_price.price:
                asset_price.change = asset_price.price - asset_price.close_price
                asset_price.change_percent = (
                    asset_price.change / asset_price.close_price
                ) * 100

            # Cache as dict
            await self.cache.set(cache_key, asset_price.to_dict(), ttl=60)
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
        """Fetch historical prices."""
        ticker_norm = self._to_yf_ticker(ticker)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        cache_key = f"yahoo:history:{ticker_norm}:{start_str}:{end_str}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            hist = await self._run(
                ticker_obj.history, start=start_str, end=end_str, interval=interval
            )

            # yfinance 1.0 may return None
            if hist is None:
                self.logger.warning(f"history() returned None for {ticker}")
                return []

            if hist.empty:
                return []

            prices = []
            for idx, row in hist.iterrows():
                # idx is Timestamp
                timestamp = idx.to_pydatetime()

                price = AssetPrice(
                    ticker=ticker,
                    price=Decimal(str(row["Close"])),
                    currency="USD",  # Default, might need to fetch from info
                    timestamp=timestamp,
                    volume=Decimal(str(row["Volume"])),
                    open_price=Decimal(str(row["Open"])),
                    high_price=Decimal(str(row["High"])),
                    low_price=Decimal(str(row["Low"])),
                    close_price=Decimal(str(row["Close"])),
                    source=DataSource.YAHOO,
                )
                prices.append(price)

            # Cache list of dicts
            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=3600)
            return prices

        except Exception as e:
            self.logger.error(f"Failed to fetch history for {ticker}: {e}")
            return []

    async def search_assets(self, query: AssetSearchQuery) -> List[AssetSearchResult]:
        """Search for assets (Yahoo doesn't support direct search well)."""
        return []

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Fetch financial statements."""
        # Keep existing implementation but ensure it works with new base class
        # ... (Same implementation as before, just copied over)
        cache_key = f"yahoo:financials:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)

        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)

            def fetch_financial_data():
                balance_sheet = ticker_obj.balance_sheet
                income_statement = ticker_obj.financials
                cash_flow = ticker_obj.cashflow
                info = ticker_obj.info
                return balance_sheet, income_statement, cash_flow, info

            balance_sheet, income_statement, cash_flow, info = await self._run(
                fetch_financial_data
            )

            company_info = {
                "公司名称": info.get("longName", info.get("shortName", "")),
                "股票代码": ticker_norm,
                "行业": info.get("industry", ""),
                "板块": info.get("sector", ""),
                "国家": info.get("country", ""),
                "网站": info.get("website", ""),
                "总市值": info.get("marketCap", 0),
                "员工人数": info.get("fullTimeEmployees", 0),
                "公司简介": info.get("longBusinessSummary", "")[:200],
            }

            # Convert DataFrames to JSON-serializable format
            def df_to_serializable(df):
                if df.empty:
                    return {}
                # Reset index to convert Timestamp index to column
                df_reset = df.reset_index()
                # Convert to dict with orient='records'
                return df_reset.to_dict(orient='records')
            
            # Clean function to handle Timestamp and other non-serializable objects
            def clean_for_json(obj):
                """Recursively clean object for JSON serialization."""
                import math
                from datetime import datetime
                
                if isinstance(obj, dict):
                    return {str(k): clean_for_json(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_for_json(item) for item in obj]
                elif isinstance(obj, (pd.Timestamp, datetime)):
                    return obj.isoformat() if hasattr(obj, 'isoformat') else str(obj)
                elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                    return None
                elif isinstance(obj, (int, float, str, bool, type(None))):
                    return obj
                else:
                    return str(obj)

            result = {
                "balance_sheet": clean_for_json(df_to_serializable(balance_sheet)),
                "income_statement": clean_for_json(df_to_serializable(income_statement)),
                "cash_flow": clean_for_json(df_to_serializable(cash_flow)),
                "financial_indicators": None,
                "company_info": company_info,
                "_raw_info": clean_for_json(info),
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")
