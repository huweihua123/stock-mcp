# src/server/domain/adapter_manager.py
"""AdapterManager for coordinating multiple data source adapters.

This manager routes ticker symbols to appropriate adapters based on
capabilities, with support for caching, failover, and LLM-based fallback search.

Aligned with ValueCell's architecture.
"""

import logging
import threading
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
)

logger = logging.getLogger(__name__)

_SECTOR_SOFT_FALLBACK_METHODS = {
    "get_sector_trend",
    "get_sector_money_flow_history",
    "get_sector_valuation_metrics",
}


class AdapterManager:
    """Manager for coordinating multiple asset data adapters.

    Provides unified interface for:
    - Asset search
    - Real-time prices
    - Historical prices
    - Asset information
    - Batch operations

    Extension pattern (adding a new operation):
    1. Add the method to BaseDataAdapter (raise NotImplementedError)
    2. Implement in the relevant Adapter(s)
    3. Add a 2-line delegation method here using _dispatch_ticker or _dispatch_market
    4. Add a 1-line delegation in MarketGateway
    5. Add a 1-line delegation in use_cases/
    6. Create the MCP tool file and register in registry.py
    """

    def __init__(self, provider_timeout_seconds: float = 12.0):
        """Initialize adapter manager."""
        self.adapters: Dict[DataSource, BaseDataAdapter] = {}
        self._adapter_order: List[BaseDataAdapter] = []
        self.exchange_routing: Dict[str, List[BaseDataAdapter]] = {}
        self._ticker_cache: Dict[str, BaseDataAdapter] = {}
        self._cache_lock = threading.Lock()
        self.lock = threading.RLock()
        self._provider_timeout_seconds = max(float(provider_timeout_seconds), 1.0)
        logger.info("Asset adapter manager initialized")

    # =========================================================================
    # Internal routing infrastructure (do NOT duplicate below)
    # =========================================================================

    def _rebuild_routing_table(self) -> None:
        """Rebuild routing table based on registered adapters' capabilities."""
        with self.lock:
            self.exchange_routing.clear()
            for adapter in self._adapter_order:
                capabilities = adapter.get_capabilities()
                supported_exchanges = set()
                for cap in capabilities:
                    for exchange in cap.exchanges:
                        exchange_key = (
                            exchange.value
                            if isinstance(exchange, Exchange)
                            else exchange
                        )
                        supported_exchanges.add(exchange_key)
                for exchange_key in supported_exchanges:
                    if exchange_key not in self.exchange_routing:
                        self.exchange_routing[exchange_key] = []
                    self.exchange_routing[exchange_key].append(adapter)
            with self._cache_lock:
                self._ticker_cache.clear()
            logger.debug(
                f"Routing table rebuilt with {len(self.exchange_routing)} exchanges"
            )

    def register_adapter(self, adapter: BaseDataAdapter) -> None:
        """Register a data adapter and rebuild routing table."""
        with self.lock:
            if adapter.source in self.adapters:
                logger.info(
                    f"Adapter already registered: {adapter.source.value}, skipping duplicate"
                )
                return
            self.adapters[adapter.source] = adapter
            self._adapter_order.append(adapter)
            self._rebuild_routing_table()
            logger.info(f"Registered adapter: {adapter.source.value}")

    def get_available_adapters(self) -> List[DataSource]:
        return list(self.adapters.keys())

    def get_adapter_by_provider(self, provider: str) -> Optional[BaseDataAdapter]:
        if not provider:
            return None
        try:
            ds = DataSource(provider)
        except Exception:
            ds = None
        with self.lock:
            if ds and ds in self.adapters:
                return self.adapters.get(ds)
            for key, adapter in self.adapters.items():
                if key.value == provider:
                    return adapter
        return None

    def get_adapters_for_exchange(self, exchange: str) -> List[BaseDataAdapter]:
        with self.lock:
            return self.exchange_routing.get(exchange, [])

    def get_adapters_for_asset_type(
        self, asset_type: AssetType
    ) -> List[BaseDataAdapter]:
        with self.lock:
            supporting = set()
            for adapter in self.adapters.values():
                if asset_type in adapter.get_supported_asset_types():
                    supporting.add(adapter)
            return list(supporting)

    def get_adapter_for_ticker(self, ticker: str) -> Optional[BaseDataAdapter]:
        """Get the best adapter for a specific ticker (with caching)."""
        with self._cache_lock:
            if ticker in self._ticker_cache:
                return self._ticker_cache[ticker]
        if ":" not in ticker:
            logger.warning(f"Invalid ticker format (missing ':'): {ticker}")
            return None
        exchange, _ = ticker.split(":", 1)
        adapters = self.get_adapters_for_exchange(exchange)
        if not adapters:
            logger.debug(f"No adapters registered for exchange: {exchange}")
            return None
        for adapter in adapters:
            if adapter.validate_ticker(ticker):
                with self._cache_lock:
                    self._ticker_cache[ticker] = adapter
                return adapter
        logger.warning(f"No suitable adapter found for ticker: {ticker}")
        return None

    def _get_fallbacks(
        self, ticker: str, primary: BaseDataAdapter
    ) -> List[BaseDataAdapter]:
        """Return fallback adapters for a ticker, excluding the primary."""
        if ":" not in ticker:
            return []
        exchange, _ = ticker.split(":", 1)
        return [
            a
            for a in self.get_adapters_for_exchange(exchange)
            if a is not primary and a.validate_ticker(ticker)
        ]

    async def _dispatch_ticker(self, method: str, ticker: str, **kwargs) -> Any:
        """Generic dispatcher for ticker-scoped operations with auto-failover.

        This is THE single place where failover logic lives.
        All per-ticker business methods delegate here.

        Args:
            method:  Name of the BaseDataAdapter method to call.
            ticker:  Internal ticker (e.g. "NASDAQ:AAPL").
            **kwargs: Extra keyword arguments forwarded to the adapter method.

        Raises:
            ValueError: If no adapter found or all adapters failed.
        """
        primary = self.get_adapter_for_ticker(ticker)
        if not primary:
            raise ValueError(f"No adapter found for ticker: {ticker}")

        last_error: Exception = ValueError(f"No result for {ticker}.{method}")
        for adapter in [primary] + self._get_fallbacks(ticker, primary):
            try:
                result = await asyncio.wait_for(
                    getattr(adapter, method)(ticker, **kwargs),
                    timeout=self._provider_timeout_seconds,
                )
                # For methods that return collections, treat empty as "no data"
                if result is not None:
                    # Allow empty dict/list — callers decide what to do with it
                    logger.debug(
                        f"{method}({ticker}) succeeded via {adapter.source.value}"
                    )
                    # Update ticker cache if we used a fallback
                    if adapter is not primary:
                        with self._cache_lock:
                            self._ticker_cache[ticker] = adapter
                    return result
                logger.warning(
                    f"{adapter.source.value}.{method}({ticker}) returned None, trying next"
                )
            except NotImplementedError:
                logger.debug(
                    f"{adapter.source.value} does not support {method}, skipping"
                )
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"timeout after {self._provider_timeout_seconds}s"
                )
                logger.warning(
                    f"{adapter.source.value}.{method}({ticker}) timeout in "
                    f"{self._provider_timeout_seconds}s"
                )
            except Exception as e:
                last_error = e
                logger.warning(f"{adapter.source.value}.{method}({ticker}) failed: {e}")

        raise ValueError(f"All adapters failed for {ticker}.{method}: {last_error}")

    async def _dispatch_market(self, method: str, **kwargs) -> Any:
        """Generic dispatcher for market-wide (non-ticker) operations.

        Tries adapters in registration order, skips NotImplementedError.

        Args:
            method:  Name of the BaseDataAdapter method to call.
            **kwargs: Extra keyword arguments forwarded to the adapter method.

        Raises:
            ValueError: If no adapter supports the method.
        """
        last_error: Exception = ValueError(f"No adapter supports {method}")
        last_soft_result: Any = None
        for adapter in self._adapter_order:
            try:
                adapter_kwargs = self._normalize_market_kwargs_for_adapter(
                    adapter=adapter,
                    method=method,
                    kwargs=kwargs,
                )
                result = await asyncio.wait_for(
                    getattr(adapter, method)(**adapter_kwargs),
                    timeout=self._provider_timeout_seconds,
                )
                if result is not None:
                    if self._should_soft_fallback_market_result(method, result):
                        last_soft_result = result
                        last_error = ValueError(
                            f"soft no-data result from {adapter.source.value}.{method}"
                        )
                        logger.info(
                            "soft no-data result, trying next adapter",
                            extra={
                                "method": method,
                                "adapter": adapter.source.value,
                            },
                        )
                        continue
                    return result
            except NotImplementedError:
                continue
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"timeout after {self._provider_timeout_seconds}s"
                )
                logger.warning(
                    f"{adapter.source.value}.{method}() timeout in "
                    f"{self._provider_timeout_seconds}s"
                )
            except Exception as e:
                last_error = e
                logger.warning(f"{adapter.source.value}.{method}() failed: {e}")

        if last_soft_result is not None:
            return last_soft_result
        raise ValueError(f"No adapter supports {method}: {last_error}")

    def _normalize_market_kwargs_for_adapter(
        self,
        *,
        adapter: BaseDataAdapter,
        method: str,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        if method not in _SECTOR_SOFT_FALLBACK_METHODS:
            return kwargs
        if not isinstance(kwargs, dict):
            return kwargs

        normalized = dict(kwargs)
        sector_id = str(normalized.get("sector_id") or "").strip().upper()
        if not sector_id:
            return normalized

        if adapter.source == DataSource.TUSHARE:
            # Tushare sector ids should be ths_index ts_code (e.g. 877042.TI).
            if not sector_id.endswith(".TI"):
                normalized["sector_id"] = None
            return normalized

        if adapter.source == DataSource.AKSHARE:
            # AkShare board ids use BK**** style codes.
            if not sector_id.startswith("BK"):
                normalized["sector_id"] = None
            return normalized

        return normalized

    async def _canonicalize_resolved_sector(
        self,
        result: Dict[str, Any],
        *,
        intent: str,
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return result
        if str(result.get("status", "")).lower() != "resolved":
            return result

        canonical: Dict[str, Any] = dict(result)
        provider_sector_id = str(canonical.get("sector_id") or "").strip().upper()
        if not provider_sector_id:
            return canonical

        canonical.setdefault("provider_sector_id", provider_sector_id)
        canonical.setdefault("canonical_sector_id", provider_sector_id)
        canonical.setdefault(
            "canonical_source",
            str(canonical.get("source") or "").strip().lower() or "unknown",
        )
        if provider_sector_id.endswith(".TI"):
            canonical["canonical_source"] = "tushare"
            return canonical

        tushare = self.adapters.get(DataSource.TUSHARE)
        if not tushare:
            return canonical

        query_text = str(
            canonical.get("canonical_name")
            or canonical.get("query_text")
            or ""
        ).strip()
        if not query_text:
            return canonical

        try:
            ts_result = await asyncio.wait_for(
                tushare.resolve_sector(query_text=query_text, intent=intent),
                timeout=self._provider_timeout_seconds,
            )
            if not isinstance(ts_result, dict):
                return canonical
            if str(ts_result.get("status", "")).lower() != "resolved":
                return canonical
            ts_sector_id = str(ts_result.get("sector_id") or "").strip().upper()
            if not ts_sector_id:
                return canonical
            canonical["sector_id"] = ts_sector_id
            canonical["canonical_sector_id"] = ts_sector_id
            canonical["canonical_source"] = "tushare"
            ts_name = str(ts_result.get("canonical_name") or "").strip()
            if ts_name:
                canonical["canonical_name"] = ts_name
            return canonical
        except Exception:
            return canonical

    @staticmethod
    def _should_soft_fallback_market_result(method: str, result: Any) -> bool:
        if method not in _SECTOR_SOFT_FALLBACK_METHODS:
            return False
        if not isinstance(result, dict):
            return False

        if str(result.get("error") or "").strip():
            return True

        candidates = result.get("candidates")
        if isinstance(candidates, list) and len(candidates) > 0:
            return True

        if method == "get_sector_trend":
            trend = result.get("trend")
            return isinstance(trend, list) and len(trend) == 0

        if method == "get_sector_money_flow_history":
            records = result.get("records")
            return isinstance(records, list) and len(records) == 0

        if method == "get_sector_valuation_metrics":
            history = result.get("history")
            return isinstance(history, list) and len(history) == 0

        return False

    # =========================================================================
    # Core price / asset operations  (use dedicated implementations for perf)
    # =========================================================================

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        try:
            return await self._dispatch_ticker("get_asset_info", ticker)
        except ValueError:
            return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        try:
            return await self._dispatch_ticker("get_real_time_price", ticker)
        except ValueError:
            return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        try:
            result = await self._dispatch_ticker(
                "get_historical_prices",
                ticker,
                start_date=start_date,
                end_date=end_date,
                interval=interval,
            )
            return result or []
        except ValueError:
            return []

    async def get_multiple_prices(
        self, tickers: List[str]
    ) -> Dict[str, Optional[AssetPrice]]:
        import asyncio

        tasks = {t: self.get_real_time_price(t) for t in tickers}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            ticker: (None if isinstance(r, Exception) else r)
            for ticker, r in zip(tasks.keys(), results)
        }

    # =========================================================================
    # Ticker-scoped business operations — each is a 2-liner via _dispatch_ticker
    # =========================================================================

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_financials", ticker)

    async def get_dividend_info(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_dividend_info", ticker)

    async def get_forecast_info(
        self, ticker: str, limit: int = 50
    ) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_forecast_info", ticker, limit=limit)

    async def get_mainbz_info(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_mainbz_info", ticker)

    async def get_shareholder_info(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_shareholder_info", ticker)

    async def get_valuation_metrics(
        self, ticker: str, days: int = 250
    ) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_valuation_metrics", ticker, days=days)

    async def get_money_flow(self, ticker: str, days: int = 20) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_money_flow", ticker, days=days)

    async def get_chip_distribution(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_chip_distribution", ticker, days=days)

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict]:
        return await self._dispatch_ticker(
            "get_filings",
            ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            filing_types=filing_types,
        )

    async def get_technical_indicators(
        self,
        ticker: str,
        indicators: List[str],
        period: str = "daily",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        return await self._dispatch_ticker(
            "get_technical_indicators",
            ticker,
            indicators=indicators,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

    # --- NEW: US-market specific ticker operations ---

    async def get_earnings_history(
        self, ticker: str, quarters: int = 8
    ) -> Dict[str, Any]:
        """Fetch EPS history with estimate vs actual and surprise %."""
        return await self._dispatch_ticker(
            "get_earnings_history", ticker, quarters=quarters
        )

    async def get_cash_flow_quality(self, ticker: str) -> Dict[str, Any]:
        """Fetch operating/free cash flow and FCF/net-income ratio."""
        return await self._dispatch_ticker("get_cash_flow_quality", ticker)

    async def get_us_valuation_metrics(self, ticker: str) -> Dict[str, Any]:
        """Fetch US stock PE/PS/PB/EV_EBITDA with historical percentile."""
        return await self._dispatch_ticker("get_us_valuation_metrics", ticker)

    async def get_us_institutional_holdings(self, ticker: str) -> Dict[str, Any]:
        """Fetch top institutional holders and recent change direction."""
        return await self._dispatch_ticker("get_us_institutional_holdings", ticker)

    async def get_us_price_history(
        self, ticker: str, days: int = 60, interval: str = "1d"
    ) -> Dict[str, Any]:
        """Fetch OHLCV klines for US stock."""
        return await self._dispatch_ticker(
            "get_us_price_history", ticker, days=days, interval=interval
        )

    async def get_us_volume_analysis(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch volume metrics: avg volume, RVol, OBV trend."""
        return await self._dispatch_ticker("get_us_volume_analysis", ticker, days=days)

    async def get_us_sector_etf_analysis(
        self, sector_name: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch US sector ETF klines by sector name."""
        return await self._dispatch_market(
            "get_us_sector_etf_analysis", sector_name=sector_name, days=days
        )

    async def get_us_economic_growth(self, quarters: int = 20) -> Dict[str, Any]:
        """Fetch US real GDP levels and growth rates."""
        return await self._dispatch_market(
            "get_us_economic_growth",
            quarters=quarters,
        )

    async def get_us_inflation_employment(self, months: int = 24) -> Dict[str, Any]:
        """Fetch US inflation (CPI YoY) and unemployment rate."""
        return await self._dispatch_market(
            "get_us_inflation_employment",
            months=months,
        )

    async def get_us_interest_rates(self, days: int = 180) -> Dict[str, Any]:
        """Fetch US 2Y/10Y/Fed Funds rates and curve spread."""
        return await self._dispatch_market(
            "get_us_interest_rates",
            days=days,
        )

    # =========================================================================
    # Market-wide operations — each is a 2-liner via _dispatch_market
    # =========================================================================

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        # North-bound data is China-specific; prefer Tushare
        if DataSource.TUSHARE in self.adapters:
            try:
                return await self.adapters[DataSource.TUSHARE].get_north_bound_flow(
                    days
                )
            except Exception as e:
                logger.warning(f"Tushare failed for north_bound_flow: {e}")
        return await self._dispatch_market("get_north_bound_flow", days=days)

    async def get_money_supply(self, months: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_money_supply", months=months)

    async def get_inflation_data(self, months: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_inflation_data", months=months)

    async def get_pmi_data(self, months: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_pmi_data", months=months)

    async def get_gdp_data(self, quarters: int = 20) -> Dict[str, Any]:
        return await self._dispatch_market("get_gdp_data", quarters=quarters)

    async def get_social_financing(self, months: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_social_financing", months=months)

    async def get_interest_rates(
        self, shibor_days: int = 252, lpr_months: int = 60
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_interest_rates", shibor_days=shibor_days, lpr_months=lpr_months
        )

    async def get_market_liquidity(self, days: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_market_liquidity", days=days)

    async def get_market_money_flow(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        include_outflow: bool = True,
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_market_money_flow",
            trade_date=trade_date,
            top_n=top_n,
            include_outflow=include_outflow,
        )

    async def get_sector_trend(
        self,
        sector_name: str = "",
        days: int = 10,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_sector_trend",
            sector_name=sector_name,
            days=days,
            sector_id=sector_id,
        )

    async def resolve_sector(
        self, query_text: str, intent: str = "trend"
    ) -> Dict[str, Any]:
        """Resolve sector with cross-adapter fallback.

        Priority policy for CN sector id dialect consistency:
        1. Tushare resolved (.TI canonical)
        2. Tushare ambiguous (let LLM choose candidate)
        3. Other adapters resolved
        4. First ambiguous / last_not_found fallback
        """
        last_not_found: Optional[Dict[str, Any]] = None
        first_ambiguous: Optional[Dict[str, Any]] = None
        first_non_tushare_resolved: Optional[Dict[str, Any]] = None
        tushare_ambiguous: Optional[Dict[str, Any]] = None
        last_error: Optional[Exception] = None
        for adapter in self._adapter_order:
            try:
                result = await asyncio.wait_for(
                    adapter.resolve_sector(query_text=query_text, intent=intent),
                    timeout=self._provider_timeout_seconds,
                )
                if not isinstance(result, dict):
                    continue
                status = str(result.get("status", "")).lower()
                if status == "resolved":
                    canonical_resolved = await self._canonicalize_resolved_sector(
                        result,
                        intent=intent,
                    )
                    if adapter.source == DataSource.TUSHARE:
                        return canonical_resolved
                    if first_non_tushare_resolved is None:
                        first_non_tushare_resolved = canonical_resolved
                    continue
                if status == "ambiguous":
                    if first_ambiguous is None:
                        first_ambiguous = result
                    if adapter.source == DataSource.TUSHARE:
                        tushare_ambiguous = result
                    continue
                if status == "not_found":
                    last_not_found = result
                    continue
                return result
            except NotImplementedError:
                continue
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"timeout after {self._provider_timeout_seconds}s"
                )
                logger.warning(
                    f"{adapter.source.value}.resolve_sector() timeout in "
                    f"{self._provider_timeout_seconds}s"
                )
            except Exception as e:
                last_error = e
                logger.warning(f"{adapter.source.value}.resolve_sector() failed: {e}")

        if tushare_ambiguous is not None:
            return tushare_ambiguous
        if first_non_tushare_resolved is not None:
            return first_non_tushare_resolved
        if first_ambiguous is not None:
            return first_ambiguous
        if last_not_found is not None:
            return last_not_found
        raise ValueError(f"No adapter supports resolve_sector: {last_error}")

    async def get_sector_money_flow_history(
        self,
        sector_name: str = "",
        days: int = 20,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_sector_money_flow_history",
            sector_name=sector_name,
            days=days,
            sector_id=sector_id,
        )

    async def get_sector_valuation_metrics(
        self,
        sector_name: str = "",
        days: int = 250,
        sample_size: int = 60,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_sector_valuation_metrics",
            sector_name=sector_name,
            days=days,
            sample_size=sample_size,
            sector_id=sector_id,
        )

    async def get_ggt_daily(self, days: int = 60) -> Dict[str, Any]:
        return await self._dispatch_market("get_ggt_daily", days=days)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_adapter_manager_instance: Optional["AdapterManager"] = None
_adapter_manager_lock = threading.Lock()


def get_adapter_manager() -> AdapterManager:
    global _adapter_manager_instance
    if _adapter_manager_instance is None:
        with _adapter_manager_lock:
            if _adapter_manager_instance is None:
                _adapter_manager_instance = AdapterManager()
    return _adapter_manager_instance
