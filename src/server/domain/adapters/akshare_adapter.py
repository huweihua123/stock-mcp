# src/server/domain/adapters/akshare_adapter.py
"""Akshare adapter for Chinese market data.

All methods are async via asyncio.run_in_executor to avoid blocking
the event loop.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.cninfo_helper import (
    _normalize_stock_code,
    fetch_cninfo_data,
)
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

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None

    @staticmethod
    def _to_trade_date_yyyymmdd(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        text = text.replace("/", "-")
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10].replace("-", "")
        if len(text) == 8 and text.isdigit():
            return text
        try:
            dt = datetime.fromisoformat(text)
            return dt.strftime("%Y%m%d")
        except Exception:
            return text.replace("-", "")

    async def _get_board_catalog(self) -> List[Dict[str, str]]:
        """Get unified industry/concept board name catalog with cache."""
        cache_key = "akshare:board_catalog:v1"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        catalog: List[Dict[str, str]] = []
        try:
            ind_df, concept_df = await asyncio.gather(
                self._run(ak.stock_board_industry_name_em),
                self._run(ak.stock_board_concept_name_em),
                return_exceptions=True,
            )
            if isinstance(ind_df, pd.DataFrame) and not ind_df.empty:
                for _, row in ind_df.iterrows():
                    name = str(row.get("板块名称", "")).strip()
                    code = str(row.get("板块代码", "")).strip()
                    if name:
                        catalog.append(
                            {
                                "name": name,
                                "code": code,
                                "board_type": "industry",
                            }
                        )
            if isinstance(concept_df, pd.DataFrame) and not concept_df.empty:
                for _, row in concept_df.iterrows():
                    name = str(row.get("板块名称", "")).strip()
                    code = str(row.get("板块代码", "")).strip()
                    if name:
                        catalog.append(
                            {
                                "name": name,
                                "code": code,
                                "board_type": "concept",
                            }
                        )
        except Exception as e:
            self.logger.warning(f"Failed to build AK board catalog: {e}")

        if catalog:
            await self.cache.set(cache_key, catalog, ttl=3600)
        return catalog

    async def _resolve_board(
        self, sector_name: str
    ) -> tuple[Optional[Dict[str, str]], Optional[List[str]]]:
        """Resolve board by token recall + scoring."""
        catalog = await self._get_board_catalog()
        if not catalog:
            return None, None

        ranked = rank_sector_candidates(
            sector_name,
            catalog,
            name_getter=lambda row: str(row.get("name", "")),
            top_k=20,
        )
        winner, candidate_names = pick_sector_resolution(
            sector_name,
            ranked,
            ambiguous_top_k=10,
        )
        if winner is not None:
            return winner.item, None
        if candidate_names:
            return None, candidate_names
        return None, None

    async def _resolve_board_by_code(self, sector_id: str) -> Optional[Dict[str, str]]:
        """Resolve board by code."""
        sid = (sector_id or "").strip().upper()
        if not sid:
            return None
        catalog = await self._get_board_catalog()
        if not catalog:
            return None
        for item in catalog:
            if str(item.get("code", "")).upper() == sid:
                return item
        return None

    async def resolve_sector(
        self, query_text: str, intent: str = "trend"
    ) -> Dict[str, Any]:
        """Resolve sector query into stable sector_id."""
        query = (query_text or "").strip()
        if not query:
            return {
                "variant": "sector_resolve",
                "source": "akshare",
                "status": "not_found",
                "query_text": query_text,
                "intent": intent,
                "reason": "empty query_text",
            }

        board = await self._resolve_board_by_code(query)
        if board:
            return {
                "variant": "sector_resolve",
                "source": "akshare",
                "status": "resolved",
                "query_text": query_text,
                "intent": intent,
                "sector_id": board.get("code", ""),
                "canonical_name": board.get("name", query),
                "board_type": board.get("board_type", ""),
            }

        board, candidates = await self._resolve_board(query)
        if board:
            return {
                "variant": "sector_resolve",
                "source": "akshare",
                "status": "resolved",
                "query_text": query_text,
                "intent": intent,
                "sector_id": board.get("code", ""),
                "canonical_name": board.get("name", query),
                "board_type": board.get("board_type", ""),
            }

        if candidates:
            catalog = await self._get_board_catalog()
            name_map = {str(x.get("name", "")): x for x in catalog}
            candidate_rows = []
            for name in candidates:
                row = name_map.get(name, {})
                candidate_rows.append(
                    {
                        "sector_id": row.get("code", ""),
                        "canonical_name": name,
                        "board_type": row.get("board_type", ""),
                        "source": "akshare",
                    }
                )
            return {
                "variant": "sector_resolve",
                "source": "akshare",
                "status": "ambiguous",
                "query_text": query_text,
                "intent": intent,
                "candidates": candidate_rows,
            }

        return {
            "variant": "sector_resolve",
            "source": "akshare",
            "status": "not_found",
            "query_text": query_text,
            "intent": intent,
            "reason": f"no sector matched for '{query}'",
        }

    async def _fetch_board_hist(
        self, board: Dict[str, str], days: int
    ) -> pd.DataFrame:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(days * 3, 40))
        symbol = board["name"]
        if board.get("board_type") == "industry":
            df = await self._run(
                ak.stock_board_industry_hist_em,
                symbol=symbol,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                period="日k",
                adjust="",
            )
        else:
            df = await self._run(
                ak.stock_board_concept_hist_em,
                symbol=symbol,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
            )
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

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

    async def get_market_money_flow(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        include_outflow: bool = True,
    ) -> Dict[str, Any]:
        """Get sector money flow ranking via AkShare Eastmoney endpoints."""
        safe_top_n = max(1, min(int(top_n), 100))
        cache_key = (
            f"akshare:market_money_flow:{trade_date or 'latest'}:"
            f"{safe_top_n}:{int(bool(include_outflow))}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        requested_date = str(trade_date) if trade_date else None
        as_of_date = datetime.now().strftime("%Y%m%d")
        data_freshness = "exact"
        if requested_date and requested_date != as_of_date:
            data_freshness = "fallback_other_trade_date"

        try:
            df = await self._run(
                ak.stock_sector_fund_flow_rank,
                indicator="今日",
                sector_type="行业资金流",
            )
            if df is None or df.empty:
                result = {
                    "variant": "market_money_flow",
                    "source": "akshare",
                    "data_source": "stock_sector_fund_flow_rank",
                    "data": [],
                    "requested_trade_date": requested_date,
                    "as_of_trade_date": as_of_date,
                    "data_freshness": "empty",
                    "top_n": safe_top_n,
                    "include_outflow": bool(include_outflow),
                    "market_overview": {
                        "inflow_count": 0,
                        "outflow_count": 0,
                        "total_net_amount": 0.0,
                    },
                    "top_inflow": [],
                    "top_outflow": [],
                    "trend_conclusion_allowed": False,
                    "blocked_reason": "market_money_flow_empty",
                }
                await self.cache.set(cache_key, result, ttl=1800)
                return result

            name_col = "名称" if "名称" in df.columns else "行业"
            net_col = (
                "今日主力净流入-净额"
                if "今日主力净流入-净额" in df.columns
                else ("净额" if "净额" in df.columns else None)
            )
            pct_col = (
                "今日涨跌幅"
                if "今日涨跌幅" in df.columns
                else ("行业-涨跌幅" if "行业-涨跌幅" in df.columns else "涨跌幅")
            )
            if net_col is None:
                raise ValueError("No net amount column in stock_sector_fund_flow_rank")

            rows: List[Dict[str, Any]] = []
            for _, row in df.iterrows():
                name = str(row.get(name_col, "")).strip()
                if not name:
                    continue
                net_amount = self._safe_float(row.get(net_col))
                if net_amount is None:
                    continue
                pct = self._safe_float(row.get(pct_col))
                rows.append(
                    {
                        "name": name,
                        "net_mf_amount": net_amount,
                        "pct_chg": pct,
                        "trade_date": as_of_date,
                    }
                )

            inflow_rows = sorted(
                [r for r in rows if (r.get("net_mf_amount") or 0) >= 0],
                key=lambda x: x.get("net_mf_amount", 0),
                reverse=True,
            )
            outflow_rows = sorted(
                [r for r in rows if (r.get("net_mf_amount") or 0) < 0],
                key=lambda x: x.get("net_mf_amount", 0),
            )
            top_inflow = [
                {
                    "rank": idx,
                    "sector_name": r.get("name"),
                    "net_amount": r.get("net_mf_amount"),
                    "pct_chg": r.get("pct_chg"),
                    "trade_date": r.get("trade_date"),
                }
                for idx, r in enumerate(inflow_rows[:safe_top_n], start=1)
            ]
            top_outflow = (
                [
                    {
                        "rank": idx,
                        "sector_name": r.get("name"),
                        "net_amount": r.get("net_mf_amount"),
                        "pct_chg": r.get("pct_chg"),
                        "trade_date": r.get("trade_date"),
                    }
                    for idx, r in enumerate(outflow_rows[:safe_top_n], start=1)
                ]
                if include_outflow
                else []
            )

            total_net_amount = sum(float(r.get("net_mf_amount", 0)) for r in rows)
            trend_conclusion_allowed = data_freshness == "exact" and len(top_inflow) > 0
            blocked_reason = None
            if not trend_conclusion_allowed:
                if not rows:
                    blocked_reason = "market_money_flow_empty"
                elif data_freshness != "exact":
                    blocked_reason = f"stale_data:{data_freshness}"
                else:
                    blocked_reason = "insufficient_rank_data"

            result = {
                "variant": "market_money_flow",
                "source": "akshare",
                "data_source": "stock_sector_fund_flow_rank",
                "data": rows,
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
            await self.cache.set(cache_key, result, ttl=900)
            return result
        except Exception as e:
            self.logger.warning(f"Akshare get_market_money_flow failed: {e}")
            raise ValueError(f"Failed to get market money flow: {e}")

    async def get_sector_trend(
        self,
        sector_name: str = "",
        days: int = 10,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get sector trend with fuzzy board resolution via AkShare."""
        cache_key = (
            f"akshare:sector_trend:{(sector_id or '').strip().upper()}:"
            f"{sector_name}:{days}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        query_name = (sector_name or "").strip()
        query_sector_id = (sector_id or "").strip().upper()
        candidates = None
        if query_sector_id:
            board = await self._resolve_board_by_code(query_sector_id)
            # Cross-adapter fallback: if sector_id dialect mismatches AkShare
            # (e.g. tushare 877042.TI), retry by sector_name.
            if board is None and query_name:
                board, candidates = await self._resolve_board(query_name)
        else:
            board, candidates = await self._resolve_board(query_name)

        if board is None:
            if candidates:
                return {
                    "error": f"板块名称 '{query_name}' 不明确，请从以下候选中选择",
                    "candidates": candidates,
                    "sector_name": query_name,
                    "sector_id": query_sector_id,
                    "variant": "sector_trend",
                    "source": "akshare",
                }
            if query_sector_id:
                raise ValueError(f"No sector index found for id '{query_sector_id}'")
            raise ValueError(f"No sector index found for '{query_name}'")

        try:
            hist_df = await self._fetch_board_hist(board, days)
            if hist_df is None or hist_df.empty:
                raise ValueError(f"No sector daily data for {board['name']}")

            hist_df = hist_df.sort_values("日期").tail(days)
            trend: List[Dict[str, Any]] = []
            total_pct_chg = 0.0
            for _, row in hist_df.iterrows():
                pct = self._safe_float(row.get("涨跌幅")) or 0.0
                total_pct_chg += pct
                trend.append(
                    {
                        "ts_code": board.get("code", ""),
                        "trade_date": self._to_trade_date_yyyymmdd(row.get("日期")),
                        "open": self._safe_float(row.get("开盘")),
                        "high": self._safe_float(row.get("最高")),
                        "low": self._safe_float(row.get("最低")),
                        "close": self._safe_float(row.get("收盘")),
                        "change": self._safe_float(row.get("涨跌额")),
                        "pct_chg": pct,
                        "vol": self._safe_float(row.get("成交量")),
                        "turnover_rate": self._safe_float(row.get("换手率")),
                    }
                )

            result = {
                "variant": "sector_trend",
                "source": "akshare",
                "sector_name": board["name"],
                "index_code": board.get("code", ""),
                "days": len(trend),
                "total_pct_chg": round(total_pct_chg, 4),
                "trend": trend,
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.warning(
                f"Akshare get_sector_trend failed for {query_name or query_sector_id}: {e}"
            )
            raise ValueError(f"Failed to get sector trend: {e}")

    async def get_sector_money_flow_history(
        self,
        sector_name: str = "",
        days: int = 20,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get sector price + capital flow history via AkShare."""
        cache_key = (
            f"akshare:sector_money_flow:{(sector_id or '').strip().upper()}:"
            f"{sector_name}:{days}"
        )
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        query_name = (sector_name or "").strip()
        query_sector_id = (sector_id or "").strip().upper()
        candidates = None
        if query_sector_id:
            board = await self._resolve_board_by_code(query_sector_id)
            # Cross-adapter fallback: if sector_id dialect mismatches AkShare
            # (e.g. tushare 877042.TI), retry by sector_name.
            if board is None and query_name:
                board, candidates = await self._resolve_board(query_name)
        else:
            board, candidates = await self._resolve_board(query_name)

        if board is None:
            if candidates:
                return {
                    "error": f"板块名称 '{query_name}' 不明确，请从以下候选中选择",
                    "candidates": candidates,
                    "sector_name": query_name,
                    "sector_id": query_sector_id,
                    "variant": "sector_flow",
                    "source": "akshare",
                }
            if query_sector_id:
                return {
                    "error": f"未找到板块ID: {query_sector_id}",
                    "sector_name": query_name,
                    "sector_id": query_sector_id,
                    "variant": "sector_flow",
                    "source": "akshare",
                }
            return {
                "error": f"未找到板块: {query_name}",
                "sector_name": query_name,
                "variant": "sector_flow",
                "source": "akshare",
            }

        try:
            hist_df = await self._fetch_board_hist(board, days)
            if hist_df is None or hist_df.empty:
                raise ValueError(f"No sector daily data for {board['name']}")
            hist_df = hist_df.sort_values("日期").tail(days)

            flow_map: Dict[str, Dict[str, Any]] = {}
            flow_source = None
            try:
                flow_df = await self._run(
                    ak.stock_sector_fund_flow_hist, symbol=board["name"]
                )
                if isinstance(flow_df, pd.DataFrame) and not flow_df.empty:
                    flow_source = "stock_sector_fund_flow_hist"
                    flow_df = flow_df.sort_values("日期")
                    for _, row in flow_df.iterrows():
                        td = self._to_trade_date_yyyymmdd(row.get("日期"))
                        flow_map[td] = {
                            "main_net_inflow": self._safe_float(
                                row.get("主力净流入-净额")
                            ),
                            "net_amount_rate": self._safe_float(
                                row.get("主力净流入-净占比")
                            ),
                        }
            except Exception as flow_err:
                self.logger.debug(
                    f"Akshare sector flow history unavailable for {board['name']}: {flow_err}"
                )

            records: List[Dict[str, Any]] = []
            for _, row in hist_df.iterrows():
                td = self._to_trade_date_yyyymmdd(row.get("日期"))
                rec = {
                    "trade_date": td,
                    "close": self._safe_float(row.get("收盘")),
                    "pct_chg": self._safe_float(row.get("涨跌幅")),
                    "vol": self._safe_float(row.get("成交量")),
                    "turnover_rate": self._safe_float(row.get("换手率")),
                }
                flow = flow_map.get(td)
                if flow and flow.get("main_net_inflow") is not None:
                    rec["main_net_inflow"] = round(float(flow["main_net_inflow"]), 2)
                    rec["retail_net_inflow"] = None
                    rec["total_net_inflow"] = round(float(flow["main_net_inflow"]), 2)
                    if flow.get("net_amount_rate") is not None:
                        rec["net_amount_rate"] = round(float(flow["net_amount_rate"]), 2)
                records.append(rec)

            has_flow = any("main_net_inflow" in r for r in records)
            total_main = sum(float(r.get("main_net_inflow", 0)) for r in records)
            total_pct_chg = sum(float(r.get("pct_chg", 0) or 0) for r in records)
            if has_flow:
                trend = "主力资金持续流入" if total_main > 0 else "主力资金持续流出"
            else:
                trend = "仅行情数据"

            result = {
                "variant": "sector_money_flow",
                "source": "akshare",
                "sector_name": board["name"],
                "index_code": board.get("code", ""),
                "days": len(records),
                "has_money_flow": has_flow,
                "amount_unit": "unknown",
                "records": records,
                "summary": {
                    "total_pct_chg": round(total_pct_chg, 2),
                    "total_main_net": (round(total_main, 2) if has_flow else None),
                    "trend": trend,
                    "flow_source": flow_source,
                    "amount_unit": "unknown",
                },
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.warning(
                "Akshare get_sector_money_flow_history failed for "
                f"{query_name or query_sector_id}: {e}"
            )
            raise ValueError(f"Failed to get sector money flow: {e}")

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        """Get north-bound flow series via AkShare."""
        cache_key = f"akshare:hsgt:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            df = await self._run(ak.stock_hsgt_hist_em)
            if df is None or df.empty:
                return {"error": "No north bound flow data", "source": "akshare"}
            df = df.copy()
            if "日期" not in df.columns:
                raise ValueError("No 日期 column in stock_hsgt_hist_em")
            df["日期"] = df["日期"].astype(str)
            df = df.sort_values("日期").tail(days)

            dates = [str(x) for x in df["日期"].tolist()]
            total = [
                self._safe_float(v) or 0.0
                for v in df.get("当日成交净买额", pd.Series(dtype=float)).tolist()
            ]

            result = {
                "variant": "north_bound_flow",
                "source": "akshare",
                "data": {
                    "dates": dates,
                    "hk_to_sh": [0.0 for _ in dates],
                    "hk_to_sz": [0.0 for _ in dates],
                    "total": total,
                },
                "summary": {
                    "total_net": round(sum(total), 2),
                    "period_days": len(dates),
                },
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.warning(f"Akshare get_north_bound_flow failed: {e}")
            raise ValueError(f"Failed to get north bound flow: {e}")

    async def get_market_liquidity(self, days: int = 60) -> Dict[str, Any]:
        """Get market liquidity (north flow + margin) via AkShare."""
        cache_key = f"akshare:market_liquidity:{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        try:
            north_result = await self.get_north_bound_flow(days)
            north_flow: List[Dict[str, Any]] = []
            if isinstance(north_result, dict) and north_result.get("data"):
                nd = north_result["data"]
                dates = nd.get("dates") or []
                totals = nd.get("total") or []
                hk_to_sh = nd.get("hk_to_sh") or [0.0] * len(dates)
                hk_to_sz = nd.get("hk_to_sz") or [0.0] * len(dates)
                for i, d in enumerate(dates):
                    north_flow.append(
                        {
                            "trade_date": self._to_trade_date_yyyymmdd(d),
                            "hgt": hk_to_sh[i] if i < len(hk_to_sh) else 0.0,
                            "sgt": hk_to_sz[i] if i < len(hk_to_sz) else 0.0,
                            "north_money": totals[i] if i < len(totals) else 0.0,
                        }
                    )

            margin: List[Dict[str, Any]] = []
            sh_df, sz_df = await asyncio.gather(
                self._run(ak.macro_china_market_margin_sh),
                self._run(ak.macro_china_market_margin_sz),
                return_exceptions=True,
            )
            merged: Dict[str, Dict[str, float]] = {}

            def _merge_margin(df_like: Any) -> None:
                if not isinstance(df_like, pd.DataFrame) or df_like.empty:
                    return
                for _, row in df_like.iterrows():
                    td = self._to_trade_date_yyyymmdd(row.get("日期"))
                    if not td:
                        continue
                    item = merged.setdefault(
                        td, {"rzye": 0.0, "rqye": 0.0, "rzrqye": 0.0}
                    )
                    item["rzye"] += self._safe_float(row.get("融资余额")) or 0.0
                    item["rqye"] += self._safe_float(row.get("融券余额")) or 0.0
                    item["rzrqye"] += self._safe_float(row.get("融资融券余额")) or 0.0

            _merge_margin(sh_df)
            _merge_margin(sz_df)

            for td in sorted(merged.keys())[-days:]:
                v = merged[td]
                margin.append(
                    {
                        "trade_date": td,
                        "rzye": v["rzye"],
                        "rqye": v["rqye"],
                        "rzrqye": v["rzrqye"],
                    }
                )

            result = {
                "variant": "market_liquidity",
                "source": "akshare",
                "data": {
                    "north_flow": north_flow,
                    "margin": margin,
                },
            }
            await self.cache.set(cache_key, result, ttl=1800)
            return result
        except Exception as e:
            self.logger.warning(f"Akshare get_market_liquidity failed: {e}")
            raise ValueError(f"Failed to get market liquidity: {e}")

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
