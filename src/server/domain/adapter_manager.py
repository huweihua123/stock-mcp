# src/server/domain/adapter_manager.py
"""AdapterManager for coordinating multiple data source adapters.

This manager routes ticker symbols to appropriate adapters based on
capabilities, with support for caching, failover, and LLM-based fallback search.

Aligned with ValueCell's architecture.
"""

import logging
import threading
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

    def __init__(self):
        """Initialize adapter manager."""
        self.adapters: Dict[DataSource, BaseDataAdapter] = {}
        self._adapter_order: List[BaseDataAdapter] = []
        self.exchange_routing: Dict[str, List[BaseDataAdapter]] = {}
        self._ticker_cache: Dict[str, BaseDataAdapter] = {}
        self._cache_lock = threading.Lock()
        self.lock = threading.RLock()
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
                result = await getattr(adapter, method)(ticker, **kwargs)
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
        for adapter in self._adapter_order:
            try:
                result = await getattr(adapter, method)(**kwargs)
                if result is not None:
                    return result
            except NotImplementedError:
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"{adapter.source.value}.{method}() failed: {e}")

        raise ValueError(f"No adapter supports {method}: {last_error}")

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
        self, trade_date: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_market_money_flow", trade_date=trade_date
        )

    async def get_sector_trend(
        self, sector_name: str, days: int = 10
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_sector_trend", sector_name=sector_name, days=days
        )

    async def get_sector_money_flow_history(
        self, sector_name: str, days: int = 20
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_sector_money_flow_history", sector_name=sector_name, days=days
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
