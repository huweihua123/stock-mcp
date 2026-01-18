# src/server/domain/adapters/base.py
"""Abstract base class for data adapters.

Each adapter must implement async methods to fetch price, history
and optional search, returning structured data models.
"""

import abc
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetSearchQuery,
    AssetSearchResult,
    AssetType,
    DataSource,
    Exchange,
)

logger = logging.getLogger(__name__)


class BaseDataAdapter(abc.ABC):
    """Abstract base class for all data source adapters.

    Each adapter must implement:
    - get_asset_info: Fetch detailed asset info
    - get_real_time_price: Fetch current price
    - get_historical_prices: Fetch historical data
    - search_assets: Search for assets
    - get_capabilities: Declare supported asset types and exchanges
    """

    name: str
    source: DataSource

    def __init__(self, source: DataSource, **kwargs):
        """Initialize adapter with data source and configuration.

        Args:
            source: Data source identifier
            **kwargs: Additional configuration parameters
        """
        self.source = source
        self.config = kwargs
        self.logger = logging.getLogger(f"{__name__}.{source.value}")

    @abc.abstractmethod
    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        """Fetch detailed information for an asset.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Asset object or None if not found
        """
        pass

    @abc.abstractmethod
    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        """Fetch current price for ticker.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Current price data or None if not found
        """
        pass

    async def get_multiple_prices(
        self, tickers: List[str]
    ) -> Dict[str, Optional[AssetPrice]]:
        """Fetch prices for multiple tickers efficiently.

        Default implementation calls get_real_time_price sequentially.
        Adapters should override this if they support batch fetching.

        Args:
            tickers: List of asset tickers

        Returns:
            Dictionary mapping tickers to price data
        """
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = await self.get_real_time_price(ticker)
            except Exception as e:
                self.logger.warning(f"Failed to fetch price for {ticker}: {e}")
                results[ticker] = None
        return results

    @abc.abstractmethod
    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        """Fetch historical price data.

        Args:
            ticker: Asset ticker in internal format
            start_date: Start date
            end_date: End date
            interval: Data interval

        Returns:
            List of historical price data
        """
        pass

    @abc.abstractmethod
    async def search_assets(self, query: AssetSearchQuery) -> List[AssetSearchResult]:
        """Search for assets.

        Args:
            query: Search query parameters

        Returns:
            List of search results
        """
        pass

    async def get_filings(
        self,
        ticker: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get regulatory filings/announcements."""
        raise NotImplementedError(f"{self.name} does not support filings retrieval")

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        """Optional method to fetch fundamental/financial data.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Dictionary containing financial statements and metrics
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_financials"
        )

    async def get_money_flow(self, ticker: str, days: int = 20) -> Dict[str, Any]:
        """获取个股资金流向数据 (Optional)

        Args:
            ticker: 股票代码 (内部格式 SSE:600519)
            days: 获取最近 N 天数据

        Returns:
            包含资金流向数据的字典
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_money_flow"
        )

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        """获取北向资金流向数据 (Optional)

        Args:
            days: 获取最近 N 天数据

        Returns:
            包含北向资金数据的字典
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_north_bound_flow"
        )

    async def get_chip_distribution(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """获取筹码分布数据 (Optional)

        Args:
            ticker: 股票代码 (内部格式)
            days: 获取最近 N 天数据

        Returns:
            包含筹码分布数据的字典
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_chip_distribution"
        )

    async def get_macro_data(self, indicators: List[str] = None) -> Dict[str, Any]:
        """获取宏观经济数据 (Optional)

        Args:
            indicators: 指标列表 ["CPI", "PPI", "M2", "GDP"]

        Returns:
            包含宏观数据的字典
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_macro_data"
        )

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
            indicators: List of indicators to calculate (e.g., ["MA", "MACD"])
            period: Data period ("daily", "weekly", "monthly")
            start_date: Start date
            end_date: End date

        Returns:
            Dictionary containing calculated indicators
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_technical_indicators"
        )

    @abc.abstractmethod
    def get_capabilities(self) -> List[AdapterCapability]:
        """Get capabilities describing supported types and exchanges.

        Returns:
            List of capabilities
        """
        pass

    def get_supported_asset_types(self) -> List[AssetType]:
        """Get list of asset types supported by this adapter."""
        capabilities = self.get_capabilities()
        asset_types = set()
        for cap in capabilities:
            asset_types.add(cap.asset_type)
        return list(asset_types)

    def get_supported_exchanges(self) -> Set[Exchange]:
        """Get set of all exchanges supported by this adapter."""
        capabilities = self.get_capabilities()
        exchanges: Set[Exchange] = set()
        for cap in capabilities:
            exchanges.update(cap.exchanges)
        return exchanges

    def validate_ticker(self, ticker: str) -> bool:
        """Validate if ticker format is supported by this adapter.

        Args:
            ticker: Ticker in internal format (e.g., "NASDAQ:AAPL")

        Returns:
            True if ticker is valid for this adapter
        """
        try:
            if ":" not in ticker:
                return False

            exchange, _ = ticker.split(":", 1)
            capabilities = self.get_capabilities()

            # Check if any capability supports this exchange
            return any(
                cap.supports_exchange(Exchange(exchange)) for cap in capabilities
            )
        except Exception:
            return False

    @abc.abstractmethod
    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        """Convert internal ticker to data source format.

        Args:
            internal_ticker: Ticker in internal format

        Returns:
            Ticker in data source specific format
        """
        pass

    @abc.abstractmethod
    def convert_to_internal_ticker(
        self, source_ticker: str, default_exchange: Optional[str] = None
    ) -> str:
        """Convert data source ticker to internal format.

        Args:
            source_ticker: Ticker in data source format
            default_exchange: Default exchange if not determinable

        Returns:
            Ticker in internal format
        """
        pass
