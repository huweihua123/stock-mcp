# src/server/domain/adapters/tushare_adapter.py
"""TushareAdapter provides price & historical data via Tushare API.

All calls are wrapped with asyncio.run_in_executor to keep
the event loop non‑blocking.
"""

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.sector_matching import (
    pick_sector_resolution,
    rank_sector_candidates,
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


class TushareAdapter(BaseDataAdapter):
    name = "tushare"

    # L1 进程内存缓存：{ts_code: str -> name: str}，带 TTL
    _sector_index_cache: Dict[str, Any] = {}  # {"data": [...], "ts": float}

    def __init__(self, tushare_conn, cache):
        super().__init__(DataSource.TUSHARE)
        self.tushare_conn = tushare_conn
        self.cache = cache
        self.logger = logger
        # SQLite L3 缓存文件路径（与 security_master.sqlite 同目录）
        self._sqlite_path: str = str(
            Path(__file__).resolve().parents[4] / "data" / "security_master.sqlite"
        )

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

    @staticmethod
    def _is_index_ts_code(ts_code: str) -> bool:
        """Best-effort detection for CN index ts_code."""
        if "." not in ts_code:
            return False
        symbol, suffix = ts_code.split(".", 1)
        suffix = suffix.upper()
        if not symbol.isdigit() or len(symbol) != 6:
            return False
        if suffix == "SH":
            return symbol.startswith("000") or symbol.startswith(
                ("880", "881", "882", "883")
            )
        if suffix == "SZ":
            return symbol.startswith("399")
        return False

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
            fetch_api = client.index_daily if self._is_index_ts_code(ts_code) else client.daily
            df = await self._run(
                fetch_api,
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
                limit=1,
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
        # Keep only the most recent dividend records to control payload size.
        max_rows = 10
        cache_key = f"tushare:dividend:{ticker}:v2:l{max_rows}"
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
                if "end_date" in df.columns:
                    sort_key = pd.to_datetime(df["end_date"], errors="coerce")
                    df = (
                        df.assign(_end_date_sort_key=sort_key)
                        .sort_values("_end_date_sort_key", ascending=False)
                        .drop(columns=["_end_date_sort_key"])
                    )
                df = df.head(max_rows)
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

    async def get_forecast_info(self, ticker: str, limit: int = 50) -> Dict[str, Any]:
        """Fetch performance forecast data from Tushare.

        Args:
            ticker: Asset ticker in internal format
            limit: Maximum number of records to return

        Returns:
            Dictionary containing forecast rows
        """
        cache_key = f"tushare:forecast:{ticker}:l{limit}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        ts_code = self._to_ts_code(ticker)

        def _fmt_date(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip().replace("-", "").replace("/", "")
            if len(text) == 8 and text.isdigit():
                return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
            return str(value)

        try:
            df = await self._run(
                client.forecast,
                ts_code=ts_code,
                fields=(
                    "ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
                    "net_profit_min,net_profit_max,last_parent_net,first_ann_date,"
                    "summary,change_reason,update_flag"
                ),
                limit=limit,
            )

            if df is None or df.empty:
                result = {
                    "variant": "forecast_info",
                    "source": "tushare",
                    "ts_code": ts_code,
                    "rows": [],
                }
            else:
                df = df.where(df.notnull(), None)
                rows = df.to_dict("records")

                for row in rows:
                    row["ann_date"] = _fmt_date(row.get("ann_date"))
                    row["end_date"] = _fmt_date(row.get("end_date"))
                    row["first_ann_date"] = _fmt_date(row.get("first_ann_date"))

                rows.sort(
                    key=lambda r: (
                        str(r.get("ann_date") or ""),
                        str(r.get("end_date") or ""),
                    ),
                    reverse=True,
                )

                dedup_rows = []
                seen = set()
                for row in rows:
                    key = (
                        row.get("ts_code"),
                        row.get("ann_date"),
                        row.get("end_date"),
                        row.get("summary"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    dedup_rows.append(row)

                if limit and limit > 0:
                    dedup_rows = dedup_rows[:limit]

                result = {
                    "variant": "forecast_info",
                    "source": "tushare",
                    "ts_code": ts_code,
                    "rows": dedup_rows,
                }

            try:
                await self.cache.set(cache_key, result, ttl=43200)
            except Exception as cache_error:
                self.logger.warning(f"Failed to cache forecast data: {cache_error}")

            return result
        except Exception as e:
            self.logger.error(f"Failed to fetch forecast info: {e}")
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
                self.logger.warning(
                    f"moneyflow returned empty for {ticker} "
                    f"(积分不足或该期无数据)，返回空 records"
                )
                result = {
                    "symbol": ts_code,
                    "ticker": ticker,
                    "variant": "money_flow",
                    "source": "tushare",
                    "amount_unit": "10k_cny",
                    "records": [],
                    "data": {
                        "dates": [],
                        "main_net_inflow": [],
                        "retail_net_inflow": [],
                        "total_net_inflow": [],
                    },
                    "summary": {
                        "total_main_net": 0,
                        "total_retail_net": 0,
                        "trend": "暂无数据（接口积分不足或该时段无数据）",
                        "period_days": 0,
                        "amount_unit": "10k_cny",
                    },
                }
                await self.cache.set(cache_key, result, ttl=300)
                return result

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
                "variant": "money_flow",
                "source": "tushare",
                "amount_unit": "10k_cny",
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
                    "amount_unit": "10k_cny",
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
                "variant": "north_bound_flow",
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
                "variant": "chip_distribution",
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

    async def get_money_supply(self, months: int = 60) -> Dict[str, Any]:
        """获取货币供应量数据 (M1/M2)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if months <= 0:
            raise ValueError("months must be > 0")

        end_dt = datetime.now()
        end_m = end_dt.strftime("%Y%m")
        start_m = (end_dt - timedelta(days=months * 31)).strftime("%Y%m")
        cache_key = f"tushare:money_supply:{start_m}-{end_m}:m{months}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_m, start_m=start_m, end_m=end_m)
            if df is None or df.empty:
                return {
                    "data": [],
                    "variant": "money_supply",
                    "source": "tushare",
                }

            df = df.sort_values("month")
            df = df.where(df.notnull(), None)
            data = df.to_dict("records")
            result = {
                "variant": "money_supply",
                "source": "tushare",
                "data": data,
                "summary": {},
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get money supply: {e}")
            raise ValueError(f"Failed to get money supply: {e}")

    async def get_inflation_data(self, months: int = 60) -> Dict[str, Any]:
        """获取通胀数据 (CPI/PPI)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if months <= 0:
            raise ValueError("months must be > 0")

        end_dt = datetime.now()
        end_m = end_dt.strftime("%Y%m")
        start_m = (end_dt - timedelta(days=months * 31)).strftime("%Y%m")
        cache_key = f"tushare:inflation:{start_m}-{end_m}:m{months}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            cpi_df = await self._run(client.cn_cpi, start_m=start_m, end_m=end_m)
            ppi_df = await self._run(client.cn_ppi, start_m=start_m, end_m=end_m)

            result = {
                "variant": "inflation_data",
                "source": "tushare",
                "data": {
                    "CPI": (
                        cpi_df.where(cpi_df.notnull(), None).to_dict("records")
                        if cpi_df is not None
                        else []
                    ),
                    "PPI": (
                        ppi_df.where(ppi_df.notnull(), None).to_dict("records")
                        if ppi_df is not None
                        else []
                    ),
                },
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get inflation data: {e}")
            raise ValueError(f"Failed to get inflation data: {e}")

    async def get_pmi_data(self, months: int = 60) -> Dict[str, Any]:
        """获取 PMI 数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if months <= 0:
            raise ValueError("months must be > 0")

        end_dt = datetime.now()
        end_m = end_dt.strftime("%Y%m")
        start_m = (end_dt - timedelta(days=months * 31)).strftime("%Y%m")
        cache_key = f"tushare:pmi:{start_m}-{end_m}:m{months}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_pmi, start_m=start_m, end_m=end_m)
            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")
            result = {
                "variant": "pmi_data",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get PMI data: {e}")
            raise ValueError(f"Failed to get PMI data: {e}")

    async def get_gdp_data(self, quarters: int = 20) -> Dict[str, Any]:
        """获取 GDP 数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if quarters <= 0:
            raise ValueError("quarters must be > 0")

        now = datetime.now()
        current_q = (now.month - 1) // 3 + 1
        start_index = (now.year * 4 + current_q) - (quarters - 1)
        start_year = (start_index - 1) // 4
        start_quarter = (start_index - 1) % 4 + 1
        start_q = f"{start_year}Q{start_quarter}"
        end_q = f"{now.year}Q{current_q}"
        cache_key = f"tushare:gdp:{start_q}-{end_q}:q{quarters}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(client.cn_gdp, start_q=start_q, end_q=end_q)
            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")
            result = {
                "variant": "gdp_data",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get GDP data: {e}")
            raise ValueError(f"Failed to get GDP data: {e}")

    async def get_social_financing(self, months: int = 60) -> Dict[str, Any]:
        """获取社会融资数据."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if months <= 0:
            raise ValueError("months must be > 0")

        end_dt = datetime.now()
        end_m = end_dt.strftime("%Y%m")
        start_m = (end_dt - timedelta(days=months * 31)).strftime("%Y%m")
        cache_key = f"tushare:social_financing:{start_m}-{end_m}:m{months}"
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
                "variant": "social_financing",
                "source": "tushare",
                "data": data,
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get social financing: {e}", exc_info=True)
            raise ValueError(f"Failed to get social financing: {e}")

    async def get_interest_rates(
        self, shibor_days: int = 252, lpr_months: int = 60
    ) -> Dict[str, Any]:
        """获取利率数据 (SHIBOR + LPR)."""
        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")
        if shibor_days <= 0:
            raise ValueError("shibor_days must be > 0")
        if lpr_months <= 0:
            raise ValueError("lpr_months must be > 0")

        cache_key = f"tushare:interest_rates:s{shibor_days}:l{lpr_months}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        errors: List[str] = []
        shibor_df = None
        lpr_df = None

        try:
            shibor_df = await self._run(
                client.shibor,
                start_date=(datetime.now() - timedelta(days=shibor_days + 30)).strftime(
                    "%Y%m%d"
                ),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
        except Exception as e:
            errors.append(f"shibor: {e}")
            self.logger.error(f"Failed to get SHIBOR: {e}")

        # LPR 官方接口为 shibor_lpr，使用 start_date/end_date 参数
        try:
            lpr_df = await self._run(
                client.shibor_lpr,
                start_date=(
                    datetime.now() - timedelta(days=lpr_months * 31 + 31)
                ).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
        except Exception as e:
            errors.append(f"shibor_lpr: {e}")
            self.logger.error(f"Failed to get LPR (shibor_lpr): {e}")

        if shibor_df is None and lpr_df is None:
            raise ValueError(
                f"Failed to get interest rates: {', '.join(errors) or 'unknown error'}"
            )

        result = {
            "variant": "interest_rates",
            "source": "tushare",
            "data": {
                "shibor": (
                    shibor_df.where(shibor_df.notnull(), None).to_dict("records")
                    if shibor_df is not None
                    else []
                ),
                "lpr": (
                    lpr_df.where(lpr_df.notnull(), None).to_dict("records")
                    if lpr_df is not None
                    else []
                ),
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
            # margin: 市场融资融券汇总时间序列（按日聚合）
            # margin_detail 是个股截面，x轴全为同一日期，此处不适用
            margin_df = await self._run(
                client.margin,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            north_flow = []
            if north_df is not None and not north_df.empty:
                north_df = north_df.sort_values("trade_date").tail(days)
                north_flow = north_df.where(north_df.notnull(), None).to_dict("records")

            margin = []
            if margin_df is not None and not margin_df.empty:
                # margin 接口按交易所分行（SSE/SZSE），需按日期聚合求和
                agg_cols = {
                    c: "sum"
                    for c in ["rzye", "rqye", "rzrqye", "rzmre", "rqmcl", "rzrqjyzl"]
                    if c in margin_df.columns
                }
                if agg_cols:
                    margin_df = margin_df.groupby("trade_date", as_index=False).agg(
                        agg_cols
                    )
                margin_df = margin_df.sort_values("trade_date").tail(days)
                margin = margin_df.where(margin_df.notnull(), None).to_dict("records")

            result = {
                "variant": "market_liquidity",
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

    async def get_market_money_flow(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        include_outflow: bool = True,
    ) -> Dict[str, Any]:
        """获取市场资金流向数据."""
        safe_top_n = max(1, min(int(top_n), 100))
        cache_key = (
            "tushare:market_money_flow:"
            f"{trade_date or 'latest'}:{safe_top_n}:{int(bool(include_outflow))}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        async def _fetch_moneyflow_mkt(
            dt: str,
        ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
            """先尝试 moneyflow_mkt，失败则降级到 moneyflow_ind_dc。"""
            try:
                df = await self._run(client.moneyflow_mkt, trade_date=dt)
                if df is not None and not df.empty:
                    return df, "moneyflow_mkt"
            except Exception as e1:
                self.logger.warning(
                    f"moneyflow_mkt failed ({e1}), " f"falling back to moneyflow_ind_dc"
                )
            # 降级：用行业资金流向汇总代替
            try:
                df = await self._run(client.moneyflow_ind_dc, trade_date=dt)
                if df is not None and not df.empty:
                    return df, "moneyflow_ind_dc"
            except Exception as e2:
                self.logger.warning(f"moneyflow_ind_dc also failed: {e2}")
            return None, None

        try:
            requested_date = str(trade_date) if trade_date else None
            target_date = trade_date or datetime.now().strftime("%Y%m%d")
            actual_fetch_date = target_date
            df, data_source = await _fetch_moneyflow_mkt(target_date)
            if (df is None or df.empty) and not trade_date:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
                df, data_source = await _fetch_moneyflow_mkt(yesterday)
                actual_fetch_date = yesterday

            def _to_float(value: Any) -> Optional[float]:
                try:
                    if value is None:
                        return None
                    return float(value)
                except (TypeError, ValueError):
                    return None

            def _row_net_amount(row: Dict[str, Any]) -> float:
                for key in ("net_mf_amount", "net_amount", "net_inflow"):
                    val = _to_float(row.get(key))
                    if val is not None:
                        return val
                return 0.0

            def _row_pct_chg(row: Dict[str, Any]) -> Optional[float]:
                for key in ("pct_chg", "change_pct", "pct_change", "chg_pct"):
                    val = _to_float(row.get(key))
                    if val is not None:
                        return val
                return None

            def _row_sector_name(row: Dict[str, Any]) -> str:
                for key in ("name", "industry", "concept", "ts_code"):
                    val = row.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                return "N/A"

            data = []
            if df is not None and not df.empty:
                data = df.where(df.notnull(), None).to_dict("records")

            as_of_date = None
            if data:
                as_of_date = str(data[0].get("trade_date") or "").strip() or None
            if not as_of_date:
                as_of_date = actual_fetch_date if data else None

            if not data:
                data_freshness = "empty"
            elif requested_date and as_of_date == requested_date:
                data_freshness = "exact"
            elif requested_date and as_of_date != requested_date:
                data_freshness = "fallback_other_trade_date"
            elif trade_date is None and as_of_date != datetime.now().strftime("%Y%m%d"):
                data_freshness = "fallback_prev_trade_date"
            else:
                data_freshness = "exact"

            normalized_rows: List[Dict[str, Any]] = []
            for row in data:
                net = _row_net_amount(row)
                normalized_rows.append(
                    {
                        "sector_name": _row_sector_name(row),
                        "net_amount": net,
                        "pct_chg": _row_pct_chg(row),
                        "trade_date": str(row.get("trade_date") or as_of_date or ""),
                    }
                )

            inflow_rows = sorted(
                [r for r in normalized_rows if r["net_amount"] >= 0],
                key=lambda x: x["net_amount"],
                reverse=True,
            )
            outflow_rows = sorted(
                [r for r in normalized_rows if r["net_amount"] < 0],
                key=lambda x: x["net_amount"],
            )

            top_inflow = []
            for idx, row in enumerate(inflow_rows[:safe_top_n], start=1):
                top_inflow.append(
                    {
                        "rank": idx,
                        "sector_name": row["sector_name"],
                        "net_amount": row["net_amount"],
                        "pct_chg": row["pct_chg"],
                        "trade_date": row["trade_date"],
                    }
                )

            top_outflow = []
            if include_outflow:
                for idx, row in enumerate(outflow_rows[:safe_top_n], start=1):
                    top_outflow.append(
                        {
                            "rank": idx,
                            "sector_name": row["sector_name"],
                            "net_amount": row["net_amount"],
                            "pct_chg": row["pct_chg"],
                            "trade_date": row["trade_date"],
                        }
                    )

            total_net_amount = sum(r["net_amount"] for r in normalized_rows)
            trend_conclusion_allowed = data_freshness == "exact" and len(top_inflow) > 0
            blocked_reason = None
            if not trend_conclusion_allowed:
                if not data:
                    blocked_reason = "market_money_flow_empty"
                elif data_freshness != "exact":
                    blocked_reason = f"stale_data:{data_freshness}"
                else:
                    blocked_reason = "insufficient_rank_data"

            result = {
                "variant": "market_money_flow",
                "source": "tushare",
                "data_source": data_source or "unknown",
                "data": data,
                "requested_trade_date": requested_date,
                "as_of_trade_date": as_of_date,
                "data_freshness": data_freshness,
                "top_n": safe_top_n,
                "include_outflow": bool(include_outflow),
                "market_overview": {
                    "inflow_count": len(inflow_rows),
                    "outflow_count": len(outflow_rows),
                    "total_net_amount": total_net_amount,
                },
                "top_inflow": top_inflow,
                "top_outflow": top_outflow,
                "trend_conclusion_allowed": trend_conclusion_allowed,
                "blocked_reason": blocked_reason,
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get market money flow: {e}")
            raise ValueError(f"Failed to get market money flow: {e}")

    # ------------------------------------------------------------------
    # 方案C：三级缓存 + 模糊匹配板块名称
    # ------------------------------------------------------------------

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        """返回 SQLite 连接并确保 ths_sector_index 表存在。"""
        Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ths_sector_index (
                ts_code TEXT PRIMARY KEY,
                name    TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        return conn

    async def _get_all_sectors(self) -> List[Dict[str, str]]:
        """获取全量板块列表，三级缓存：L1内存(1h) → L2 Redis(6h) → L3 SQLite → Tushare API.

        Returns:
            [{"ts_code": "...", "name": "..."}, ...]
        """
        _L1_TTL = 3600  # 1小时

        # --- L1 进程内存 ---
        cached = TushareAdapter._sector_index_cache
        if cached and time.time() - cached.get("ts", 0) < _L1_TTL:
            return cached["data"]

        # --- L2 Redis ---
        redis_key = "tushare:all_sectors:v1"
        redis_data = await self.cache.get(redis_key)
        if redis_data:
            TushareAdapter._sector_index_cache = {
                "data": redis_data,
                "ts": time.time(),
            }
            return redis_data

        # --- L3 SQLite ---
        try:
            conn = self._get_sqlite_conn()
            rows = conn.execute(
                "SELECT ts_code, name FROM ths_sector_index ORDER BY name"
            ).fetchall()
            conn.close()
            if rows:
                sqlite_data = [dict(r) for r in rows]
                TushareAdapter._sector_index_cache = {
                    "data": sqlite_data,
                    "ts": time.time(),
                }
                await self.cache.set(redis_key, sqlite_data, ttl=21600)
                return sqlite_data
        except Exception as e:
            self.logger.warning(f"SQLite sector cache read failed: {e}")

        # --- Tushare API ---
        client = self.tushare_conn.get_client()
        if client is None:
            return []

        try:
            df = await self._run(client.ths_index, exchange="A")
            if df is None or df.empty:
                # 部分 token 不传 exchange 才能拉全量
                df = await self._run(client.ths_index)
            if df is None or df.empty:
                return []

            df = df[["ts_code", "name"]].drop_duplicates(subset=["ts_code"])
            api_data: List[Dict[str, str]] = df.to_dict("records")

            # 回写 SQLite
            try:
                conn = self._get_sqlite_conn()
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO ths_sector_index(ts_code, name, updated_at)
                    VALUES (?, ?, datetime('now'))
                    """,
                    [(r["ts_code"], r["name"]) for r in api_data],
                )
                conn.commit()
                conn.close()
            except Exception as e:
                self.logger.warning(f"SQLite sector cache write failed: {e}")

            # 回写 Redis L2
            await self.cache.set(redis_key, api_data, ttl=21600)

            # 更新 L1
            TushareAdapter._sector_index_cache = {
                "data": api_data,
                "ts": time.time(),
            }
            return api_data
        except Exception as e:
            self.logger.warning(f"Tushare ths_index full fetch failed: {e}")
            return []

    async def _resolve_sector_index(
        self, sector_name: str
    ) -> Tuple[Optional[str], Optional[str], Optional[List[str]]]:
        """将板块名称解析为 (ts_code, matched_name, candidates).
        """
        # 优先使用全量板块目录 + 统一打分，避免 ths_index(name) 第一条不稳定导致错码。
        all_sectors = await self._get_all_sectors()
        if all_sectors:
            ranked = rank_sector_candidates(
                sector_name,
                all_sectors,
                name_getter=lambda s: str(s.get("name", "")),
                top_k=20,
            )
            winner, candidate_names = pick_sector_resolution(
                sector_name,
                ranked,
                ambiguous_top_k=10,
            )
            if winner is not None:
                row = winner.item
                return str(row.get("ts_code") or ""), str(row.get("name") or ""), None
            if candidate_names:
                return None, None, candidate_names
            return None, None, None

        # 兜底：当全量目录不可用时，回退到接口直查。
        client = self.tushare_conn.get_client()
        if client is None:
            return None, None, None
        try:
            df = await self._run(client.ths_index, name=sector_name)
            if df is None or df.empty:
                return None, None, None
            code = df.iloc[0].get("ts_code")
            name = df.iloc[0].get("name", sector_name)
            if code:
                return str(code), str(name or sector_name), None
        except Exception:
            pass
        return None, None, None

    async def _resolve_sector_by_code(
        self, sector_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """通过板块 ts_code 解析名称."""
        sid = (sector_id or "").strip().upper()
        if not sid:
            return None, None
        all_sectors = await self._get_all_sectors()
        if not all_sectors:
            return None, None
        for item in all_sectors:
            if str(item.get("ts_code", "")).upper() == sid:
                return str(item.get("ts_code")), str(item.get("name", ""))
        return None, None

    async def resolve_sector(
        self, query_text: str, intent: str = "trend"
    ) -> Dict[str, Any]:
        """解析板块查询词，返回稳定板块ID."""
        query = (query_text or "").strip()
        if not query:
            return {
                "variant": "sector_resolve",
                "source": "tushare",
                "status": "not_found",
                "query_text": query_text,
                "intent": intent,
                "reason": "empty query_text",
            }

        # 支持直接传入 ts_code
        by_code, by_code_name = await self._resolve_sector_by_code(query)
        if by_code:
            return {
                "variant": "sector_resolve",
                "source": "tushare",
                "status": "resolved",
                "query_text": query_text,
                "intent": intent,
                "sector_id": by_code,
                "canonical_name": by_code_name or query,
            }

        index_code, matched_name, candidates = await self._resolve_sector_index(query)
        if index_code:
            return {
                "variant": "sector_resolve",
                "source": "tushare",
                "status": "resolved",
                "query_text": query_text,
                "intent": intent,
                "sector_id": index_code,
                "canonical_name": matched_name or query,
            }

        if candidates:
            all_sectors = await self._get_all_sectors()
            code_map = {str(x.get("name", "")): str(x.get("ts_code", "")) for x in all_sectors}
            candidate_rows = [
                {
                    "sector_id": code_map.get(name, ""),
                    "canonical_name": name,
                    "source": "tushare",
                }
                for name in candidates
            ]
            return {
                "variant": "sector_resolve",
                "source": "tushare",
                "status": "ambiguous",
                "query_text": query_text,
                "intent": intent,
                "candidates": candidate_rows,
            }

        return {
            "variant": "sector_resolve",
            "source": "tushare",
            "status": "not_found",
            "query_text": query_text,
            "intent": intent,
            "reason": f"no sector matched for '{query}'",
        }

    async def get_sector_trend(
        self,
        sector_name: str = "",
        days: int = 10,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块走势数据（支持模糊匹配板块名称）."""
        cache_key = (
            f"tushare:sector_trend:{(sector_id or '').strip().upper()}:"
            f"{sector_name}:{days}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            query_name = (sector_name or "").strip()
            query_sector_id = (sector_id or "").strip().upper()
            matched_name: Optional[str] = None
            candidates: Optional[List[str]] = None

            if query_sector_id:
                index_code, code_name = await self._resolve_sector_by_code(query_sector_id)
                matched_name = code_name or query_name or query_sector_id
                # Cross-adapter fallback: if sector_id dialect mismatches Tushare
                # (e.g. AkShare BK0478), retry by sector_name.
                if index_code is None and query_name:
                    index_code, matched_name, candidates = await self._resolve_sector_index(
                        query_name
                    )
            else:
                # 方案C：使用三级缓存 + 模糊匹配解析板块
                index_code, matched_name, candidates = await self._resolve_sector_index(
                    query_name
                )

            if index_code is None:
                if candidates:
                    return {
                        "error": f"板块名称 '{query_name}' 不明确，请从以下候选中选择",
                        "candidates": candidates,
                        "sector_name": query_name,
                        "sector_id": query_sector_id,
                        "variant": "sector_trend",
                        "source": "tushare",
                    }
                if query_sector_id:
                    raise ValueError(f"No sector index found for id '{query_sector_id}'")
                raise ValueError(f"No sector index found for '{query_name}'")

            display_name = matched_name or query_name or query_sector_id
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)

            daily_df = await self._run(
                client.ths_daily,
                ts_code=index_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if daily_df is None or daily_df.empty:
                raise ValueError(f"No sector daily data for {display_name}")

            daily_df = daily_df.sort_values("trade_date").tail(days)
            # ths_daily 部分版本返回 pct_change，统一归一化到 pct_chg
            if "pct_change" in daily_df.columns and "pct_chg" not in daily_df.columns:
                daily_df = daily_df.rename(columns={"pct_change": "pct_chg"})
            daily_df = daily_df.where(daily_df.notnull(), None)
            trend = daily_df.to_dict("records")
            total_pct_chg = (
                float(daily_df["pct_chg"].fillna(0).sum())
                if "pct_chg" in daily_df
                else 0.0
            )

            result = {
                "variant": "sector_trend",
                "source": "tushare",
                "sector_name": display_name,
                "index_code": index_code,
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
                "variant": "ggt_daily",
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
            status = "no_data"
            no_data_reason = "tushare.fina_mainbz returned empty rows"
            dimensions_found = []
            latest_period = None
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False)
                rows = df.where(df.notnull(), None).to_dict("records")
                status = "ok"
                no_data_reason = None
                dimensions_found = sorted(
                    {
                        str(row.get("type"))
                        for row in rows
                        if isinstance(row, dict) and row.get("type")
                    }
                )
                latest_period = next(
                    (
                        str(row.get("end_date"))
                        for row in rows
                        if isinstance(row, dict) and row.get("end_date")
                    ),
                    None,
                )
            result = {
                "variant": "mainbz_info",
                "source": "tushare",
                "ts_code": ts_code,
                "rows": rows,
                "status": status,
                "no_data_reason": no_data_reason,
                "coverage": {
                    "row_count": len(rows),
                    "dimensions_found": dimensions_found,
                    "expected_dimensions": ["P", "D", "I"],
                    "latest_period": latest_period,
                },
                "reroute_if_no_data": [
                    "use_filings_or_web_news_for_business_mix_narrative",
                    "fallback_to_income_and_shareholder_tools",
                ],
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
                "variant": "shareholder_info",
                "source": "tushare",
                "ts_code": ts_code,
                "data": {
                    "top10_holders": (
                        holders_df.where(holders_df.notnull(), None).to_dict("records")
                        if holders_df is not None
                        else []
                    ),
                    "top10_floatholders": (
                        float_df.where(float_df.notnull(), None).to_dict("records")
                        if float_df is not None
                        else []
                    ),
                    "holder_number": (
                        number_df.where(number_df.notnull(), None).to_dict("records")
                        if number_df is not None
                        else []
                    ),
                    "holder_trade": (
                        trade_df.where(trade_df.notnull(), None).to_dict("records")
                        if trade_df is not None
                        else []
                    ),
                },
            }
            await self.cache.set(cache_key, result, ttl=3600)
            return result
        except Exception as e:
            self.logger.error(f"Failed to get shareholder info: {e}")
            raise ValueError(f"Failed to get shareholder info: {e}")

    async def get_valuation_metrics(
        self, ticker: str, days: int = 250
    ) -> Dict[str, Any]:
        """获取估值指标数据 (PE/PB/PS/PCF + 历史百分位).

        通过 daily_basic 接口拉取近 N 个交易日的数据，
        计算当前值在历史区间中的百分位，帮助判断估值水平。

        Args:
            ticker: 股票代码 (内部格式 SSE:600519)
            days: 拉取最近 N 个交易日数据 (默认 250，约一年)

        Returns:
            包含估值指标及百分位信息的结构化数据
        """
        ts_code = self._to_ts_code(ticker)
        cache_key = f"tushare:valuation:{ts_code}:{days}"

        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            end_date = datetime.now()
            # 多取一些天数以确保有足够交易日数据
            start_date = end_date - timedelta(days=int(days * 1.6))

            df = await self._run(
                client.daily_basic,
                ts_code=ts_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                fields="ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,turnover_rate,volume_ratio",
            )

            if df is None or df.empty:
                return {
                    "error": f"No valuation data for {ticker}",
                    "symbol": ts_code,
                    "source": "tushare",
                }

            df = df.sort_values("trade_date").tail(days)

            # 将数值列转为 float
            metric_cols = [
                "pe",
                "pe_ttm",
                "pb",
                "ps",
                "ps_ttm",
                "dv_ratio",
                "dv_ttm",
                "total_mv",
                "circ_mv",
                "turnover_rate",
                "volume_ratio",
            ]
            for col in metric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            latest = df.iloc[-1]

            def _percentile(series: pd.Series, current_value) -> float | None:
                """计算 current_value 在 series 中的历史百分位 (0~100)."""
                valid = series.dropna()
                if valid.empty or pd.isna(current_value):
                    return None
                return round(float((valid < current_value).sum() / len(valid) * 100), 1)

            def _safe_float(val) -> float | None:
                if pd.isna(val):
                    return None
                return round(float(val), 4)

            # 构建各指标的当前值 + 百分位
            metrics = {}
            for col in ["pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm"]:
                if col not in df.columns:
                    continue
                current = _safe_float(latest.get(col))
                pct = _percentile(df[col], latest.get(col))
                series_data = df[col].dropna()
                metrics[col] = {
                    "current": current,
                    "percentile": pct,
                    "min": (
                        _safe_float(series_data.min())
                        if not series_data.empty
                        else None
                    ),
                    "max": (
                        _safe_float(series_data.max())
                        if not series_data.empty
                        else None
                    ),
                    "mean": (
                        _safe_float(series_data.mean())
                        if not series_data.empty
                        else None
                    ),
                    "median": (
                        _safe_float(series_data.median())
                        if not series_data.empty
                        else None
                    ),
                }

            # 市值信息
            market_cap = {
                "total_mv": _safe_float(latest.get("total_mv")),
                "circ_mv": _safe_float(latest.get("circ_mv")),
            }

            # 历史序列 (用于图表)
            dates = (
                df["trade_date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}").tolist()
            )
            history = {"dates": dates}
            for col in ["pe_ttm", "pb", "ps_ttm"]:
                if col in df.columns:
                    history[col] = [_safe_float(v) for v in df[col].tolist()]

            # 估值水平判断
            pe_pct = metrics.get("pe_ttm", {}).get("percentile")
            pb_pct = metrics.get("pb", {}).get("percentile")
            avg_pct = None
            pct_values = [v for v in [pe_pct, pb_pct] if v is not None]
            if pct_values:
                avg_pct = sum(pct_values) / len(pct_values)

            if avg_pct is not None:
                if avg_pct <= 20:
                    level = "低估"
                elif avg_pct <= 40:
                    level = "偏低"
                elif avg_pct <= 60:
                    level = "适中"
                elif avg_pct <= 80:
                    level = "偏高"
                else:
                    level = "高估"
            else:
                level = "未知"

            result = {
                "symbol": ts_code,
                "ticker": ticker,
                "variant": "valuation_metrics",
                "source": "tushare",
                "trade_date": str(latest.get("trade_date", "")),
                "metrics": metrics,
                "market_cap": market_cap,
                "history": history,
                "summary": {
                    "valuation_level": level,
                    "pe_ttm_percentile": pe_pct,
                    "pb_percentile": pb_pct,
                    "period_days": len(df),
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get valuation metrics for {ticker}: {e}")
            raise ValueError(f"Failed to get valuation metrics: {e}")

    async def get_sector_money_flow_history(
        self,
        sector_name: str = "",
        days: int = 20,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块资金流向历史数据（支持模糊匹配板块名称）.

        通过同花顺行业指数 (ths_index) 查找板块代码，
        再用 ths_daily 获取含成交量/成交额的日线数据，
        结合 moneyflow_ind (行业资金流向) 提供主力/散户资金净流入。

        Args:
            sector_name: 板块名称 (如 "白酒", "半导体", "新能源")
            days: 获取最近 N 个交易日数据 (默认 20)

        Returns:
            包含板块资金流向历史数据的结构化字典
        """
        cache_key = (
            f"tushare:sector_money_flow:{(sector_id or '').strip().upper()}:"
            f"{sector_name}:{days}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            query_name = (sector_name or "").strip()
            query_sector_id = (sector_id or "").strip().upper()

            if query_sector_id:
                index_code, code_name = await self._resolve_sector_by_code(query_sector_id)
                index_name = code_name or query_name or query_sector_id
                candidates = None
                # Cross-adapter fallback: if sector_id dialect mismatches Tushare
                # (e.g. AkShare BK0478), retry by sector_name.
                if index_code is None and query_name:
                    index_code, index_name, candidates = await self._resolve_sector_index(
                        query_name
                    )
            else:
                # Step 1: 方案C — 三级缓存 + 模糊匹配解析板块
                index_code, index_name, candidates = await self._resolve_sector_index(
                    query_name
                )

            if index_code is None:
                if candidates:
                    return {
                        "error": (
                            f"板块名称 '{query_name}' 不明确，" "请从以下候选中选择"
                        ),
                        "candidates": candidates,
                        "sector_name": query_name,
                        "sector_id": query_sector_id,
                        "variant": "sector_flow",
                        "source": "tushare",
                    }
                if query_sector_id:
                    return {
                        "error": f"未找到板块ID: {query_sector_id}",
                        "sector_name": query_name,
                        "sector_id": query_sector_id,
                        "source": "tushare",
                        "variant": "sector_flow",
                    }
                return {
                    "error": f"未找到板块: {query_name}",
                    "sector_name": query_name,
                    "source": "tushare",
                    "variant": "sector_flow",
                }

            index_name = index_name or query_name or query_sector_id

            end_date = datetime.now()
            start_date = end_date - timedelta(days=int(days * 1.6))

            # Step 2: 获取板块日线行情 (ths_daily)
            daily_df = await self._run(
                client.ths_daily,
                ts_code=index_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            # Step 3: 尝试获取行业资金流向（多源回退）
            flow_df = None
            flow_source = None
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")
            flow_attempts = [
                ("moneyflow_ind", {"ts_code": index_code}),
                ("moneyflow_ind_ths", {"ts_code": index_code}),
                ("moneyflow_ind_ths", {"ths_code": index_code}),
            ]

            for api_name, kwargs in flow_attempts:
                try:
                    api = getattr(client, api_name)
                    df_try = await self._run(
                        api,
                        start_date=start_str,
                        end_date=end_str,
                        **kwargs,
                    )
                    if df_try is not None and not df_try.empty:
                        flow_df = df_try
                        flow_source = api_name
                        break
                except Exception as flow_err:
                    self.logger.debug(
                        f"{api_name} unavailable for {query_name or query_sector_id}/{index_code}: "
                        f"{flow_err}"
                    )

            # 处理日线行情
            records = []
            if daily_df is not None and not daily_df.empty:
                daily_df = daily_df.sort_values("trade_date").tail(days)
                # ths_daily 部分版本返回 pct_change，统一归一化到 pct_chg
                if (
                    "pct_change" in daily_df.columns
                    and "pct_chg" not in daily_df.columns
                ):
                    daily_df = daily_df.rename(columns={"pct_change": "pct_chg"})
                for col in [
                    "close",
                    "open",
                    "high",
                    "low",
                    "pct_chg",
                    "vol",
                    "turnover_rate",
                ]:
                    if col in daily_df.columns:
                        daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce")

                for _, row in daily_df.iterrows():
                    td = str(row.get("trade_date", ""))
                    rec = {
                        "trade_date": td,
                        "close": (
                            float(row["close"]) if pd.notna(row.get("close")) else None
                        ),
                        "pct_chg": (
                            float(row["pct_chg"])
                            if pd.notna(row.get("pct_chg"))
                            else None
                        ),
                        "vol": float(row["vol"]) if pd.notna(row.get("vol")) else None,
                        "turnover_rate": (
                            float(row["turnover_rate"])
                            if pd.notna(row.get("turnover_rate"))
                            else None
                        ),
                    }
                    records.append(rec)

            # 仍无资金流时，用行业DC口径按日期回补 net_amount
            if (flow_df is None or flow_df.empty) and records:
                dc_rows: List[Dict[str, Any]] = []
                for rec in records:
                    td = str(rec.get("trade_date", ""))
                    if not td:
                        continue
                    try:
                        dc_df = await self._run(client.moneyflow_ind_dc, trade_date=td)
                        if dc_df is None or dc_df.empty:
                            continue
                        row = None
                        if "ts_code" in dc_df.columns:
                            hit = dc_df[dc_df["ts_code"].astype(str) == index_code]
                            if not hit.empty:
                                row = hit.iloc[0]
                        if row is None and "name" in dc_df.columns:
                            names = [index_name, query_name]
                            hit = dc_df[
                                dc_df["name"].astype(str).isin([n for n in names if n])
                            ]
                            if hit.empty:
                                hit = dc_df[
                                    dc_df["name"].astype(str).str.contains(
                                        str(query_name), na=False
                                    )
                                ]
                            if not hit.empty:
                                row = hit.iloc[0]
                        if row is not None:
                            item = {k: row.get(k) for k in dc_df.columns}
                            item["trade_date"] = td
                            dc_rows.append(item)
                    except Exception as dc_err:
                        self.logger.debug(
                            f"moneyflow_ind_dc fallback failed for {query_name or query_sector_id} "
                            f"on {td}: {dc_err}"
                        )
                if dc_rows:
                    flow_df = pd.DataFrame(dc_rows)
                    flow_source = "moneyflow_ind_dc"

            # 合并资金流向数据
            if flow_df is not None and not flow_df.empty:
                flow_df = flow_df.sort_values("trade_date").tail(days)
                flow_map = {}
                for _, row in flow_df.iterrows():
                    td = str(row.get("trade_date", ""))
                    flow_map[td] = {
                        "buy_elg_amount": (
                            float(row["buy_elg_amount"])
                            if pd.notna(row.get("buy_elg_amount"))
                            else 0
                        ),
                        "sell_elg_amount": (
                            float(row["sell_elg_amount"])
                            if pd.notna(row.get("sell_elg_amount"))
                            else 0
                        ),
                        "buy_lg_amount": (
                            float(row["buy_lg_amount"])
                            if pd.notna(row.get("buy_lg_amount"))
                            else 0
                        ),
                        "sell_lg_amount": (
                            float(row["sell_lg_amount"])
                            if pd.notna(row.get("sell_lg_amount"))
                            else 0
                        ),
                        "buy_md_amount": (
                            float(row["buy_md_amount"])
                            if pd.notna(row.get("buy_md_amount"))
                            else 0
                        ),
                        "sell_md_amount": (
                            float(row["sell_md_amount"])
                            if pd.notna(row.get("sell_md_amount"))
                            else 0
                        ),
                        "buy_sm_amount": (
                            float(row["buy_sm_amount"])
                            if pd.notna(row.get("buy_sm_amount"))
                            else 0
                        ),
                        "sell_sm_amount": (
                            float(row["sell_sm_amount"])
                            if pd.notna(row.get("sell_sm_amount"))
                            else 0
                        ),
                        "net_amount": (
                            float(row["net_amount"])
                            if pd.notna(row.get("net_amount"))
                            else None
                        ),
                        "net_amount_rate": (
                            float(row["net_amount_rate"])
                            if pd.notna(row.get("net_amount_rate"))
                            else (
                                float(row["net_mf_rate"])
                                if pd.notna(row.get("net_mf_rate"))
                                else None
                            )
                        ),
                    }
                for rec in records:
                    flow = flow_map.get(rec["trade_date"])
                    if flow:
                        net_amount = flow.get("net_amount")
                        if net_amount is not None:
                            rec["main_net_inflow"] = round(float(net_amount), 2)
                            rec["retail_net_inflow"] = None
                            rec["total_net_inflow"] = round(float(net_amount), 2)
                            if flow.get("net_amount_rate") is not None:
                                rec["net_amount_rate"] = round(
                                    float(flow["net_amount_rate"]),
                                    2,
                                )
                        else:
                            main_buy = flow["buy_elg_amount"] + flow["buy_lg_amount"]
                            main_sell = flow["sell_elg_amount"] + flow["sell_lg_amount"]
                            retail_buy = flow["buy_md_amount"] + flow["buy_sm_amount"]
                            retail_sell = flow["sell_md_amount"] + flow["sell_sm_amount"]
                            rec["main_net_inflow"] = round(main_buy - main_sell, 2)
                            rec["retail_net_inflow"] = round(
                                retail_buy - retail_sell, 2
                            )
                            rec["total_net_inflow"] = round(
                                rec["main_net_inflow"] + rec["retail_net_inflow"],
                                2,
                            )

            # 汇总
            has_flow = any("main_net_inflow" in r for r in records)
            total_main = sum(r.get("main_net_inflow", 0) for r in records)
            total_pct_chg = sum(r.get("pct_chg", 0) or 0 for r in records)
            amount_unit = "unknown"
            if has_flow:
                if flow_source == "moneyflow_ind_dc":
                    # Eastmoney 行业资金流向 net_amount 的原始单位与 Tushare 口径不同，
                    # 先标记为未知，避免错误地当作“元”格式化。
                    amount_unit = "unknown"
                else:
                    # moneyflow_ind / moneyflow_ind_ths 的金额字段口径按万元处理。
                    amount_unit = "10k_cny"

            if has_flow:
                if total_main > 0:
                    trend = "主力资金持续流入"
                else:
                    trend = "主力资金持续流出"
            else:
                trend = "仅行情数据"

            result = {
                "variant": "sector_money_flow",
                "source": "tushare",
                "sector_name": index_name,
                "index_code": index_code,
                "days": len(records),
                "has_money_flow": has_flow,
                "amount_unit": amount_unit,
                "records": records,
                "summary": {
                    "total_pct_chg": round(total_pct_chg, 2),
                    "total_main_net": (round(total_main, 2) if has_flow else None),
                    "trend": trend,
                    "flow_source": flow_source,
                    "amount_unit": amount_unit,
                },
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(
                f"Failed to get sector money flow for "
                f"{query_name or query_sector_id}: {e}"
            )
            raise ValueError(f"Failed to get sector money flow: {e}")

    async def get_sector_valuation_metrics(
        self,
        sector_name: str = "",
        days: int = 250,
        sample_size: int = 60,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块估值指标(PE/PB)及历史分位.

        数据来源：
        1) 通过 ths_index + ths_member 解析板块与成分股
        2) 对成分股批量拉取 daily_basic(PE_TTM/PB/总市值)
        3) 按交易日聚合为板块加权估值序列，并计算当前历史百分位
        """
        safe_days = max(30, min(int(days), 750))
        safe_sample = max(10, min(int(sample_size), 200))
        cache_key = (
            f"tushare:sector_valuation:{(sector_id or '').strip().upper()}:"
            f"{sector_name}:{safe_days}:{safe_sample}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        client = self.tushare_conn.get_client()
        if client is None:
            raise ValueError("Tushare client not available")

        try:
            query_name = (sector_name or "").strip()
            query_sector_id = (sector_id or "").strip().upper()

            if query_sector_id:
                index_code, code_name = await self._resolve_sector_by_code(query_sector_id)
                index_name = code_name or query_name or query_sector_id
                candidates = None
                # Cross-adapter fallback: if sector_id dialect mismatches Tushare
                # (e.g. AkShare BK0478), retry by sector_name.
                if index_code is None and query_name:
                    index_code, index_name, candidates = await self._resolve_sector_index(
                        query_name
                    )
            else:
                index_code, index_name, candidates = await self._resolve_sector_index(
                    query_name
                )

            if index_code is None:
                if candidates:
                    return {
                        "error": f"板块名称 '{query_name}' 不明确，请从候选中选择",
                        "candidates": candidates,
                        "sector_name": query_name,
                        "sector_id": query_sector_id,
                        "variant": "sector_valuation_metrics",
                        "source": "tushare",
                    }
                if query_sector_id:
                    return {
                        "error": f"未找到板块ID: {query_sector_id}",
                        "sector_name": query_name,
                        "sector_id": query_sector_id,
                        "variant": "sector_valuation_metrics",
                        "source": "tushare",
                    }
                return {
                    "error": f"未找到板块: {query_name}",
                    "sector_name": query_name,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                }

            display_name = index_name or query_name or query_sector_id

            member_df = await self._run(client.ths_member, ts_code=index_code)
            if member_df is None or member_df.empty:
                return {
                    "error": f"板块 {display_name} 无成分股数据",
                    "sector_name": display_name,
                    "index_code": index_code,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                }

            member_codes: List[str] = []
            for col in ("con_code", "code", "ts_code"):
                if col in member_df.columns:
                    vals = (
                        member_df[col]
                        .astype(str)
                        .str.upper()
                        .str.strip()
                        .tolist()
                    )
                    member_codes.extend(vals)
            member_codes = [
                x
                for x in member_codes
                if x.endswith((".SH", ".SZ", ".BJ")) and len(x) >= 9
            ]
            member_codes = list(dict.fromkeys(member_codes))
            if not member_codes:
                return {
                    "error": f"板块 {display_name} 成分股为空",
                    "sector_name": display_name,
                    "index_code": index_code,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                }

            selected_codes = member_codes[:safe_sample]
            end_date = datetime.now()
            start_date = end_date - timedelta(days=int(safe_days * 1.9))
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")
            sem = asyncio.Semaphore(8)

            async def _fetch_one(ts_code: str) -> Optional[pd.DataFrame]:
                async with sem:
                    try:
                        df = await self._run(
                            client.daily_basic,
                            ts_code=ts_code,
                            start_date=start_str,
                            end_date=end_str,
                            fields="ts_code,trade_date,pe_ttm,pb,total_mv",
                        )
                        if df is None or df.empty:
                            return None
                        keep = [
                            c
                            for c in ["ts_code", "trade_date", "pe_ttm", "pb", "total_mv"]
                            if c in df.columns
                        ]
                        if not keep:
                            return None
                        return df[keep]
                    except Exception as e:
                        self.logger.debug(
                            f"daily_basic failed for {ts_code} in sector "
                            f"{display_name}: {e}"
                        )
                        return None

            frames = [
                df
                for df in await asyncio.gather(
                    *[_fetch_one(code) for code in selected_codes]
                )
                if df is not None and not df.empty
            ]
            if not frames:
                return {
                    "error": f"板块 {display_name} 估值数据为空",
                    "sector_name": display_name,
                    "index_code": index_code,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                    "member_count_total": len(member_codes),
                    "member_count_used": len(selected_codes),
                }

            df_all = pd.concat(frames, ignore_index=True)
            for col in ["pe_ttm", "pb", "total_mv"]:
                if col in df_all.columns:
                    df_all[col] = pd.to_numeric(df_all[col], errors="coerce")
            df_all["trade_date"] = df_all["trade_date"].astype(str)
            df_all = df_all.dropna(subset=["trade_date", "ts_code"])

            if df_all.empty:
                return {
                    "error": f"板块 {display_name} 估值数据为空",
                    "sector_name": display_name,
                    "index_code": index_code,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                }

            def _agg_metric(metric: str) -> pd.Series:
                data = df_all[["trade_date", "ts_code", metric, "total_mv"]].copy()
                data = data.dropna(subset=[metric])
                data = data[data[metric] > 0]
                if data.empty:
                    return pd.Series(dtype=float)

                def _reduce(g: pd.DataFrame) -> float:
                    w = g["total_mv"]
                    v = g[metric]
                    mask = w.notna() & (w > 0)
                    if int(mask.sum()) >= 3:
                        denom = float(w[mask].sum())
                        if denom > 0:
                            return float((v[mask] * w[mask]).sum() / denom)
                    return float(v.mean())

                return data.groupby("trade_date").apply(_reduce)

            pe_series = _agg_metric("pe_ttm")
            pb_series = _agg_metric("pb")
            coverage = df_all.groupby("trade_date")["ts_code"].nunique()

            history_df = pd.DataFrame({"trade_date": coverage.index})
            history_df = history_df.set_index("trade_date")
            history_df["coverage"] = coverage.astype(int)
            if not pe_series.empty:
                history_df["pe_ttm"] = pe_series
            if not pb_series.empty:
                history_df["pb"] = pb_series

            history_df = history_df.sort_index().tail(safe_days)
            history_df = history_df.reset_index()
            if history_df.empty:
                return {
                    "error": f"板块 {display_name} 历史估值序列为空",
                    "sector_name": display_name,
                    "index_code": index_code,
                    "variant": "sector_valuation_metrics",
                    "source": "tushare",
                }

            def _percentile(series: pd.Series, current: Any) -> Optional[float]:
                valid = series.dropna()
                if valid.empty or pd.isna(current):
                    return None
                return round(float((valid < current).sum() / len(valid) * 100), 1)

            latest = history_df.iloc[-1]
            curr_pe = float(latest["pe_ttm"]) if pd.notna(latest.get("pe_ttm")) else None
            curr_pb = float(latest["pb"]) if pd.notna(latest.get("pb")) else None

            pe_pct = _percentile(history_df.get("pe_ttm", pd.Series(dtype=float)), curr_pe)
            pb_pct = _percentile(history_df.get("pb", pd.Series(dtype=float)), curr_pb)

            avg_pct = None
            pct_vals = [v for v in [pe_pct, pb_pct] if v is not None]
            if pct_vals:
                avg_pct = sum(pct_vals) / len(pct_vals)
            if avg_pct is None:
                level = "未知"
            elif avg_pct <= 20:
                level = "低估"
            elif avg_pct <= 40:
                level = "偏低"
            elif avg_pct <= 60:
                level = "适中"
            elif avg_pct <= 80:
                level = "偏高"
            else:
                level = "高估"

            history_rows: List[Dict[str, Any]] = []
            for _, row in history_df.iterrows():
                history_rows.append(
                    {
                        "trade_date": str(row.get("trade_date", "")),
                        "pe_ttm": (
                            round(float(row["pe_ttm"]), 4)
                            if pd.notna(row.get("pe_ttm"))
                            else None
                        ),
                        "pb": (
                            round(float(row["pb"]), 4)
                            if pd.notna(row.get("pb"))
                            else None
                        ),
                        "coverage": (
                            int(row["coverage"])
                            if pd.notna(row.get("coverage"))
                            else None
                        ),
                    }
                )

            result = {
                "variant": "sector_valuation_metrics",
                "source": "tushare",
                "sector_name": display_name,
                "index_code": index_code,
                "days": len(history_rows),
                "member_count_total": len(member_codes),
                "member_count_used": len(selected_codes),
                "member_count_with_data": int(df_all["ts_code"].nunique()),
                "current": {
                    "trade_date": str(latest.get("trade_date", "")),
                    "pe_ttm": (round(curr_pe, 4) if curr_pe is not None else None),
                    "pb": (round(curr_pb, 4) if curr_pb is not None else None),
                },
                "summary": {
                    "valuation_level": level,
                    "pe_ttm_percentile": pe_pct,
                    "pb_percentile": pb_pct,
                    "coverage_latest": (
                        int(latest.get("coverage"))
                        if pd.notna(latest.get("coverage"))
                        else None
                    ),
                },
                "history": history_rows,
            }

            await self.cache.set(cache_key, result, ttl=1800)
            return result

        except Exception as e:
            self.logger.error(
                f"Failed to get sector valuation metrics for "
                f"{query_name or query_sector_id}: {e}"
            )
            raise ValueError(f"Failed to get sector valuation metrics: {e}")

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
        df = df.rename(
            columns={
                "close_price": "close",
                "open_price": "open",
                "high_price": "high",
                "low_price": "low",
            }
        )

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
                    "hist": _series_to_list(hist),
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
                    k = (2 / 3) * k + (1 / 3) * r
                    d = (2 / 3) * d + (1 / 3) * k
                    j = 3 * k - 2 * d
                    k_list.append(k)
                    d_list.append(d)
                    j_list.append(j)

                result["indicators"]["kdj"] = {"k": k_list, "d": d_list, "j": j_list}

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

                row.update(
                    {
                        "MA5": ma.get("ma5", [None] * len(df)).__getitem__(idx),
                        "MA10": ma.get("ma10", [None] * len(df)).__getitem__(idx),
                        "MA20": ma.get("ma20", [None] * len(df)).__getitem__(idx),
                        "MA60": ma.get("ma60", [None] * len(df)).__getitem__(idx),
                        "MACD": macd_ind.get("diff", [None] * len(df)).__getitem__(idx),
                        "MACD_signal": macd_ind.get(
                            "dea", [None] * len(df)
                        ).__getitem__(idx),
                        "RSI": rsi_list[idx] if idx < len(rsi_list) else None,
                        "K": kdj.get("k", [None] * len(df)).__getitem__(idx),
                        "D": kdj.get("d", [None] * len(df)).__getitem__(idx),
                        "J": kdj.get("j", [None] * len(df)).__getitem__(idx),
                    }
                )
                rows.append(row)

            result["rows"] = rows
            result["current_price"] = float(df["close"].iloc[-1])

            return result

        except Exception as e:
            self.logger.error(f"Failed to calculate indicators for {ticker}: {e}")
            raise ValueError(f"Failed to calculate indicators: {e}")
