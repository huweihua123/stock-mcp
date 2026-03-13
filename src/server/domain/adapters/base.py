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

    async def get_real_time_price_by_provider_symbol(
        self, provider_symbol: str, internal_ticker: Optional[str] = None
    ) -> Optional[AssetPrice]:
        """Fetch current price using provider-specific symbol if supported.

        Default behavior uses internal ticker when provided.
        """
        if internal_ticker:
            return await self.get_real_time_price(internal_ticker)
        return await self.get_real_time_price(provider_symbol)

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

    async def get_historical_prices_by_provider_symbol(
        self,
        provider_symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
        internal_ticker: Optional[str] = None,
    ) -> List[AssetPrice]:
        """Fetch historical price data using provider-specific symbol if supported.

        Default behavior uses internal ticker when provided.
        """
        if internal_ticker:
            return await self.get_historical_prices(
                internal_ticker, start_date, end_date, interval
            )
        return await self.get_historical_prices(
            provider_symbol, start_date, end_date, interval
        )

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

    async def get_dividend_info(self, ticker: str) -> Dict[str, Any]:
        """Optional method to fetch dividend history info.

        Args:
            ticker: Asset ticker in internal format

        Returns:
            Dictionary containing dividend history data
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_dividend_info"
        )

    async def get_forecast_info(
        self, ticker: str, limit: int = 50
    ) -> Dict[str, Any]:
        """Optional method to fetch performance forecast info.

        Args:
            ticker: Asset ticker in internal format
            limit: Maximum number of records

        Returns:
            Dictionary containing forecast rows
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_forecast_info"
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

    async def get_money_supply(self, months: int = 60) -> Dict[str, Any]:
        """获取货币供应量数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_money_supply"
        )

    async def get_inflation_data(self, months: int = 60) -> Dict[str, Any]:
        """获取通胀数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_inflation_data"
        )

    async def get_pmi_data(self, months: int = 60) -> Dict[str, Any]:
        """获取 PMI 数据 (Optional)."""
        raise NotImplementedError(f"{self.source.value} does not support get_pmi_data")

    async def get_gdp_data(self, quarters: int = 20) -> Dict[str, Any]:
        """获取 GDP 数据 (Optional)."""
        raise NotImplementedError(f"{self.source.value} does not support get_gdp_data")

    async def get_social_financing(self, months: int = 60) -> Dict[str, Any]:
        """获取社融数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_social_financing"
        )

    async def get_interest_rates(
        self, shibor_days: int = 252, lpr_months: int = 60
    ) -> Dict[str, Any]:
        """获取利率数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_interest_rates"
        )

    async def get_market_liquidity(self, days: int = 60) -> Dict[str, Any]:
        """获取市场流动性数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_market_liquidity"
        )

    async def get_market_money_flow(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        include_outflow: bool = True,
    ) -> Dict[str, Any]:
        """获取市场资金流向数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_market_money_flow"
        )

    async def resolve_sector(
        self, query_text: str, intent: str = "trend"
    ) -> Dict[str, Any]:
        """解析板块查询词为稳定板块ID (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support resolve_sector"
        )

    async def get_sector_trend(
        self,
        sector_name: str = "",
        days: int = 10,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块走势数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_sector_trend"
        )

    async def get_ggt_daily(self, days: int = 60) -> Dict[str, Any]:
        """获取港股通每日成交统计 (Optional)."""
        raise NotImplementedError(f"{self.source.value} does not support get_ggt_daily")

    async def get_mainbz_info(self, ticker: str) -> Dict[str, Any]:
        """获取主营业务构成 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_mainbz_info"
        )

    async def get_shareholder_info(self, ticker: str) -> Dict[str, Any]:
        """获取股东信息 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_shareholder_info"
        )

    async def get_valuation_metrics(
        self, ticker: str, days: int = 250
    ) -> Dict[str, Any]:
        """获取估值指标数据 (PE/PB/PS + 历史百分位) (Optional).

        Args:
            ticker: 股票代码 (内部格式)
            days: 拉取最近 N 个交易日数据

        Returns:
            包含估值指标及百分位信息的字典
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_valuation_metrics"
        )

    async def get_sector_money_flow_history(
        self,
        sector_name: str = "",
        days: int = 20,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块资金流向历史数据 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support " "get_sector_money_flow_history"
        )

    async def get_sector_valuation_metrics(
        self,
        sector_name: str = "",
        days: int = 250,
        sample_size: int = 60,
        sector_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取板块估值指标与历史分位 (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_sector_valuation_metrics"
        )

    # =========================================================================
    # US-market specific operations (Optional)
    # =========================================================================

    async def get_earnings_history(
        self, ticker: str, quarters: int = 8
    ) -> Dict[str, Any]:
        """Fetch EPS history: estimate vs actual and surprise % (Optional).

        Args:
            ticker: Asset ticker in internal format (e.g. NASDAQ:AAPL)
            quarters: Number of past quarters to return

        Returns:
            Dict with keys: ticker, quarters (list of {date, actual_eps,
            estimated_eps, surprise_pct})
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_earnings_history"
        )

    async def get_cash_flow_quality(self, ticker: str) -> Dict[str, Any]:
        """Fetch operating / free cash flow and FCF/net-income ratio (Optional).

        Returns:
            Dict with keys: ticker, annual (list of {year, operating_cf,
            capex, free_cf, net_income, fcf_ratio})
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_cash_flow_quality"
        )

    async def get_us_valuation_metrics(self, ticker: str) -> Dict[str, Any]:
        """Fetch US stock valuation: PE/PS/PB/EV_EBITDA (Optional).

        Returns:
            Dict with keys: ticker, pe_ttm, ps_ttm, pb, ev_ebitda,
            market_cap, enterprise_value
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_valuation_metrics"
        )

    async def get_us_institutional_holdings(self, ticker: str) -> Dict[str, Any]:
        """Fetch top institutional holders and recent change (Optional).

        Returns:
            Dict with keys: ticker, holders (list of {name, pct_held,
            shares, change_pct, filing_date})
        """
        raise NotImplementedError(
            f"{self.source.value} does not support " "get_us_institutional_holdings"
        )

    async def get_us_price_history(
        self, ticker: str, days: int = 60, interval: str = "1d"
    ) -> Dict[str, Any]:
        """Fetch OHLCV klines for a US stock (Optional).

        Returns:
            Dict with keys: ticker, interval, bars (list of {date, open,
            high, low, close, volume})
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_price_history"
        )

    async def get_us_volume_analysis(
        self, ticker: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch volume metrics: avg volume, relative volume, OBV (Optional).

        Returns:
            Dict with keys: ticker, avg_volume_20d, current_volume,
            rvol, obv_trend, bars (list of {date, volume, rvol})
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_volume_analysis"
        )

    async def get_us_sector_etf_analysis(
        self, sector_name: str, days: int = 30
    ) -> Dict[str, Any]:
        """Fetch US sector ETF klines by sector name (Optional).

        Sector name is mapped to a representative ETF ticker internally.

        Returns:
            Dict with keys: sector_name, etf_ticker, bars (list of
            {date, close, change_pct}), trend_summary
        """
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_sector_etf_analysis"
        )

    async def get_us_economic_growth(self, quarters: int = 20) -> Dict[str, Any]:
        """Fetch US real GDP levels and growth rates (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_economic_growth"
        )

    async def get_us_inflation_employment(self, months: int = 24) -> Dict[str, Any]:
        """Fetch US CPI and unemployment time series (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_inflation_employment"
        )

    async def get_us_interest_rates(self, days: int = 180) -> Dict[str, Any]:
        """Fetch US rates (2Y/10Y/Fed Funds) and curve spread (Optional)."""
        raise NotImplementedError(
            f"{self.source.value} does not support get_us_interest_rates"
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
