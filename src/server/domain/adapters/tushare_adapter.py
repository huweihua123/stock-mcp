# src/server/domain/adapters/tushare_adapter.py
"""TushareAdapter provides price & historical data via Tushare API.

All calls are wrapped with asyncio.run_in_executor to keep
the event loop non‑blocking.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pandas as pd

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


class TushareAdapter(BaseDataAdapter):
    name = "tushare"

    def __init__(self, tushare_conn, cache):
        super().__init__(DataSource.TUSHARE)
        self.tushare_conn = tushare_conn
        self.cache = cache
        self.logger = logger

    def get_capabilities(self) -> List[AdapterCapability]:
        """Declare Tushare's capabilities."""
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.SSE, Exchange.SZSE, Exchange.BSE},
            ),
            AdapterCapability(
                asset_type=AssetType.INDEX, exchanges={Exchange.SSE, Exchange.SZSE}
            ),
            AdapterCapability(
                asset_type=AssetType.ETF, exchanges={Exchange.SSE, Exchange.SZSE}
            ),
            AdapterCapability(
                asset_type=AssetType.FUND, exchanges={Exchange.SSE, Exchange.SZSE}
            ),
        ]

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert EXCHANGE:SYMBOL to Tushare format."""
        if ":" not in internal_ticker:
            return internal_ticker

        exchange, symbol = internal_ticker.split(":", 1)

        if exchange == "SSE":
            return f"{symbol}.SH"
        elif exchange == "SZSE":
            return f"{symbol}.SZ"
        elif exchange == "BSE":
            return f"{symbol}.BJ"
        else:
            return symbol

    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert Tushare format to EXCHANGE:SYMBOL."""
        if "." in source_ticker:
            symbol, suffix = source_ticker.split(".", 1)
            if suffix == "SH":
                return f"SSE:{symbol}"
            elif suffix == "SZ":
                return f"SZSE:{symbol}"
            elif suffix == "BJ":
                return f"BSE:{symbol}"

        return source_ticker

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _to_ts_code(self, ticker: str) -> str:
        """Convert internal ticker to Tushare format."""
        return self.convert_to_source_ticker(ticker)

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Get asset information."""
        client = self.tushare_conn.get_client()
        if client is None:
            return None

        ts_code = self._to_ts_code(ticker)
        cache_key = f"tushare:info:{ts_code}"
        cached = await self.cache.get(cache_key)
        if cached:
            return Asset(**cached)

        try:
            # Use stock_basic to get info
            df = await self._run(
                client.stock_basic,
                ts_code=ts_code,
                fields="ts_code,symbol,name,fullname,market,list_date,curr_type",
            )

            if df.empty:
                return None

            row = df.iloc[0]

            asset = Asset(
                ticker=ticker,
                name=row.get("name", ""),
                description=row.get("fullname", ""),
                asset_type=AssetType.STOCK,
                exchange=(
                    Exchange.SSE
                    if ts_code.endswith(".SH")
                    else Exchange.SZSE if ts_code.endswith(".SZ") else Exchange.BSE
                ),
                currency=row.get("curr_type", "CNY"),
                market_info=MarketInfo(
                    market_status=MarketStatus.OPEN,  # Simplified
                    exchange_timezone="Asia/Shanghai",
                ),
            )

            await self.cache.set(cache_key, asset.model_dump(mode="json"), ttl=86400)
            return asset

        except Exception as e:
            self.logger.error(f"Failed to get asset info for {ticker}: {e}")
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Get real-time price."""
        cache_key = f"tushare:price:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return AssetPrice.from_dict(cached)

        client = self.tushare_conn.get_client()
        if client is None:
            return None

        ts_code = self._to_ts_code(ticker)

        try:
            # Use daily interface with limit 1 for latest price (Tushare doesn't have real-time free API easily)
            df = await self._run(client.daily, ts_code=ts_code, limit=1)

            if df.empty:
                return None

            row = df.iloc[0]
            price = AssetPrice(
                ticker=ticker,
                price=Decimal(str(row["close"])),
                currency="CNY",
                timestamp=datetime.strptime(row["trade_date"], "%Y%m%d"),
                volume=Decimal(str(row["vol"])),
                open_price=Decimal(str(row["open"])),
                high_price=Decimal(str(row["high"])),
                low_price=Decimal(str(row["low"])),
                close_price=Decimal(str(row["close"])),
                change_amount=Decimal(str(row["change"])) if "change" in row else None,
                change_percentage=(
                    Decimal(str(row["pct_chg"])) if "pct_chg" in row else None
                ),
                source=self.source,
            )

            await self.cache.set(cache_key, price.to_dict(), ttl=300)
            return price

        except Exception as e:
            self.logger.error(f"Failed to get price for {ticker}: {e}")
            return None

    async def get_multiple_prices(
        self, tickers: List[str]
    ) -> Dict[str, Optional[AssetPrice]]:
        """Get multiple prices."""
        # Tushare daily can take multiple codes separated by comma
        ts_codes = [self._to_ts_code(t) for t in tickers]
        ts_code_str = ",".join(ts_codes)

        client = self.tushare_conn.get_client()
        if client is None:
            return {t: None for t in tickers}

        try:
            # Get latest date first to query multiple stocks for that date
            # This is tricky with Tushare as different stocks might have different trading days?
            # We'll just loop for now as Tushare limits are generous enough for small batches or use single calls
            # Actually, let's just use parallel calls to get_real_time_price for simplicity and robustness
            tasks = [self.get_real_time_price(t) for t in tickers]
            results = await asyncio.gather(*tasks)
            return dict(zip(tickers, results))

        except Exception as e:
            self.logger.error(f"Failed to get multiple prices: {e}")
            return {t: None for t in tickers}

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Get historical prices."""
        cache_key = (
            f"tushare:history:{ticker}:{start_date.date()}:{end_date.date()}:{interval}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return [AssetPrice.from_dict(p) for p in cached]

        client = self.tushare_conn.get_client()
        if client is None:
            return []

        ts_code = self._to_ts_code(ticker)

        try:
            df = await self._run(
                client.daily,
                ts_code=ts_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if df.empty:
                return []

            prices = []
            # Tushare returns data in descending order by default
            for _, row in df.iterrows():
                prices.append(
                    AssetPrice(
                        ticker=ticker,
                        price=Decimal(str(row["close"])),
                        currency="CNY",
                        timestamp=datetime.strptime(row["trade_date"], "%Y%m%d"),
                        volume=Decimal(str(row["vol"])),
                        open_price=Decimal(str(row["open"])),
                        high_price=Decimal(str(row["high"])),
                        low_price=Decimal(str(row["low"])),
                        close_price=Decimal(str(row["close"])),
                        change_amount=(
                            Decimal(str(row["change"])) if "change" in row else None
                        ),
                        change_percentage=(
                            Decimal(str(row["pct_chg"])) if "pct_chg" in row else None
                        ),
                        source=self.source,
                    )
                )

            # Sort by date ascending
            prices.sort(key=lambda x: x.timestamp)

            await self.cache.set(cache_key, [p.to_dict() for p in prices], ttl=600)
            return prices

        except Exception as e:
            self.logger.error(f"Failed to get history for {ticker}: {e}")
            return []

    async def search_assets(self, query: AssetSearchQuery) -> List[AssetSearchResult]:
        """Search assets."""
        client = self.tushare_conn.get_client()
        if client is None:
            return []

        cache_key = "tushare:stock_basic"
        basic_data = await self.cache.get(cache_key)

        if not basic_data:
            self.logger.info("Fetching stock_basic from Tushare...")
            try:
                df = await self._run(
                    client.stock_basic,
                    exchange="",
                    list_status="L",
                    fields="ts_code,symbol,name,market",
                )
                basic_data = df.to_dict("records")
                await self.cache.set(cache_key, basic_data, ttl=86400)
            except Exception as e:
                self.logger.error(f"Failed to fetch stock_basic: {e}")
                return []

        results = []
        q = query.query.upper()
        limit = query.limit or 10

        for item in basic_data:
            ts_code = item.get("ts_code", "")
            name = item.get("name", "")
            symbol = item.get("symbol", "")

            if q in name or q in symbol or q in ts_code:
                internal_ticker = self.convert_to_internal_ticker(ts_code)

                results.append(
                    AssetSearchResult(
                        ticker=internal_ticker,
                        name=name,
                        asset_type=AssetType.STOCK,
                        exchange=(
                            Exchange.SSE
                            if ts_code.endswith(".SH")
                            else (
                                Exchange.SZSE
                                if ts_code.endswith(".SZ")
                                else Exchange.BSE
                            )
                        ),
                        currency="CNY",
                        source=self.source,
                    )
                )

            if len(results) >= limit:
                break

        return results

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Fetch financial statements and metrics from Tushare.

        Tushare provides comprehensive financial data including:
        - Income statement (利润表)
        - Balance sheet (资产负债表)
        - Cash flow statement (现金流量表)
        - Financial indicators (财务指标)

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Dictionary containing financial data
        """
        cache_key = f"tushare:financials:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        ts_code = self._to_ts_code(ticker)

        try:
            # Fetch financial data in parallel
            # 利润表 (Income Statement)
            income_task = self._run(
                client.income,
                ts_code=ts_code,
                fields="ts_code,end_date,revenue,operate_profit,total_profit,n_income,n_income_attr_p",
            )

            # 资产负债表 (Balance Sheet)
            balance_task = self._run(
                client.balancesheet,
                ts_code=ts_code,
                fields="ts_code,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int",
            )

            # 现金流量表 (Cash Flow Statement)
            cashflow_task = self._run(
                client.cashflow,
                ts_code=ts_code,
                fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act",
            )

            # 财务指标 (Financial Indicators)
            indicator_task = self._run(
                client.fina_indicator,
                ts_code=ts_code,
                fields="ts_code,end_date,eps,roe,roa,grossprofit_margin,debt_to_assets,current_ratio",
            )

            income_df, balance_df, cashflow_df, indicator_df = await asyncio.gather(
                income_task,
                balance_task,
                cashflow_task,
                indicator_task,
                return_exceptions=True,
            )

            # Helper function to convert DataFrame to serializable format
            def df_to_dict(df):
                if isinstance(df, Exception):
                    self.logger.warning(f"Failed to fetch financial data: {df}")
                    return None
                if df is None or df.empty:
                    return None
                # Convert DataFrame to list of dicts (JSON serializable)
                # Limit to latest 4 periods (quarters/years)
                return df.head(4).to_dict("records")

            result = {
                "income_statement": df_to_dict(income_df),
                "balance_sheet": df_to_dict(balance_df),
                "cash_flow": df_to_dict(cashflow_df),
                "financial_indicators": df_to_dict(indicator_df),
                "source": "tushare",
                "ts_code": ts_code,
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")

    async def get_money_flow(self, ticker: str, days: int = 20) -> Dict[str, Any]:
        """获取个股资金流向数据

        Args:
            ticker: 股票代码 (内部格式 SSE:600519)
            days: 获取最近 N 天数据

        Returns:
            包含资金流向的结构化数据
        """
        ts_code = self._to_ts_code(ticker)
        cache_key = f"tushare:moneyflow:{ts_code}:{days}"

        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            df = await self._run(
                client.moneyflow,
                ts_code=ts_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if df is None or df.empty:
                return {
                    "error": f"No money flow data for {ticker}",
                    "symbol": ts_code,
                    "source": "tushare",
                }

            df = df.sort_values("trade_date").tail(days)

            # 格式化日期
            dates = (
                df["trade_date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}").tolist()
            )

            # 主力 = 超大单 + 大单 (金额)
            main_buy = df["buy_elg_amount"].fillna(0) + df["buy_lg_amount"].fillna(0)
            main_sell = df["sell_elg_amount"].fillna(0) + df["sell_lg_amount"].fillna(0)
            main_net = (main_buy - main_sell).tolist()

            # 散户 = 中单 + 小单
            retail_buy = df["buy_md_amount"].fillna(0) + df["buy_sm_amount"].fillna(0)
            retail_sell = df["sell_md_amount"].fillna(0) + df["sell_sm_amount"].fillna(
                0
            )
            retail_net = (retail_buy - retail_sell).tolist()

            # 总净流入
            total_net = [m + r for m, r in zip(main_net, retail_net)]

            # 计算汇总和趋势
            total_main = sum(main_net)
            total_retail = sum(retail_net)
            recent_main = sum(main_net[-5:]) if len(main_net) >= 5 else total_main

            if recent_main > 0 and total_main > 0:
                trend = "主力持续流入"
            elif recent_main < 0 and total_main < 0:
                trend = "主力持续流出"
            elif recent_main > 0:
                trend = "主力近期流入"
            else:
                trend = "主力近期流出"

            result = {
                "symbol": ts_code,
                "ticker": ticker,
                "component_type": "money_flow",
                "source": "tushare",
                "data": {
                    "dates": dates,
                    "main_net_inflow": [round(x, 2) for x in main_net],
                    "retail_net_inflow": [round(x, 2) for x in retail_net],
                    "total_net_inflow": [round(x, 2) for x in total_net],
                },
                "summary": {
                    "total_main_net": round(total_main, 2),
                    "total_retail_net": round(total_retail, 2),
                    "trend": trend,
                    "period_days": len(dates),
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get money flow for {ticker}: {e}")
            raise ValueError(f"Failed to get money flow: {e}")

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        """获取北向资金(沪深港通)流向数据

        Returns:
            包含北向资金数据的结构化数据
        """
        cache_key = f"tushare:hsgt:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            df = await self._run(
                client.moneyflow_hsgt,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if df is None or df.empty:
                return {"error": "No north bound flow data", "source": "tushare"}

            df = df.sort_values("trade_date").tail(days)

            dates = (
                df["trade_date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}").tolist()
            )

            # 北向资金 = 沪股通 + 深股通
            # Ensure numeric types
            df["hgt"] = pd.to_numeric(df["hgt"], errors="coerce")
            df["sgt"] = pd.to_numeric(df["sgt"], errors="coerce")
            df["north_money"] = pd.to_numeric(df["north_money"], errors="coerce")

            hgt = df["hgt"].fillna(0).tolist()  # 沪股通
            sgt = df["sgt"].fillna(0).tolist()  # 深股通
            north_total = df["north_money"].fillna(0).tolist()

            result = {
                "component_type": "north_bound_flow",
                "source": "tushare",
                "data": {
                    "dates": dates,
                    "hk_to_sh": hgt,
                    "hk_to_sz": sgt,
                    "total": north_total,
                },
                "summary": {
                    "total_net": round(sum(north_total), 2),
                    "period_days": len(dates),
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get north bound flow: {e}")
            raise ValueError(f"Failed to get north bound flow: {e}")

    async def get_chip_distribution(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """获取筹码分布/成本分布数据 (Chip Distribution)

        Tushare cyq_perf 接口提供筹码分布相关指标。

        Args:
            ticker: 股票代码 (内部格式)
            days: 获取最近 N 天数据

        Returns:
            筹码分布数据
        """
        ts_code = self._to_ts_code(ticker)
        cache_key = f"tushare:cyq:{ts_code}:{days}"

        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            # cyq_perf - 每日筹码及盈亏
            df = await self._run(
                client.cyq_perf,
                ts_code=ts_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if df is None or df.empty:
                return {
                    "error": f"No chip data for {ticker}",
                    "symbol": ts_code,
                    "source": "tushare",
                }

            df = df.sort_values("trade_date").tail(days)

            # 最新一天的筹码数据
            latest = df.iloc[-1]

            result = {
                "symbol": ts_code,
                "ticker": ticker,
                "component_type": "chip_distribution",
                "source": "tushare",
                "data": {
                    "dates": df["trade_date"]
                    .apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}")
                    .tolist(),
                    # 获利比例历史
                    "profit_ratio": df["his_low"].fillna(0).tolist(),
                    # 成本集中度 (90% 成本区间)
                    "cost_5pct": df["cost_5pct"].fillna(0).tolist(),
                    "cost_15pct": df["cost_15pct"].fillna(0).tolist(),
                    "cost_50pct": df["cost_50pct"].fillna(0).tolist(),
                    "cost_85pct": df["cost_85pct"].fillna(0).tolist(),
                    "cost_95pct": df["cost_95pct"].fillna(0).tolist(),
                },
                "summary": {
                    "current_profit_ratio": float(latest.get("his_low", 0)),
                    "cost_concentration": (
                        float(latest.get("cost_85pct", 0) - latest.get("cost_15pct", 0))
                        if latest.get("cost_85pct") and latest.get("cost_15pct")
                        else 0
                    ),
                    "period_days": len(df),
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get chip data for {ticker}: {e}")
            raise ValueError(f"Failed to get chip data: {e}")

    async def get_macro_data(self, indicators: List[str] = None) -> Dict[str, Any]:
        """获取宏观经济数据

        Args:
            indicators: 指标列表 ["CPI", "PPI", "M2", "GDP"]

        Returns:
            宏观数据
        """
        if indicators is None:
            indicators = ["CPI", "PPI", "M2"]

        cache_key = f"tushare:macro:{'-'.join(sorted(indicators))}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        result = {
            "component_type": "macro_indicators",
            "source": "tushare",
            "data": {},
            "timestamp": datetime.now().isoformat(),
        }

        try:
            # CPI 数据
            if "CPI" in indicators:
                try:
                    cpi_df = await self._run(
                        client.cn_cpi,
                        start_m="202301",
                        end_m=datetime.now().strftime("%Y%m"),
                    )
                    if cpi_df is not None and not cpi_df.empty:
                        cpi_df = cpi_df.sort_values("month").tail(12)
                        result["data"]["CPI"] = {
                            "months": cpi_df["month"].tolist(),
                            "values": cpi_df["nt_yoy"].fillna(0).tolist(),
                            "latest": float(cpi_df["nt_yoy"].iloc[-1]),
                        }
                except Exception as e:
                    self.logger.warning(f"Failed to get CPI: {e}")

            # PPI 数据
            if "PPI" in indicators:
                try:
                    ppi_df = await self._run(
                        client.cn_ppi,
                        start_m="202301",
                        end_m=datetime.now().strftime("%Y%m"),
                    )
                    if ppi_df is not None and not ppi_df.empty:
                        ppi_df = ppi_df.sort_values("month").tail(12)
                        result["data"]["PPI"] = {
                            "months": ppi_df["month"].tolist(),
                            "values": ppi_df["ppi_yoy"].fillna(0).tolist(),
                            "latest": float(ppi_df["ppi_yoy"].iloc[-1]),
                        }
                except Exception as e:
                    self.logger.warning(f"Failed to get PPI: {e}")

            # M2 货币供应量
            if "M2" in indicators:
                try:
                    m2_df = await self._run(
                        client.cn_m,
                        start_m="202301",
                        end_m=datetime.now().strftime("%Y%m"),
                    )
                    if m2_df is not None and not m2_df.empty:
                        m2_df = m2_df.sort_values("month").tail(12)
                        result["data"]["M2"] = {
                            "months": m2_df["month"].tolist(),
                            "values": m2_df["m2_yoy"].fillna(0).tolist(),
                            "latest": float(m2_df["m2_yoy"].iloc[-1]),
                        }
                except Exception as e:
                    self.logger.warning(f"Failed to get M2: {e}")

            # GDP 数据 (季度)
            if "GDP" in indicators:
                try:
                    gdp_df = await self._run(
                        client.cn_gdp,
                        start_q="2023Q1",
                        end_q=f"{datetime.now().year}Q4",
                    )
                    if gdp_df is not None and not gdp_df.empty:
                        gdp_df = gdp_df.sort_values("quarter").tail(8)
                        result["data"]["GDP"] = {
                            "quarters": gdp_df["quarter"].tolist(),
                            "values": gdp_df["gdp_yoy"].fillna(0).tolist(),
                            "latest": float(gdp_df["gdp_yoy"].iloc[-1]),
                        }
                except Exception as e:
                    self.logger.warning(f"Failed to get GDP: {e}")

            await self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get macro data: {e}")
            raise ValueError(f"Failed to get macro data: {e}")
