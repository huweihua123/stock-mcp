# src/server/domain/adapters/akshare_adapter.py
"""Akshare adapter for Chinese market data.

All methods are async via asyncio.run_in_executor to avoid blocking
the event loop.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.cninfo_helper import (
    _normalize_stock_code,
    fetch_cninfo_data,
)
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


class AkshareAdapter(BaseDataAdapter):
    name = "akshare"

    def __init__(self, cache):
        super().__init__(DataSource.AKSHARE)
        self.cache = cache
        self.logger = logger

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare AkShare's capabilities."""
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.SSE, Exchange.SZSE, Exchange.BSE, Exchange.HKEX},
            ),
            AdapterCapability(
                asset_type=AssetType.INDEX, exchanges={Exchange.SSE, Exchange.SZSE}
            ),
            AdapterCapability(
                asset_type=AssetType.ETF, exchanges={Exchange.SSE, Exchange.SZSE}
            ),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert EXCHANGE:SYMBOL to AKShare format."""
        if ":" in internal_ticker:
            return internal_ticker.split(":")[1]
        return internal_ticker

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert AKShare format to EXCHANGE:SYMBOL."""
        if source_ticker.startswith("6") and len(source_ticker) == 6:
            return f"SSE:{source_ticker}"
        elif (source_ticker.startswith("0") or source_ticker.startswith("3")) and len(
            source_ticker
        ) == 6:
            return f"SZSE:{source_ticker}"
        elif len(source_ticker) == 5:
            return f"HKEX:{source_ticker}"
        elif source_ticker.startswith("8") and len(source_ticker) == 6:
            return f"BSE:{source_ticker}"
        elif default_exchange:
            return f"{default_exchange}:{source_ticker}"
        else:
            return f"SSE:{source_ticker}"

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _to_ak_code(self, ticker: str) -> str:
        return self.convert_to_source_ticker(ticker)

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch detailed asset information."""
        cache_key = f"akshare:info:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset.model_validate(cached)

        symbol = self._to_ak_code(ticker)
        try:
            # Use stock_individual_info_em for A-shares
            if ticker.startswith("HKEX"):
                # HK stocks might need different API, for now fallback or simple
                return None

            df = await self._run(ak.stock_individual_info_em, symbol=symbol)
            if df.empty:
                return None

            info = {}
            for _, row in df.iterrows():
                key = row.get("item")
                val = row.get("value")
                if key:
                    info[key] = val

            exchange = ticker.split(":")[0]

            asset = Asset(
                ticker=ticker,
                asset_type=AssetType.STOCK,
                name=str(info.get("股票简称", ticker)),
                market_info=MarketInfo(
                    exchange=exchange,
                    country="CN",
                    currency="CNY",
                    timezone="Asia/Shanghai",
                    market_status=MarketStatus.UNKNOWN,
                ),
                source_mappings={DataSource.AKSHARE: symbol},
                properties={
                    "industry": str(info.get("行业", "")),
                    "listing_date": str(info.get("上市时间", "")),
                    "total_shares": str(info.get("总股本", "")),
                    "float_shares": str(info.get("流通股", "")),
                },
            )

            await self.cache.set(cache_key, asset.model_dump(), ttl=3600)
            return asset
        except Exception as e:
            self.logger.warning(f"Failed to fetch asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price."""
        cache_key = f"akshare:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        symbol = self._to_ak_code(ticker)
        is_hk = ticker.startswith("HKEX:")

        try:
            if is_hk:
                df = await self._run(
                    ak.stock_hk_hist_min_em, symbol=symbol, period="1", adjust=""
                )
            else:
                df = await self._run(
                    ak.stock_zh_a_hist_min_em, symbol=symbol, period="1", adjust="qfq"
                )

            if df.empty:
                # Fallback to daily
                if is_hk:
                    df = await self._run(
                        ak.stock_hk_hist,
                        symbol=symbol,
                        period="daily",
                        start_date="20240101",
                        adjust="qfq",
                    )
                else:
                    df = await self._run(
                        ak.stock_zh_a_hist,
                        symbol=symbol,
                        period="daily",
                        start_date="20240101",
                        adjust="qfq",
                    )

            if df.empty:
                return None

            row = df.iloc[-1]
            price_val = float(row["收盘"])

            # Try to get other fields if available (daily data usually has them)
            open_val = float(row.get("开盘", 0))
            high_val = float(row.get("最高", 0))
            low_val = float(row.get("最低", 0))
            prev_close = float(row.get("前收盘", 0))  # Might not exist
            volume = float(row.get("成交量", 0))

            # Date handling
            date_val = row.get("日期") or row.get("时间")
            if isinstance(date_val, str):
                try:
                    timestamp = datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
                except:
                    try:
                        timestamp = datetime.strptime(date_val, "%Y-%m-%d")
                    except:
                        timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()

            asset_price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(price_val)),
                currency="HKD" if is_hk else "CNY",
                timestamp=timestamp,
                volume=Decimal(str(volume)),
                open_price=Decimal(str(open_val)) if open_val else None,
                high_price=Decimal(str(high_val)) if high_val else None,
                low_price=Decimal(str(low_val)) if low_val else None,
                close_price=None,  # Akshare history doesn't give prev close easily in this call
                source=DataSource.AKSHARE,
            )

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
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        cache_key = f"akshare:history:{ticker}:{start_str}:{end_str}:{interval}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(item) for item in cached]

        symbol = self._to_ak_code(ticker)
        is_hk = ticker.startswith("HKEX:")

        try:
            if is_hk:
                # HK daily
                df = await self._run(
                    ak.stock_hk_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",
                )
            else:
                # A-share daily
                df = await self._run(
                    ak.stock_zh_a_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",
                )

            if df.empty:
                return []

            prices = []
            for _, row in df.iterrows():
                date_val = row["日期"]
                if isinstance(date_val, str):
                    timestamp = datetime.strptime(date_val, "%Y-%m-%d")
                else:
                    timestamp = date_val  # Assuming date object

                price = AssetPrice(
                    ticker=ticker,
                    price=Decimal(str(row["收盘"])),
                    currency="HKD" if is_hk else "CNY",
                    timestamp=timestamp,
                    volume=Decimal(str(row["成交量"])),
                    open_price=Decimal(str(row["开盘"])),
                    high_price=Decimal(str(row["最高"])),
                    low_price=Decimal(str(row["最低"])),
                    close_price=Decimal(str(row["收盘"])),
                    source=DataSource.AKSHARE,
                )
                prices.append(price)

            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=3600)
            return prices

        except Exception as e:
            self.logger.error(f"Failed to fetch history for {ticker}: {e}")
            return []

    async def _get_all_stocks_cached(self) -> List[Dict]:
        """Helper to get all stocks with caching."""
        cache_key = "akshare:all_stocks_v2"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(ak.stock_zh_a_spot_em)
            if df.empty:
                return []

            stocks = []
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                if not code:
                    continue

                if code.startswith("6"):
                    exchange = "SSE"
                elif code.startswith("0") or code.startswith("3"):
                    exchange = "SZSE"
                elif code.startswith("8"):
                    exchange = "BSE"
                else:
                    exchange = "SSE"

                stocks.append(
                    {"ticker": f"{exchange}:{code}", "code": code, "name": name}
                )

            await self.cache.set(cache_key, stocks, ttl=3600)
            return stocks
        except Exception as e:
            self.logger.warning(f"Failed to fetch all stocks: {e}")
            return []

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Fetch financial statements and company info for A-share stocks."""
        cache_key = f"akshare:financials:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        symbol = self._to_ak_code(ticker)

        try:
            # Fetch all financial data in parallel
            balance_task = self._run(
                ak.stock_financial_report_sina, stock=symbol, symbol="资产负债表"
            )
            income_task = self._run(
                ak.stock_financial_report_sina, stock=symbol, symbol="利润表"
            )
            cashflow_task = self._run(
                ak.stock_financial_report_sina, stock=symbol, symbol="现金流量表"
            )
            indicator_task = self._run(
                ak.stock_financial_analysis_indicator, symbol=symbol
            )
            company_task = self._run(ak.stock_individual_info_em, symbol=symbol)

            balance_df, income_df, cashflow_df, indicator_df, company_df = (
                await asyncio.gather(
                    balance_task,
                    income_task,
                    cashflow_task,
                    indicator_task,
                    company_task,
                    return_exceptions=True,
                )
            )

            # Convert company DataFrame to dict
            company_info = {}
            if (
                not isinstance(company_df, Exception)
                and company_df is not None
                and not company_df.empty
            ):
                for _, row in company_df.iterrows():
                    key = row.get("item", row.get("项目", ""))
                    value = row.get("value", row.get("值", ""))
                    if key:
                        company_info[key] = value

            # Helper function to convert DataFrame to serializable format
            def df_to_dict(df):
                if isinstance(df, Exception) or df is None or df.empty:
                    return None
                # Convert DataFrame to list of dicts (JSON serializable)
                return df.to_dict("records")

            result = {
                "balance_sheet": df_to_dict(balance_df),
                "income_statement": df_to_dict(income_df),
                "cash_flow": df_to_dict(cashflow_df),
                "financial_indicators": df_to_dict(indicator_df),
                "company_info": company_info,
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch A-share filings/announcements from CNINFO.

        Args:
            ticker: Stock ticker (e.g., "SSE:600519" or "600519")
            start_date: Start date for filtering (optional)
            end_date: End date for filtering (optional)
            limit: Maximum number of filings to return
            filing_types: List of filing types (e.g., ["annual", "quarterly"])

        Returns:
            List of filing dictionaries with metadata and PDF URLs
        """
        try:
            # Normalize stock code
            stock_code = _normalize_stock_code(ticker)

            # Use provided filing_types or default to all
            report_types = filing_types or ["annual", "semi-annual", "quarterly"]

            # Extract years from date range or use recent years
            years = []
            if start_date and end_date:
                start_year = start_date.year
                end_year = end_date.year
                years = list(range(start_year, end_year + 1))

            # Fetch data from CNINFO
            filings_data = await fetch_cninfo_data(
                stock_code=stock_code,
                report_types=report_types,
                years=years,
                quarters=[],  # No quarter filtering by default
                limit=limit,
            )

            # Transform to standard format
            results = []
            for filing in filings_data:
                # Filter by date if specified
                if start_date or end_date:
                    filing_date_str = filing.get("filing_date", "")
                    try:
                        filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
                        if start_date and filing_date < start_date:
                            continue
                        if end_date and filing_date > end_date:
                            continue
                    except ValueError:
                        pass  # Skip date filtering if parsing fails

                result = {
                    "filing_id": filing.get("announcement_id", ""),
                    "symbol": filing.get("stock_code", ""),
                    "company_name": filing.get("company", ""),
                    "exchange": filing.get("market", ""),
                    "title": filing.get("announcement_title", ""),
                    "type": filing.get("doc_type", ""),
                    "form": self._map_report_type_to_form(filing.get("doc_type", "")),
                    "filing_date": filing.get("filing_date", ""),
                    "period_of_report": filing.get("period_of_report", ""),
                    "url": filing.get("pdf_url", ""),
                    "content_summary": filing.get("announcement_title", "")[:200],
                }
                results.append(result)

            return results[:limit]

        except Exception as e:
            self.logger.error(f"Failed to fetch filings for {ticker}: {e}")
            return []

    def _map_report_type_to_form(self, doc_type: str) -> str:
        """Map English report type to Chinese form name.

        Args:
            doc_type: Report type ("annual", "semi-annual", "quarterly")

        Returns:
            Chinese form name
        """
        mapping = {
            "annual": "年报",
            "semi-annual": "半年报",
            "quarterly": "季报",
        }
        return mapping.get(doc_type, doc_type)

    # Keep extra methods for NewsService usage
    async def get_news(self, ticker: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch specific stock news from Eastmoney."""
        # ... (Keep existing implementation)
        # For brevity, I'm not pasting the full news implementation here again unless requested,
        # but in a real refactor I would keep it.
        # I will include it to avoid breaking NewsService.
        cache_key = f"akshare:news:{ticker}:{limit}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        symbol = self._to_ak_code(ticker)
        try:
            import requests
            import json
            from datetime import datetime as dt

            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://guba.eastmoney.com/",
            }

            url = "https://search-api-web.eastmoney.com/search/jsonp"
            params = {
                "cb": "jQuery_callback",
                "param": json.dumps(
                    {
                        "uid": "",
                        "keyword": symbol,
                        "type": ["cmsArticleWebOld"],
                        "client": "web",
                        "clientType": "web",
                        "clientVersion": "curr",
                        "param": {
                            "cmsArticleWebOld": {
                                "searchScope": "default",
                                "sort": "default",
                                "pageIndex": 1,
                                "pageSize": max(limit, 20),
                                "preTag": "",
                                "postTag": "",
                            }
                        },
                    }
                ),
                "_": str(int(dt.now().timestamp() * 1000)),
            }

            def fetch_news():
                response = requests.get(url, params=params, headers=headers, timeout=10)
                if response.status_code != 200:
                    return []
                text = response.text
                start = text.find("(")
                end = text.rfind(")")
                if start == -1 or end == -1:
                    return []
                json_text = text[start + 1 : end]
                data = json.loads(json_text)
                return data.get("result", {}).get("cmsArticleWebOld", [])

            articles = await self._run(fetch_news)

            news_list = []
            for article in articles[:limit]:
                news_list.append(
                    {
                        "title": article.get("title", ""),
                        "url": article.get("url", ""),
                        "publish_time": article.get("date", "")
                        or article.get("showTime", ""),
                        "source": article.get("mediaName", "Eastmoney"),
                        "snippet": article.get("content", "")[:200],
                        "keyword": symbol,
                    }
                )

            await self.cache.set(cache_key, news_list, ttl=600)
            return news_list
        except Exception as e:
            self.logger.error(f"Failed to fetch news for {ticker}: {e}")
            return []
