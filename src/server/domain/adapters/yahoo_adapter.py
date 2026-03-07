# src/server/domain/adapters/yahoo_adapter.py
"""YahooFinance adapter using yfinance.

All methods are async via asyncio.run_in_executor to avoid blocking.
"""

import asyncio
import logging
import time
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
        self.proxy_url = proxy_url

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
                self.logger.warning(
                    f"⚠️  Failed to set proxy via yf.config: {e}, continue without global env proxy mutation"
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
            AdapterCapability(asset_type=AssetType.CRYPTO, exchanges={Exchange.CRYPTO}),
            AdapterCapability(asset_type=AssetType.FX, exchanges={Exchange.FOREX}),
            AdapterCapability(
                asset_type=AssetType.COMMODITY_SPOT, exchanges={Exchange.OTC}
            ),
            AdapterCapability(
                asset_type=AssetType.COMMODITY_FUTURE,
                exchanges={Exchange.COMEX, Exchange.NYMEX, Exchange.CME, Exchange.ICE},
            ),
        ]

    def get_supported_asset_types(self) -> List[AssetType]:
        """Get list of supported asset types."""
        return [
            AssetType.STOCK,
            AssetType.ETF,
            AssetType.INDEX,
            AssetType.FX,
            AssetType.COMMODITY_SPOT,
            AssetType.COMMODITY_FUTURE,
            AssetType.CRYPTO,
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

        # Handle FX (Yahoo: EURUSD=X)
        if exchange == "FOREX":
            return f"{symbol}=X"

        # Handle commodity spot (Yahoo: XAUUSD=X, XAGUSD=X)
        if exchange == "OTC":
            return f"{symbol}=X"

        # Handle commodity futures (Yahoo: GC=F, SI=F, CL=F)
        if exchange in ["COMEX", "NYMEX", "CME", "ICE"]:
            if symbol.endswith("=F"):
                return symbol
            return f"{symbol}=F"

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
        # FX and commodity spot symbols: EURUSD=X, XAUUSD=X
        if source_ticker.endswith("=X"):
            core = source_ticker.replace("=X", "").upper()
            if core in {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}:
                return f"OTC:{core}"
            if len(core) == 6 and core.isalpha():
                return f"FOREX:{core}"
            return f"OTC:{core}"

        # Commodity futures: GC=F, SI=F
        if source_ticker.endswith("=F"):
            core = source_ticker.replace("=F", "").upper()
            comex = {"GC", "SI", "HG"}
            nymex = {"CL", "NG"}
            if core in comex:
                return f"COMEX:{core}"
            if core in nymex:
                return f"NYMEX:{core}"
            return f"CME:{core}"

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

            asset_type = AssetType.STOCK
            if ":" in ticker:
                exchange = ticker.split(":", 1)[0]
                if exchange == "FOREX":
                    asset_type = AssetType.FX
                elif exchange == "OTC":
                    asset_type = AssetType.COMMODITY_SPOT
                elif exchange in {"COMEX", "NYMEX", "CME", "ICE"}:
                    asset_type = AssetType.COMMODITY_FUTURE

            asset = Asset(
                ticker=ticker,
                asset_type=asset_type,
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
                    price = getattr(fast_info, "last_price", None)
                    currency = getattr(fast_info, "currency", "USD") or "USD"
                    volume = getattr(fast_info, "last_volume", 0) or 0
                    open_price = getattr(fast_info, "open", None)
                    high_price = getattr(fast_info, "day_high", None)
                    low_price = getattr(fast_info, "day_low", None)
                    close_price = getattr(fast_info, "previous_close", None)
                    market_cap = getattr(fast_info, "market_cap", None)
            except Exception as fast_info_error:
                self.logger.debug(
                    f"fast_info failed for {ticker}, trying info: {fast_info_error}"
                )

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
                return df_reset.to_dict(orient="records")

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
                    return obj.isoformat() if hasattr(obj, "isoformat") else str(obj)
                elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                    return None
                elif isinstance(obj, (int, float, str, bool, type(None))):
                    return obj
                else:
                    return str(obj)

            result = {
                "balance_sheet": clean_for_json(df_to_serializable(balance_sheet)),
                "income_statement": clean_for_json(
                    df_to_serializable(income_statement)
                ),
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

    # =========================================================================
    # US-market specific implementations
    # =========================================================================

    async def get_earnings_history(
        self, ticker: str, quarters: int = 8
    ) -> Dict[str, Any]:
        """Fetch EPS history: estimate vs actual and surprise %."""
        t0 = time.perf_counter()
        cache_key = f"yahoo:earnings:{ticker}:{quarters}"
        cached = await self.cache.get(cache_key)
        if cached:
            self.logger.info(
                "yahoo.get_earnings_history cache hit",
                ticker=ticker,
                quarters=quarters,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        try:
            t_ticker = time.perf_counter()
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            t_fetch = time.perf_counter()

            def _fetch():
                return ticker_obj.earnings_history

            raw = await self._run(_fetch)
            t_parse = time.perf_counter()
            if raw is None or (hasattr(raw, "empty") and raw.empty):
                self.logger.warning(
                    "yahoo.get_earnings_history empty response",
                    ticker=ticker,
                    ticker_norm=ticker_norm,
                    build_ticker_ms=int((t_fetch - t_ticker) * 1000),
                    fetch_ms=int((t_parse - t_fetch) * 1000),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                return {"ticker": ticker, "quarters": []}

            rows = []
            df = raw.reset_index() if hasattr(raw, "reset_index") else raw
            for _, row in df.iterrows():
                actual = row.get("epsActual") or row.get("Reported EPS")
                estimate = row.get("epsEstimate") or row.get("EPS Estimate")
                surprise = row.get("surprisePercent") or row.get("Surprise(%)")
                date_val = (
                    row.get("Earnings Date") or row.get("Quarter") or row.get("index")
                )
                rows.append(
                    {
                        "date": str(date_val)[:10] if date_val is not None else None,
                        "actual_eps": float(actual) if actual is not None else None,
                        "estimated_eps": (
                            float(estimate) if estimate is not None else None
                        ),
                        "surprise_pct": (
                            float(surprise) if surprise is not None else None
                        ),
                    }
                )
            rows = rows[-quarters:]
            result = {"ticker": ticker, "quarters": rows}
            await self.cache.set(cache_key, result, ttl=3600)
            self.logger.info(
                "yahoo.get_earnings_history success",
                ticker=ticker,
                ticker_norm=ticker_norm,
                quarters=quarters,
                rows=len(rows),
                build_ticker_ms=int((t_fetch - t_ticker) * 1000),
                fetch_ms=int((t_parse - t_fetch) * 1000),
                parse_cache_ms=int((time.perf_counter() - t_parse) * 1000),
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
            return result
        except Exception as e:
            self.logger.error(
                f"get_earnings_history failed for {ticker}: {e}",
                ticker=ticker,
                ticker_norm=ticker_norm,
                quarters=quarters,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
            raise ValueError(f"get_earnings_history failed for {ticker}: {e}")

    async def get_cash_flow_quality(self, ticker: str) -> Dict[str, Any]:
        """Fetch operating/free cash flow and FCF/net-income ratio."""
        cache_key = f"yahoo:cashflow:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)

            def _fetch():
                return ticker_obj.cashflow, ticker_obj.financials

            cf_df, inc_df = await self._run(_fetch)

            import math

            def _safe(val):
                if val is None:
                    return None
                try:
                    v = float(val)
                    return None if math.isnan(v) or math.isinf(v) else v
                except Exception:
                    return None

            annual = []
            if cf_df is not None and not cf_df.empty:
                for col in cf_df.columns:
                    year = str(col)[:4]
                    op_cf = _safe(
                        cf_df.loc["Operating Cash Flow", col]
                        if "Operating Cash Flow" in cf_df.index
                        else None
                    )
                    capex = _safe(
                        cf_df.loc["Capital Expenditure", col]
                        if "Capital Expenditure" in cf_df.index
                        else None
                    )
                    free_cf = (op_cf or 0) + (capex or 0) if op_cf is not None else None
                    net_inc = None
                    if (
                        inc_df is not None
                        and not inc_df.empty
                        and col in inc_df.columns
                    ):
                        net_inc = _safe(
                            inc_df.loc["Net Income", col]
                            if "Net Income" in inc_df.index
                            else None
                        )
                    fcf_ratio = (
                        (free_cf / net_inc)
                        if (free_cf is not None and net_inc and net_inc != 0)
                        else None
                    )
                    annual.append(
                        {
                            "year": year,
                            "operating_cf": op_cf,
                            "capex": capex,
                            "free_cf": free_cf,
                            "net_income": net_inc,
                            "fcf_ratio": (
                                round(fcf_ratio, 4) if fcf_ratio is not None else None
                            ),
                        }
                    )

            result = {"ticker": ticker, "annual": annual}
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"get_cash_flow_quality failed for {ticker}: {e}")
            raise ValueError(f"get_cash_flow_quality failed for {ticker}: {e}")

    async def get_us_valuation_metrics(self, ticker: str) -> Dict[str, Any]:
        """Fetch US stock valuation: PE/PS/PB/EV_EBITDA."""
        cache_key = f"yahoo:us_val:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            info = await self._run(lambda: ticker_obj.info)
            if not info or not isinstance(info, dict):
                raise ValueError(f"No info for {ticker}")

            import math

            def _safe(key):
                v = info.get(key)
                if v is None:
                    return None
                try:
                    f = float(v)
                    return None if math.isnan(f) or math.isinf(f) else f
                except Exception:
                    return None

            result = {
                "ticker": ticker,
                "pe_ttm": _safe("trailingPE"),
                "pe_forward": _safe("forwardPE"),
                "ps_ttm": _safe("priceToSalesTrailing12Months"),
                "pb": _safe("priceToBook"),
                "ev_ebitda": _safe("enterpriseToEbitda"),
                "peg_ratio": _safe("pegRatio"),
                "market_cap": _safe("marketCap"),
                "enterprise_value": _safe("enterpriseValue"),
                "beta": _safe("beta"),
                "dividend_yield": _safe("dividendYield"),
                "name": info.get("longName") or info.get("shortName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"get_us_valuation_metrics failed for {ticker}: {e}")
            raise ValueError(f"get_us_valuation_metrics failed for {ticker}: {e}")

    async def get_us_institutional_holdings(self, ticker: str) -> Dict[str, Any]:
        """Fetch top institutional holders and recent change."""
        cache_key = f"yahoo:institutions:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)

            def _fetch():
                return ticker_obj.institutional_holders, ticker_obj.major_holders

            inst_df, major_df = await self._run(_fetch)

            holders = []
            if inst_df is not None and not inst_df.empty:
                for _, row in inst_df.head(15).iterrows():
                    pct = row.get("pctHeld") or row.get("% Out")
                    shares = row.get("Shares") or row.get("shares")
                    change = row.get("Change") or row.get("change")
                    date_filed = row.get("Date Reported") or row.get("dateReported")
                    holders.append(
                        {
                            "name": str(row.get("Holder") or row.get("holder") or ""),
                            "pct_held": (
                                round(float(pct) * 100, 2) if pct is not None else None
                            ),
                            "shares": int(shares) if shares is not None else None,
                            "change_pct": (
                                round(float(change) * 100, 2)
                                if change is not None
                                else None
                            ),
                            "filing_date": (
                                str(date_filed)[:10] if date_filed is not None else None
                            ),
                        }
                    )

            major = {}
            if major_df is not None and not major_df.empty:
                for _, row in major_df.iterrows():
                    val = row.iloc[0] if len(row) > 0 else None
                    label = row.iloc[1] if len(row) > 1 else None
                    if label and val is not None:
                        major[str(label)] = str(val)

            result = {"ticker": ticker, "holders": holders, "major_holders": major}
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"get_us_institutional_holdings failed for {ticker}: {e}")
            raise ValueError(f"get_us_institutional_holdings failed for {ticker}: {e}")

    async def get_us_price_history(
        self, ticker: str, days: int = 60, interval: str = "1d"
    ) -> Dict[str, Any]:
        """Fetch OHLCV klines for a US stock."""
        from datetime import timedelta

        cache_key = f"yahoo:us_price_hist:{ticker}:{days}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            hist = await self._run(
                ticker_obj.history,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
            )
            if hist is None or (hasattr(hist, "empty") and hist.empty):
                return {"ticker": ticker, "interval": interval, "bars": []}

            bars = []
            for idx, row in hist.iterrows():
                bars.append(
                    {
                        "date": str(idx)[:10],
                        "open": round(float(row["Open"]), 4),
                        "high": round(float(row["High"]), 4),
                        "low": round(float(row["Low"]), 4),
                        "close": round(float(row["Close"]), 4),
                        "volume": int(row["Volume"]),
                    }
                )
            result = {"ticker": ticker, "interval": interval, "bars": bars}
            await self.cache.set(cache_key, result, ttl=600)
            return result
        except Exception as e:
            self.logger.error(f"get_us_price_history failed for {ticker}: {e}")
            raise ValueError(f"get_us_price_history failed for {ticker}: {e}")

    async def get_us_volume_analysis(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch volume metrics: avg volume, relative volume, OBV trend."""
        from datetime import timedelta

        cache_key = f"yahoo:us_vol:{ticker}:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        ticker_norm = self._to_yf_ticker(ticker)
        end = datetime.utcnow()
        start = end - timedelta(days=max(days + 20, 60))  # extra days for avg
        try:
            ticker_obj = await self._run(yf.Ticker, ticker_norm)
            hist = await self._run(
                ticker_obj.history,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
            )
            if hist is None or (hasattr(hist, "empty") and hist.empty):
                return {"ticker": ticker, "bars": []}

            vols = hist["Volume"].values
            avg_vol_20 = (
                float(vols[-20:].mean()) if len(vols) >= 20 else float(vols.mean())
            )
            current_vol = float(vols[-1]) if len(vols) > 0 else 0.0
            rvol = current_vol / avg_vol_20 if avg_vol_20 > 0 else None

            # OBV
            obv = 0.0
            obv_series = []
            closes = hist["Close"].values
            for i in range(1, len(vols)):
                if closes[i] > closes[i - 1]:
                    obv += vols[i]
                elif closes[i] < closes[i - 1]:
                    obv -= vols[i]
                obv_series.append(obv)
            obv_trend = (
                "up"
                if (len(obv_series) >= 5 and obv_series[-1] > obv_series[-5])
                else "down" if len(obv_series) >= 5 else "flat"
            )

            bars = []
            hist_tail = hist.tail(days)
            avg_20_rolling = (
                float(hist["Volume"].rolling(20).mean().iloc[-1])
                if len(hist) >= 20
                else avg_vol_20
            )
            for idx, row in hist_tail.iterrows():
                v = float(row["Volume"])
                bars.append(
                    {
                        "date": str(idx)[:10],
                        "volume": int(v),
                        "rvol": (
                            round(v / avg_20_rolling, 2) if avg_20_rolling > 0 else None
                        ),
                    }
                )

            result = {
                "ticker": ticker,
                "avg_volume_20d": round(avg_vol_20, 0),
                "current_volume": round(current_vol, 0),
                "rvol": round(rvol, 2) if rvol is not None else None,
                "obv_trend": obv_trend,
                "bars": bars,
            }
            await self.cache.set(cache_key, result, ttl=600)
            return result
        except Exception as e:
            self.logger.error(f"get_us_volume_analysis failed for {ticker}: {e}")
            raise ValueError(f"get_us_volume_analysis failed for {ticker}: {e}")

    async def get_us_sector_etf_analysis(
        self, sector_name: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch US sector ETF klines by sector name."""
        from datetime import timedelta

        # Sector → ETF ticker mapping
        SECTOR_ETF_MAP: Dict[str, str] = {
            "technology": "XLK",
            "tech": "XLK",
            "科技": "XLK",
            "financials": "XLF",
            "finance": "XLF",
            "金融": "XLF",
            "healthcare": "XLV",
            "health": "XLV",
            "医疗": "XLV",
            "医疗保健": "XLV",
            "energy": "XLE",
            "能源": "XLE",
            "consumer discretionary": "XLY",
            "consumer staples": "XLP",
            "consumer": "XLY",
            "消费": "XLY",
            "日常消费": "XLP",
            "utilities": "XLU",
            "公用事业": "XLU",
            "industrials": "XLI",
            "industrial": "XLI",
            "工业": "XLI",
            "materials": "XLB",
            "材料": "XLB",
            "real estate": "XLRE",
            "realestate": "XLRE",
            "房地产": "XLRE",
            "communication": "XLC",
            "communications": "XLC",
            "通信": "XLC",
            "semiconductor": "SOXX",
            "semiconductors": "SOXX",
            "半导体": "SOXX",
            "biotech": "XBI",
            "biotechnology": "XBI",
            "生物技术": "XBI",
            "software": "IGV",
            "软件": "IGV",
            "cloud": "SKYY",
            "云计算": "SKYY",
            "ev": "DRIV",
            "electric vehicle": "DRIV",
            "新能源车": "DRIV",
            "cybersecurity": "HACK",
            "网络安全": "HACK",
            "ai": "AIQ",
            "人工智能": "AIQ",
        }

        key = sector_name.strip().lower()
        etf_ticker = SECTOR_ETF_MAP.get(key)
        if not etf_ticker:
            # Fuzzy: try partial match
            for k, v in SECTOR_ETF_MAP.items():
                if k in key or key in k:
                    etf_ticker = v
                    break
        if not etf_ticker:
            etf_ticker = "SPY"  # fallback to S&P500

        cache_key = f"yahoo:sector_etf:{etf_ticker}:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            cached["sector_name"] = sector_name
            return cached

        end = datetime.utcnow()
        start = end - timedelta(days=days + 5)
        try:
            ticker_obj = await self._run(yf.Ticker, etf_ticker)
            hist = await self._run(
                ticker_obj.history,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
            )
            if hist is None or (hasattr(hist, "empty") and hist.empty):
                return {
                    "sector_name": sector_name,
                    "etf_ticker": etf_ticker,
                    "bars": [],
                    "trend_summary": "no data",
                }

            bars = []
            closes = []
            for idx, row in hist.tail(days).iterrows():
                c = float(row["Close"])
                closes.append(c)
                bars.append(
                    {"date": str(idx)[:10], "close": round(c, 4), "change_pct": None}
                )

            # Fill change_pct
            for i in range(1, len(bars)):
                prev = closes[i - 1]
                if prev > 0:
                    bars[i]["change_pct"] = round((closes[i] - prev) / prev * 100, 2)

            total_chg = (
                ((closes[-1] - closes[0]) / closes[0] * 100) if closes[0] > 0 else 0
            )
            trend_summary = f"{etf_ticker} {days}d return: {total_chg:+.2f}%"

            result = {
                "sector_name": sector_name,
                "etf_ticker": etf_ticker,
                "bars": bars,
                "trend_summary": trend_summary,
                "total_change_pct": round(total_chg, 2),
            }
            await self.cache.set(cache_key, result, ttl=600)
            return result
        except Exception as e:
            self.logger.error(
                f"get_us_sector_etf_analysis failed for {sector_name}: {e}"
            )
            raise ValueError(
                f"get_us_sector_etf_analysis failed for {sector_name}: {e}"
            )
