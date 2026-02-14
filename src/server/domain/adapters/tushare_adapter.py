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
                change=Decimal(str(row["change"])) if "change" in row else None,
                change_percent=(
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
                        change=(
                            Decimal(str(row["change"])) if "change" in row else None
                        ),
                        change_percent=(
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
        # v3: increase history depth to support multi-year charts
        cache_key = f"tushare:financials:{ticker}:v3"
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

            # 每日指标 (Daily Basic - PE/PB/MarketCap)
            # Fetch latest available
            daily_basic_df = await self._run(
                client.daily_basic,
                ts_code=ts_code,
                fields="ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv,circ_mv",
                limit=1
            )

            # Helper function to convert DataFrame to serializable format
            def df_to_dict(df, max_rows: int | None = None):
                if isinstance(df, Exception):
                    self.logger.warning(f"Failed to fetch financial data: {df}")
                    return None
                if df is None or df.empty:
                    return None
                # Convert DataFrame to list of dicts (JSON serializable)
                if max_rows is not None:
                    df = df.head(max_rows)
                return df.to_dict("records")

            # Tushare returns most-recent first; keep enough quarters for ~10 years
            max_periods = 40

            result = {
                "income_statement": df_to_dict(income_df, max_periods),
                "balance_sheet": df_to_dict(balance_df, max_periods),
                "cash_flow": df_to_dict(cashflow_df, max_periods),
                "financial_indicators": df_to_dict(indicator_df, max_periods),
                "market_metrics": df_to_dict(daily_basic_df),
                "source": "tushare",
                "ts_code": ts_code,
            }

            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to fetch financials for {ticker}: {e}")
            raise ValueError(f"Failed to fetch financials for {ticker}: {e}")

    async def get_dividend_info(self, ticker: str) -> Dict[str, Any]:
        """Fetch dividend history from Tushare.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Dictionary containing dividend history rows
        """
        cache_key = f"tushare:dividend:{ticker}:v1"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        ts_code = self._to_ts_code(ticker)

        try:
            df = await self._run(
                client.dividend,
                ts_code=ts_code,
                fields=(
                    "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,"
                    "stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,"
                    "pay_date,div_listdate,imp_ann_date,base_share"
                ),
            )

            if df is None or df.empty:
                result = {"ts_code": ts_code, "rows": []}
            else:
                df = df.where(df.notnull(), None)
                result = {"ts_code": ts_code, "rows": df.to_dict("records")}

            try:
                await self.cache.set(cache_key, result, ttl=86400)
            except Exception as cache_error:
                self.logger.warning(f"Failed to cache dividend data: {cache_error}")

            return result
        except Exception as e:
            self.logger.error(f"Failed to fetch dividend info: {e}")
            raise

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

    async def get_money_supply(self) -> Dict[str, Any]:
        """获取货币供应量数据 (M1/M2)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        end_m = datetime.now().strftime("%Y%m")
        start_m = f"{datetime.now().year - 5}01"
        cache_key = f"tushare:money_supply:{start_m}-{end_m}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_m, start_m=start_m, end_m=end_m)
            if df is None or df.empty:
                return {"data": [], "component_type": "money_supply", "source": "tushare"}

            df = df.sort_values("month")
            df = df.where(df.notnull(), None)
            data = df.to_dict("records")
            result = {
                "component_type": "money_supply",
                "source": "tushare",
                "data": data,
                "summary": {},
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get money supply: {e}")
            raise ValueError(f"Failed to get money supply: {e}")

    async def get_inflation_data(self) -> Dict[str, Any]:
        """获取通胀数据 (CPI/PPI)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        end_m = datetime.now().strftime("%Y%m")
        start_m = f"{datetime.now().year - 5}01"
        cache_key = f"tushare:inflation:{start_m}-{end_m}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            cpi_df = await self._run(client.cn_cpi, start_m=start_m, end_m=end_m)
            ppi_df = await self._run(client.cn_ppi, start_m=start_m, end_m=end_m)

            result = {
                "component_type": "inflation_data",
                "source": "tushare",
                "data": {
                    "CPI": cpi_df.where(cpi_df.notnull(), None).to_dict("records") if cpi_df is not None else [],
                    "PPI": ppi_df.where(ppi_df.notnull(), None).to_dict("records") if ppi_df is not None else [],
                },
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get inflation data: {e}")
            raise ValueError(f"Failed to get inflation data: {e}")

    async def get_pmi_data(self) -> Dict[str, Any]:
        """获取 PMI 数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        end_m = datetime.now().strftime("%Y%m")
        start_m = f"{datetime.now().year - 5}01"
        cache_key = f"tushare:pmi:{start_m}-{end_m}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_pmi, start_m=start_m, end_m=end_m)
            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")
            result = {
                "component_type": "pmi_data",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get PMI data: {e}")
            raise ValueError(f"Failed to get PMI data: {e}")

    async def get_gdp_data(self) -> Dict[str, Any]:
        """获取 GDP 数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        start_q = f"{datetime.now().year - 5}Q1"
        end_q = f"{datetime.now().year}Q4"
        cache_key = f"tushare:gdp:{start_q}-{end_q}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_gdp, start_q=start_q, end_q=end_q)
            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")
            result = {
                "component_type": "gdp_data",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get GDP data: {e}")
            raise ValueError(f"Failed to get GDP data: {e}")

    async def get_social_financing(self) -> Dict[str, Any]:
        """获取社会融资数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        end_m = datetime.now().strftime("%Y%m")
        start_m = f"{datetime.now().year - 5}01"
        cache_key = f"tushare:social_financing:{start_m}-{end_m}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            # Tushare 社融（月度）接口为 sf_month
            df = await self._run(client.sf_month, start_m=start_m, end_m=end_m)
            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")
            result = {
                "component_type": "social_financing",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get social financing: {e}", exc_info=True)
            raise ValueError(f"Failed to get social financing: {e}")

    async def get_interest_rates(self) -> Dict[str, Any]:
        """获取利率数据 (SHIBOR + LPR)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        end_m = datetime.now().strftime("%Y%m")
        start_m = f"{datetime.now().year - 5}01"
        cache_key = f"tushare:interest_rates:{start_m}-{end_m}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        errors: List[str] = []
        shibor_df = None
        lpr_df = None

        try:
            shibor_df = await self._run(
                client.shibor,
                start_date=(datetime.now() - timedelta(days=370)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
        except Exception as e:
            errors.append(f"shibor: {e}")
            self.logger.error(f"Failed to get SHIBOR: {e}")

        # LPR 官方接口为 shibor_lpr，使用 start_date/end_date 参数
        try:
            lpr_df = await self._run(
                client.shibor_lpr,
                start_date=(datetime.now() - timedelta(days=5 * 365)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
        except Exception as e:
            errors.append(f"shibor_lpr: {e}")
            self.logger.error(f"Failed to get LPR (shibor_lpr): {e}")

        if shibor_df is None and lpr_df is None:
            raise ValueError(f"Failed to get interest rates: {', '.join(errors) or 'unknown error'}")

        result = {
            "component_type": "interest_rates",
            "source": "tushare",
            "data": {
                "shibor": shibor_df.where(shibor_df.notnull(), None).to_dict("records") if shibor_df is not None else [],
                "lpr": lpr_df.where(lpr_df.notnull(), None).to_dict("records") if lpr_df is not None else [],
            },
        }
        if errors:
            result["errors"] = errors

        await self.cache.set(cache_key, result, ttl=3600)
        return result

    async def get_market_liquidity(self, days: int = 60) -> Dict[str, Any]:
        """获取市场流动性数据 (北向资金 + 融资融券)."""
        cache_key = f"tushare:market_liquidity:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            north_df = await self._run(
                client.moneyflow_hsgt,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
            margin_df = await self._run(
                client.margin_detail,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            north_flow = []
            if north_df is not None and not north_df.empty:
                north_df = north_df.sort_values("trade_date").tail(days)
                north_flow = north_df.where(north_df.notnull(), None).to_dict("records")

            margin = []
            if margin_df is not None and not margin_df.empty:
                margin_df = margin_df.sort_values("trade_date").tail(days)
                margin = margin_df.where(margin_df.notnull(), None).to_dict("records")

            result = {
                "component_type": "market_liquidity",
                "source": "tushare",
                "data": {
                    "north_flow": north_flow,
                    "margin": margin,
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get market liquidity: {e}")
            raise ValueError(f"Failed to get market liquidity: {e}")

    async def get_market_money_flow(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """获取市场资金流向数据."""
        cache_key = f"tushare:market_money_flow:{trade_date or 'latest'}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            if trade_date:
                df = await self._run(client.moneyflow_mkt, trade_date=trade_date)
            else:
                df = await self._run(
                    client.moneyflow_mkt,
                    trade_date=datetime.now().strftime("%Y%m%d"),
                )
                if df is None or df.empty:
                    df = await self._run(
                        client.moneyflow_mkt,
                        trade_date=(datetime.now() - timedelta(days=1)).strftime("%Y%m%d"),
                    )

            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")

            result = {
                "component_type": "market_money_flow",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get market money flow: {e}")
            raise ValueError(f"Failed to get market money flow: {e}")

    async def get_sector_trend(self, sector_name: str, days: int = 10) -> Dict[str, Any]:
        """获取板块走势数据."""
        cache_key = f"tushare:sector_trend:{sector_name}:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            index_df = await self._run(client.ths_index, name=sector_name)
            if index_df is None or index_df.empty:
                raise ValueError(f"No sector index found for {sector_name}")

            index_code = index_df.iloc[0].get("ts_code")
            if not index_code:
                raise ValueError(f"No ts_code for sector {sector_name}")

            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            daily_df = await self._run(
                client.ths_daily,
                ts_code=index_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if daily_df is None or daily_df.empty:
                raise ValueError(f"No sector daily data for {sector_name}")

            daily_df = daily_df.sort_values("trade_date").tail(days)
            daily_df = daily_df.where(daily_df.notnull(), None)
            trend = daily_df.to_dict("records")
            total_pct_chg = float(daily_df["pct_chg"].fillna(0).sum()) if "pct_chg" in daily_df else 0.0

            result = {
                "component_type": "sector_trend",
                "source": "tushare",
                "sector_name": sector_name,
                "days": days,
                "total_pct_chg": total_pct_chg,
                "trend": trend,
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get sector trend: {e}")
            raise ValueError(f"Failed to get sector trend: {e}")

    async def get_ggt_daily(self, days: int = 60) -> Dict[str, Any]:
        """获取港股通每日成交统计."""
        cache_key = f"tushare:ggt_daily:{days}"
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
                client.ggt_daily,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            rows = []
            if df is not None and not df.empty:
                df = df.sort_values("trade_date").tail(days)
                rows = df.where(df.notnull(), None).to_dict("records")

            result = {
                "component_type": "ggt_daily",
                "source": "tushare",
                "data": rows,
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get ggt daily: {e}")
            raise ValueError(f"Failed to get ggt daily: {e}")

    async def get_mainbz_info(self, ticker: str) -> Dict[str, Any]:
        """获取主营业务构成."""
        cache_key = f"tushare:mainbz:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        ts_code = self._to_ts_code(ticker)
        try:
            df = await self._run(client.fina_mainbz, ts_code=ts_code)
            rows = []
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False)
                rows = df.where(df.notnull(), None).to_dict("records")
            result = {
                "component_type": "mainbz_info",
                "source": "tushare",
                "ts_code": ts_code,
                "rows": rows,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get main business info: {e}")
            raise ValueError(f"Failed to get main business info: {e}")

    async def get_shareholder_info(self, ticker: str) -> Dict[str, Any]:
        """获取股东信息."""
        cache_key = f"tushare:shareholder:{ticker}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        ts_code = self._to_ts_code(ticker)
        try:
            holders_df = await self._run(client.top10_holders, ts_code=ts_code)
            float_df = await self._run(client.top10_floatholders, ts_code=ts_code)
            number_df = await self._run(client.stk_holdernumber, ts_code=ts_code)
            trade_df = await self._run(client.stk_holdertrade, ts_code=ts_code)

            result = {
                "component_type": "shareholder_info",
                "source": "tushare",
                "ts_code": ts_code,
                "data": {
                    "top10_holders": holders_df.where(holders_df.notnull(), None).to_dict("records") if holders_df is not None else [],
                    "top10_floatholders": float_df.where(float_df.notnull(), None).to_dict("records") if float_df is not None else [],
                    "holder_number": number_df.where(number_df.notnull(), None).to_dict("records") if number_df is not None else [],
                    "holder_trade": trade_df.where(trade_df.notnull(), None).to_dict("records") if trade_df is not None else [],
                },
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get shareholder info: {e}")
            raise ValueError(f"Failed to get shareholder info: {e}")

    async def get_technical_indicators(
        self,
        ticker: str,
        indicators: List[str],
        period: str = "daily",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Calculate technical indicators.

        Args:
            ticker: Asset ticker
            indicators: List of indicators ["MA", "MACD", "KDJ", "RSI", "VOL"]
            period: Data period (currently only supports "daily")
            start_date: Start date
            end_date: End date

        Returns:
            Dictionary containing calculated indicators
        """
        if not indicators:
            indicators = ["MA", "MACD", "KDJ", "RSI", "VOL"]

        # Default to 1 year of data if not specified, to ensure enough data for indicators
        if not end_date:
            end_date = datetime.now()
        if not start_date:
            start_date = end_date - timedelta(days=365)

        # Fetch historical prices
        prices = await self.get_historical_prices(
            ticker, start_date, end_date, interval="1d"
        )

        if not prices:
            return {"error": f"No historical data for {ticker}", "source": "tushare"}

        # Convert to DataFrame
        data = [p.to_dict() for p in prices]
        df = pd.DataFrame(data)
        
        # Rename columns to match technical analysis expectations
        df = df.rename(columns={
            "close_price": "close",
            "open_price": "open",
            "high_price": "high",
            "low_price": "low"
        })
        
        # Ensure numeric types
        for col in ["close", "high", "low", "open", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
        df = df.sort_values("timestamp")

        # Ensure timestamp is datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        
        result = {
            "dates": df["timestamp"].apply(lambda x: x.strftime("%Y-%m-%d")).tolist(),
            "close": df["close"].tolist(),
            "indicators": {},
            "source": "tushare",
            "ticker": ticker,
            "ts_code": self._to_ts_code(ticker),
        }

        try:
            def _series_to_list(series: pd.Series) -> List[Optional[float]]:
                return series.where(pd.notnull(series), None).tolist()

            # MA (Moving Average)
            if "MA" in indicators:
                ma_data = {}
                for window in [5, 10, 20, 30, 60]:
                    ma_data[f"ma{window}"] = _series_to_list(
                        df["close"].rolling(window=window).mean()
                    )
                result["indicators"]["ma"] = ma_data

            # MACD
            if "MACD" in indicators:
                exp12 = df["close"].ewm(span=12, adjust=False).mean()
                exp26 = df["close"].ewm(span=26, adjust=False).mean()
                macd = exp12 - exp26
                signal = macd.ewm(span=9, adjust=False).mean()
                hist = (macd - signal) * 2
                
                result["indicators"]["macd"] = {
                    "diff": _series_to_list(macd),
                    "dea": _series_to_list(signal),
                    "hist": _series_to_list(hist)
                }

            # KDJ
            if "KDJ" in indicators:
                low_min = df["low"].rolling(window=9).min()
                high_max = df["high"].rolling(window=9).max()
                rsv = (df["close"] - low_min) / (high_max - low_min) * 100
                
                # Use simple moving average for K and D as per common Chinese stock software
                # K = 2/3 * PrevK + 1/3 * RSV
                # D = 2/3 * PrevD + 1/3 * K
                # J = 3 * K - 2 * D
                
                k_list = []
                d_list = []
                j_list = []
                
                k = 50
                d = 50
                
                for r in rsv.fillna(50):
                    k = (2/3) * k + (1/3) * r
                    d = (2/3) * d + (1/3) * k
                    j = 3 * k - 2 * d
                    k_list.append(k)
                    d_list.append(d)
                    j_list.append(j)
                    
                result["indicators"]["kdj"] = {
                    "k": k_list,
                    "d": d_list,
                    "j": j_list
                }

            # RSI
            if "RSI" in indicators:
                delta = df["close"].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                result["indicators"]["rsi"] = _series_to_list(rsi)

            # VOL (Volume MA)
            if "VOL" in indicators:
                vol_data = {"volume": df["volume"].tolist()}
                for window in [5, 10, 20]:
                    vol_data[f"ma{window}"] = _series_to_list(
                        df["volume"].rolling(window=window).mean()
                    )
                result["indicators"]["vol"] = vol_data

            # Build per-day rows (align with common front-end expectations)
            rows = []
            for idx, ts in enumerate(df["timestamp"]):
                row = {
                    "trade_date": ts.strftime("%Y%m%d"),
                    "close": df["close"].iloc[idx],
                }
                ma = result["indicators"].get("ma", {})
                macd_ind = result["indicators"].get("macd", {})
                kdj = result["indicators"].get("kdj", {})
                rsi_list = result["indicators"].get("rsi", [])

                row.update({
                    "MA5": ma.get("ma5", [None] * len(df)).__getitem__(idx),
                    "MA10": ma.get("ma10", [None] * len(df)).__getitem__(idx),
                    "MA20": ma.get("ma20", [None] * len(df)).__getitem__(idx),
                    "MA60": ma.get("ma60", [None] * len(df)).__getitem__(idx),
                    "MACD": macd_ind.get("diff", [None] * len(df)).__getitem__(idx),
                    "MACD_signal": macd_ind.get("dea", [None] * len(df)).__getitem__(idx),
                    "RSI": rsi_list[idx] if idx < len(rsi_list) else None,
                    "K": kdj.get("k", [None] * len(df)).__getitem__(idx),
                    "D": kdj.get("d", [None] * len(df)).__getitem__(idx),
                    "J": kdj.get("j", [None] * len(df)).__getitem__(idx),
                })
                rows.append(row)

            result["rows"] = rows
            result["current_price"] = float(df["close"].iloc[-1])

            return result

        except Exception as e:
            self.logger.error(f"Failed to calculate indicators for {ticker}: {e}")
            raise ValueError(f"Failed to calculate indicators: {e}")
