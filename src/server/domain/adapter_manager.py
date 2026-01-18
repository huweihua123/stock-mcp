# src/server/domain/adapter_manager.py
"""AdapterManager for coordinating multiple data source adapters.

This manager routes ticker symbols to appropriate adapters based on
capabilities, with support for caching, failover, and LLM-based fallback search.

Aligned with ValueCell's architecture.
"""

import json
import logging
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    Asset,
    AssetPrice,
    AssetSearchQuery,
    AssetSearchResult,
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
    """

    def __init__(self):
        """Initialize adapter manager."""
        self.adapters: Dict[DataSource, BaseDataAdapter] = {}

        # Maintain registration order for priority routing
        self._adapter_order: List[BaseDataAdapter] = []

        # Exchange → Adapters routing table (maintains registration order)
        self.exchange_routing: Dict[str, List[BaseDataAdapter]] = {}

        # Ticker → Adapter cache for fast lookups
        self._ticker_cache: Dict[str, BaseDataAdapter] = {}
        self._cache_lock = threading.Lock()

        self.lock = threading.RLock()

        logger.info("Asset adapter manager initialized")

    def _rebuild_routing_table(self) -> None:
        """Rebuild routing table based on registered adapters' capabilities.

        Adapters are processed in registration order to maintain priority.
        """
        with self.lock:
            self.exchange_routing.clear()

            # Use _adapter_order to maintain registration order
            for adapter in self._adapter_order:
                capabilities = adapter.get_capabilities()

                # Get all exchanges supported by this adapter
                supported_exchanges = set()
                for cap in capabilities:
                    for exchange in cap.exchanges:
                        exchange_key = (
                            exchange.value
                            if isinstance(exchange, Exchange)
                            else exchange
                        )
                        supported_exchanges.add(exchange_key)

                # Register adapter for each supported exchange
                for exchange_key in supported_exchanges:
                    if exchange_key not in self.exchange_routing:
                        self.exchange_routing[exchange_key] = []
                    self.exchange_routing[exchange_key].append(adapter)

            # Clear ticker cache when routing table changes
            with self._cache_lock:
                self._ticker_cache.clear()

            logger.debug(
                f"Routing table rebuilt with {len(self.exchange_routing)} exchanges"
            )

    def register_adapter(self, adapter: BaseDataAdapter) -> None:
        """Register a data adapter and rebuild routing table.

        Adapters are prioritized in registration order. For exchanges with
        multiple adapters, the first registered adapter will be tried first.

        Args:
            adapter: Data adapter instance to register
        """
        with self.lock:
            self.adapters[adapter.source] = adapter
            self._adapter_order.append(adapter)
            self._rebuild_routing_table()
            logger.info(f"Registered adapter: {adapter.source.value}")

    def get_available_adapters(self) -> List[DataSource]:
        """Get list of available data adapters."""
        with self.lock:
            return list(self.adapters.keys())

    def get_adapters_for_exchange(self, exchange: str) -> List[BaseDataAdapter]:
        """Get list of adapters for a specific exchange.

        Args:
            exchange: Exchange identifier (e.g., "NASDAQ", "SSE")

        Returns:
            List of adapters that support the exchange
        """
        with self.lock:
            return self.exchange_routing.get(exchange, [])

    def get_adapters_for_asset_type(
        self, asset_type: AssetType
    ) -> List[BaseDataAdapter]:
        """Get list of adapters that support a specific asset type.

        Args:
            asset_type: Type of asset

        Returns:
            List of adapters that support this asset type
        """
        with self.lock:
            supporting_adapters = set()
            for adapter in self.adapters.values():
                supported_types = adapter.get_supported_asset_types()
                if asset_type in supported_types:
                    supporting_adapters.add(adapter)

            return list(supporting_adapters)

    def get_adapter_for_ticker(self, ticker: str) -> Optional[BaseDataAdapter]:
        """Get the best adapter for a specific ticker (with caching).

        Args:
            ticker: Asset ticker in internal format (e.g., "NASDAQ:AAPL")

        Returns:
            Best available adapter for the ticker or None if not found
        """
        # Check cache first
        with self._cache_lock:
            if ticker in self._ticker_cache:
                return self._ticker_cache[ticker]

        # Parse ticker
        if ":" not in ticker:
            logger.warning(f"Invalid ticker format (missing ':'): {ticker}")
            return None

        exchange, symbol = ticker.split(":", 1)

        # Get adapters for this exchange
        adapters = self.get_adapters_for_exchange(exchange)

        if not adapters:
            logger.debug(f"No adapters registered for exchange: {exchange}")
            return None

        # Find first adapter that validates this ticker
        for adapter in adapters:
            if adapter.validate_ticker(ticker):
                # Cache the result
                with self._cache_lock:
                    self._ticker_cache[ticker] = adapter
                logger.debug(f"Matched adapter {adapter.source.value} for {ticker}")
                return adapter

        logger.warning(f"No suitable adapter found for ticker: {ticker}")
        return None

    def _deduplicate_search_results(
        self, results: List[AssetSearchResult]
    ) -> List[AssetSearchResult]:
        """Smart deduplication of search results to handle cross-exchange duplicates.

        This method handles cases where the same asset appears on multiple exchanges
        (e.g., AMEX:GORO vs NASDAQ:GORO). It prioritizes certain exchanges and removes
        likely duplicates based on symbol matching.

        Args:
            results: List of search results to deduplicate

        Returns:
            Deduplicated list of search results
        """
        # Exchange priority for US stocks (higher number = higher priority)
        exchange_priority = {
            "NASDAQ": 3,
            "NYSE": 2,
            "AMEX": 1,
            "HKEX": 3,
            "SSE": 2,
            "SZSE": 2,
            "BSE": 1,
        }

        seen_tickers = set()
        # Map: (symbol, country) -> best result so far
        symbol_map: Dict[tuple, AssetSearchResult] = {}

        for result in results:
            # Skip exact ticker duplicates
            if result.ticker in seen_tickers:
                continue

            try:
                exchange, symbol = result.ticker.split(":", 1)
            except ValueError:
                logger.warning(
                    f"Invalid ticker format in search result: {result.ticker}"
                )
                continue

            # Create a key for cross-exchange deduplication
            dedup_key = (symbol.upper(), result.country)

            # Check if we've seen this symbol in the same country before
            if dedup_key in symbol_map:
                existing_result = symbol_map[dedup_key]
                existing_exchange = existing_result.ticker.split(":")[0]

                # Compare exchange priorities
                current_priority = exchange_priority.get(exchange, 0)
                existing_priority = exchange_priority.get(existing_exchange, 0)

                if current_priority > existing_priority:
                    symbol_map[dedup_key] = result
                    logger.debug(
                        f"Preferring {result.ticker} over {existing_result.ticker} (priority)"
                    )
                elif current_priority == existing_priority:
                    # Same priority, prefer higher relevance score
                    if result.relevance_score > existing_result.relevance_score:
                        symbol_map[dedup_key] = result
                        logger.debug(
                            f"Preferring {result.ticker} over {existing_result.ticker} (relevance)"
                        )
            else:
                symbol_map[dedup_key] = result

            seen_tickers.add(result.ticker)

        # Convert map back to list
        unique_results = list(symbol_map.values())

        # Sort by relevance score (descending)
        unique_results.sort(key=lambda x: x.relevance_score, reverse=True)

        logger.info(
            f"Deduplicated {len(results)} results to {len(unique_results)} unique assets"
        )

        return unique_results

    async def search_assets(self, query: AssetSearchQuery) -> List[AssetSearchResult]:
        """Search for assets across all available adapters.

        Args:
            query: Search query parameters

        Returns:
            Combined and deduplicated search results
        """
        all_results = []

        # Use all available adapters
        with self.lock:
            target_adapters = list(self.adapters.values())

        if not target_adapters:
            return []

        # Search in parallel across adapters
        tasks = [adapter.search_assets(query) for adapter in target_adapters]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results_list):
            adapter = target_adapters[i]
            if isinstance(result, Exception):
                logger.warning(
                    f"Search failed for adapter {adapter.source.value}: {result}"
                )
            else:
                all_results.extend(result)

        # Smart deduplication of results
        unique_results = self._deduplicate_search_results(all_results)

        # TODO: Add LLM fallback search if no results found
        # if len(unique_results) == 0:
        #     fallback_results = self._fallback_search_assets(query)
        #     unique_results = self._deduplicate_search_results(fallback_results)

        return unique_results[: query.limit]

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Get detailed asset information with automatic failover.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Asset information or None if not found
        """
        adapter = self.get_adapter_for_ticker(ticker)

        if not adapter:
            logger.warning(f"No suitable adapter found for ticker: {ticker}")
            return None

        # Try the primary adapter
        try:
            logger.debug(
                f"Fetching asset info for {ticker} from {adapter.source.value}"
            )
            asset_info = await adapter.get_asset_info(ticker)
            if asset_info:
                logger.info(
                    f"Successfully fetched asset info for {ticker} from {adapter.source.value}"
                )
                return asset_info
        except Exception as e:
            logger.warning(
                f"Primary adapter {adapter.source.value} failed for {ticker}: {e}"
            )

        # Automatic failover: try other adapters for this exchange
        exchange = ticker.split(":")[0] if ":" in ticker else ""
        fallback_adapters = self.get_adapters_for_exchange(exchange)

        for fallback_adapter in fallback_adapters:
            if fallback_adapter.source == adapter.source:
                continue

            if not fallback_adapter.validate_ticker(ticker):
                continue

            try:
                logger.debug(
                    f"Fallback: trying {fallback_adapter.source.value} for {ticker}"
                )
                asset_info = await fallback_adapter.get_asset_info(ticker)
                if asset_info:
                    logger.info(
                        f"Fallback success: fetched asset info for {ticker} from {fallback_adapter.source.value}"
                    )
                    # Update cache to use successful adapter
                    with self._cache_lock:
                        self._ticker_cache[ticker] = fallback_adapter
                    return asset_info
            except Exception as e:
                logger.warning(
                    f"Fallback adapter {fallback_adapter.source.value} failed for {ticker}: {e}"
                )
                continue

        logger.error(f"All adapters failed for {ticker}")
        return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Get real-time price for an asset with automatic failover.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Current price data or None if not available
        """
        adapter = self.get_adapter_for_ticker(ticker)

        if not adapter:
            logger.warning(f"No suitable adapter found for ticker: {ticker}")
            return None

        # Try the primary adapter
        try:
            logger.debug(f"Fetching price for {ticker} from {adapter.source.value}")
            price = await adapter.get_real_time_price(ticker)
            if price:
                logger.info(
                    f"Successfully fetched price for {ticker} from {adapter.source.value}"
                )
                return price
        except Exception as e:
            logger.warning(
                f"Primary adapter {adapter.source.value} failed for {ticker}: {e}"
            )

        # Automatic failover
        exchange = ticker.split(":")[0] if ":" in ticker else ""
        fallback_adapters = self.get_adapters_for_exchange(exchange)

        for fallback_adapter in fallback_adapters:
            if fallback_adapter.source == adapter.source:
                continue

            if not fallback_adapter.validate_ticker(ticker):
                continue

            try:
                logger.debug(
                    f"Fallback: trying {fallback_adapter.source.value} for {ticker}"
                )
                price = await fallback_adapter.get_real_time_price(ticker)
                if price:
                    logger.info(
                        f"Fallback success: fetched price for {ticker} from {fallback_adapter.source.value}"
                    )
                    with self._cache_lock:
                        self._ticker_cache[ticker] = fallback_adapter
                    return price
            except Exception as e:
                logger.warning(
                    f"Fallback adapter {fallback_adapter.source.value} failed for {ticker}: {e}"
                )
                continue

        logger.error(f"All adapters failed for {ticker}")
        return None

    async def get_multiple_prices(
        self, tickers: List[str]
    ) -> Dict[str, Optional[AssetPrice]]:
        """Get real-time prices for multiple assets efficiently with automatic failover.

        Args:
            tickers: List of asset tickers

        Returns:
            Dictionary mapping tickers to price data
        """
        # Group tickers by adapter
        adapter_tickers: Dict[BaseDataAdapter, List[str]] = {}

        for ticker in tickers:
            adapter = self.get_adapter_for_ticker(ticker)
            if adapter:
                if adapter not in adapter_tickers:
                    adapter_tickers[adapter] = []
                adapter_tickers[adapter].append(ticker)

        # Fetch prices in parallel from each adapter
        all_results = {}
        failed_tickers = []

        if not adapter_tickers:
            return {ticker: None for ticker in tickers}

        tasks = []
        adapters_list = []

        for adapter, ticker_list in adapter_tickers.items():
            tasks.append(adapter.get_multiple_prices(ticker_list))
            adapters_list.append((adapter, ticker_list))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results_list):
            adapter, ticker_list = adapters_list[i]
            if isinstance(result, Exception):
                logger.warning(
                    f"Batch price fetch failed for adapter {adapter.source.value}: {result}"
                )
                failed_tickers.extend(ticker_list)
            else:
                # result is Dict[str, Optional[AssetPrice]]
                for ticker, price in result.items():
                    if price is not None:
                        all_results[ticker] = price
                    else:
                        failed_tickers.append(ticker)

        # Retry failed tickers individually with fallback adapters
        if failed_tickers:
            logger.info(
                f"Retrying {len(failed_tickers)} failed tickers with fallback adapters"
            )
            for ticker in failed_tickers:
                if ticker not in all_results or all_results[ticker] is None:
                    price = await self.get_real_time_price(ticker)
                    all_results[ticker] = price

        # Ensure all requested tickers are in results
        for ticker in tickers:
            if ticker not in all_results:
                all_results[ticker] = None

        return all_results

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Get historical price data for an asset with automatic failover.

        Args:
            ticker: Asset ticker in internal format
            start_date: Start date for historical data
            end_date: End date for historical data
            interval: Data interval

        Returns:
            List of historical price data
        """
        adapter = self.get_adapter_for_ticker(ticker)

        if not adapter:
            logger.warning(f"No suitable adapter found for ticker: {ticker}")
            return []

        # Try the primary adapter
        try:
            logger.debug(
                f"Fetching historical data for {ticker} from {adapter.source.value}"
            )
            prices = await adapter.get_historical_prices(
                ticker, start_date, end_date, interval
            )
            if prices:
                logger.info(
                    f"Successfully fetched {len(prices)} historical prices for {ticker} from {adapter.source.value}"
                )
                return prices
        except Exception as e:
            logger.warning(
                f"Primary adapter {adapter.source.value} failed for historical data of {ticker}: {e}"
            )

        # Automatic failover
        exchange = ticker.split(":")[0] if ":" in ticker else ""
        fallback_adapters = self.get_adapters_for_exchange(exchange)

        for fallback_adapter in fallback_adapters:
            if fallback_adapter.source == adapter.source:
                continue

            if not fallback_adapter.validate_ticker(ticker):
                continue

            try:
                logger.info(
                    f"Fallback: trying {fallback_adapter.source.value} for historical data of {ticker}"
                )
                prices = await fallback_adapter.get_historical_prices(
                    ticker, start_date, end_date, interval
                )
                if prices:
                    logger.info(
                        f"Fallback success: fetched {len(prices)} historical prices for {ticker} from {fallback_adapter.source.value}"
                    )
                    with self._cache_lock:
                        self._ticker_cache[ticker] = fallback_adapter
                    return prices
                else:
                    logger.warning(
                        f"Fallback adapter {fallback_adapter.source.value} returned empty data for {ticker}"
                    )
            except Exception as e:
                logger.warning(
                    f"Fallback adapter {fallback_adapter.source.value} failed for historical data of {ticker}: {e}"
                )
                continue

        logger.error(f"All adapters failed for historical data of {ticker}")
        return []

    async def get_financials(self, ticker: str) -> Dict:
        """Fetch financial/fundamental data for a ticker.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Dictionary containing financial statements and metrics

        Raises:
            ValueError: If no adapter found or all adapters failed
        """
        adapter = self.get_adapter_for_ticker(ticker)
        if not adapter:
            raise ValueError(f"No adapter found for ticker {ticker}")

        try:
            return await adapter.get_financials(ticker)
        except Exception as e:
            if isinstance(e, NotImplementedError):
                logger.warning(
                    f"Adapter {adapter.source.value} does not support financials"
                )
            else:
                logger.warning(f"Adapter {adapter.source.value} failed: {e}")

            # Try failover
            if ":" in ticker:
                exchange, _ = ticker.split(":", 1)
                adapters = self.get_adapters_for_exchange(exchange)

                for alt in adapters:
                    if alt is adapter:
                        continue
                    try:
                        logger.info(
                            f"Trying failover adapter {alt.source.value} for financials of {ticker}"
                        )
                        return await alt.get_financials(ticker)
                    except Exception as failover_error:
                        logger.warning(
                            f"Failover adapter {alt.source.value} also failed: {failover_error}"
                        )
                        continue

            raise ValueError(
                f"All adapters failed to fetch financials for {ticker}: {e}"
            )

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Fetch regulatory filings/announcements.

        Args:
            ticker: Asset ticker
            start_date: Start date
            end_date: End date
            limit: Max results
            filing_types: List of filing types to filter

        Returns:
            List of filings
        """
        adapter = self.get_adapter_for_ticker(ticker)
        if not adapter:
            raise ValueError(f"No adapter found for ticker {ticker}")

        try:
            result = await adapter.get_filings(
                ticker, start_date, end_date, limit, filing_types
            )
            return result
        except Exception as e:
            if isinstance(e, NotImplementedError):
                logger.warning(
                    f"Adapter {adapter.source.value} does not " "support filings"
                )
            else:
                logger.warning(f"Adapter {adapter.source.value} failed: {e}")

            # Try failover
            if ":" in ticker:
                exchange, _ = ticker.split(":", 1)
                adapters = self.get_adapters_for_exchange(exchange)

                for alt in adapters:
                    if alt is adapter:
                        continue
                    try:
                        logger.info(
                            f"Trying failover adapter {alt.source.value} "
                            f"for filings of {ticker}"
                        )
                        result = await alt.get_filings(
                            ticker, start_date, end_date, limit, filing_types
                        )
                        return result
                    except Exception as failover_error:
                        logger.warning(
                            f"Failover adapter {alt.source.value} "
                            f"also failed: {failover_error}"
                        )
                        continue

            raise ValueError(f"All adapters failed to fetch filings for {ticker}: {e}")

    async def get_money_flow(self, ticker: str, days: int = 20) -> Dict[str, Any]:
        """获取个股资金流向数据 (带自动降级)

        Args:
            ticker: 股票代码 (内部格式 SSE:600519)
            days: 获取最近 N 天数据

        Returns:
            包含资金流向的结构化数据
        """
        adapter = self.get_adapter_for_ticker(ticker)
        if not adapter:
            raise ValueError(f"No adapter found for ticker {ticker}")

        try:
            return await adapter.get_money_flow(ticker, days)
        except NotImplementedError:
            logger.warning(
                f"Adapter {adapter.source.value} does not support money flow"
            )
        except Exception as e:
            logger.warning(f"Adapter {adapter.source.value} failed for money flow: {e}")

        # Try failover
        if ":" in ticker:
            exchange, _ = ticker.split(":", 1)
            adapters = self.get_adapters_for_exchange(exchange)

            for alt in adapters:
                if alt is adapter:
                    continue
                try:
                    logger.info(
                        f"Trying failover adapter {alt.source.value} "
                        f"for money flow of {ticker}"
                    )
                    return await alt.get_money_flow(ticker, days)
                except Exception as failover_error:
                    logger.warning(
                        f"Failover adapter {alt.source.value} "
                        f"also failed: {failover_error}"
                    )
                    continue

        raise ValueError(f"All adapters failed to fetch money flow for {ticker}")

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        """获取北向资金流向数据 (沪深港通)

        Args:
            days: 获取最近 N 天数据

        Returns:
            包含北向资金数据的结构化数据
        """
        # 北向资金是全市场数据，优先使用 Tushare
        from src.server.domain.types import DataSource

        if DataSource.TUSHARE in self.adapters:
            adapter = self.adapters[DataSource.TUSHARE]
            try:
                return await adapter.get_north_bound_flow(days)
            except Exception as e:
                logger.warning(f"Tushare failed for north bound flow: {e}")

        # Try other adapters
        for adapter in self._adapter_order:
            try:
                return await adapter.get_north_bound_flow(days)
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning(
                    f"Adapter {adapter.source.value} failed for north bound: {e}"
                )
                continue

        raise ValueError("No adapter supports north bound flow data")

    async def get_chip_distribution(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """获取筹码分布数据 (带自动降级)

        Args:
            ticker: 股票代码 (内部格式)
            days: 获取最近 N 天数据

        Returns:
            包含筹码分布数据的字典
        """
        adapter = self.get_adapter_for_ticker(ticker)
        if not adapter:
            raise ValueError(f"No adapter found for ticker {ticker}")

        try:
            return await adapter.get_chip_distribution(ticker, days)
        except NotImplementedError:
            logger.warning(
                f"Adapter {adapter.source.value} does not support chip distribution"
            )
        except Exception as e:
            logger.warning(
                f"Adapter {adapter.source.value} failed for chip distribution: {e}"
            )

        # Try failover
        if ":" in ticker:
            exchange, _ = ticker.split(":", 1)
            adapters = self.get_adapters_for_exchange(exchange)

            for alt in adapters:
                if alt is adapter:
                    continue
                try:
                    logger.info(
                        f"Trying failover adapter {alt.source.value} "
                        f"for chip distribution of {ticker}"
                    )
                    return await alt.get_chip_distribution(ticker, days)
                except Exception as failover_error:
                    logger.warning(
                        f"Failover adapter {alt.source.value} "
                        f"also failed: {failover_error}"
                    )
                    continue

        raise ValueError(f"All adapters failed to fetch chip distribution for {ticker}")

    async def get_macro_data(self, indicators: List[str] = None) -> Dict[str, Any]:
        """获取宏观经济数据 (带自动降级)

        Args:
            indicators: 指标列表 ["CPI", "PPI", "M2", "GDP"]

        Returns:
            包含宏观数据的字典
        """
        # 宏观数据优先使用 Tushare
        from src.server.domain.types import DataSource

        if DataSource.TUSHARE in self.adapters:
            adapter = self.adapters[DataSource.TUSHARE]
            try:
                return await adapter.get_macro_data(indicators)
            except Exception as e:
                logger.warning(f"Tushare failed for macro data: {e}")

        # Try other adapters
        for adapter in self._adapter_order:
            try:
                return await adapter.get_macro_data(indicators)
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning(f"Adapter {adapter.source.value} failed for macro: {e}")
                continue

        raise ValueError("No adapter supports macro economic data")

    async def get_technical_indicators(
        self,
        ticker: str,
        indicators: List[str],
        period: str = "daily",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """获取技术指标数据 (带自动降级)

        Args:
            ticker: 股票代码
            indicators: 指标列表
            period: 周期
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            技术指标数据
        """
        adapter = self.get_adapter_for_ticker(ticker)
        if not adapter:
            raise ValueError(f"No adapter found for ticker {ticker}")

        try:
            return await adapter.get_technical_indicators(
                ticker, indicators, period, start_date, end_date
            )
        except NotImplementedError:
            logger.warning(
                f"Adapter {adapter.source.value} does not support technical indicators"
            )
        except Exception as e:
            logger.warning(
                f"Adapter {adapter.source.value} failed for technical indicators: {e}"
            )

        # Try failover
        if ":" in ticker:
            exchange, _ = ticker.split(":", 1)
            adapters = self.get_adapters_for_exchange(exchange)

            for alt in adapters:
                if alt is adapter:
                    continue
                try:
                    logger.info(
                        f"Trying failover adapter {alt.source.value} "
                        f"for technical indicators of {ticker}"
                    )
                    return await alt.get_technical_indicators(
                        ticker, indicators, period, start_date, end_date
                    )
                except Exception as failover_error:
                    logger.warning(
                        f"Failover adapter {alt.source.value} "
                        f"also failed: {failover_error}"
                    )
                    continue

        raise ValueError(f"All adapters failed to fetch technical indicators for {ticker}")


# Singleton instance
_adapter_manager_instance: Optional[AdapterManager] = None
_adapter_manager_lock = threading.Lock()


def get_adapter_manager() -> AdapterManager:
    """Get the singleton AdapterManager instance."""
    global _adapter_manager_instance

    if _adapter_manager_instance is None:
        with _adapter_manager_lock:
            if _adapter_manager_instance is None:
                _adapter_manager_instance = AdapterManager()

    return _adapter_manager_instance
