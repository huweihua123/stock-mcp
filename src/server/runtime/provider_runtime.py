"""Runtime-native provider coordination layer.

This is the new home for the logic that previously lived in
`domain.adapter_manager`. Capability plugins and provider startup should
interact with this runtime primitive instead of the old center class.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import Asset, AssetPrice, AssetType, DataSource, Exchange

logger = logging.getLogger(__name__)

_SECTOR_SOFT_FALLBACK_METHODS = {
    "get_sector_trend",
    "get_sector_money_flow_history",
    "get_sector_valuation_metrics",
}


class ProviderRuntime:
    """Coordinate multiple provider adapters under the new runtime architecture."""

    def __init__(self, provider_timeout_seconds: float = 12.0):
        self.adapters: Dict[DataSource, BaseDataAdapter] = {}
        self._adapter_order: List[BaseDataAdapter] = []
        self.exchange_routing: Dict[str, List[BaseDataAdapter]] = {}
        self._ticker_cache: Dict[str, BaseDataAdapter] = {}
        self._cache_lock = threading.Lock()
        self.lock = threading.RLock()
        self._provider_timeout_seconds = max(float(provider_timeout_seconds), 1.0)
        logger.info("Provider runtime initialized")

    def _rebuild_routing_table(self) -> None:
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
                    self.exchange_routing.setdefault(exchange_key, []).append(adapter)
            with self._cache_lock:
                self._ticker_cache.clear()
            logger.debug(
                "Routing table rebuilt with %s exchanges", len(self.exchange_routing)
            )

    def register_adapter(self, adapter: BaseDataAdapter) -> None:
        with self.lock:
            if adapter.source in self.adapters:
                logger.info(
                    "Adapter already registered: %s, skipping duplicate",
                    adapter.source.value,
                )
                return
            self.adapters[adapter.source] = adapter
            self._adapter_order.append(adapter)
            self._rebuild_routing_table()
            logger.info("Registered adapter: %s", adapter.source.value)

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
        with self._cache_lock:
            if ticker in self._ticker_cache:
                return self._ticker_cache[ticker]
        if ":" not in ticker:
            logger.warning("Invalid ticker format (missing ':'): %s", ticker)
            return None
        exchange, _ = ticker.split(":", 1)
        adapters = self.get_adapters_for_exchange(exchange)
        if not adapters:
            logger.debug("No adapters registered for exchange: %s", exchange)
            return None
        for adapter in adapters:
            if adapter.validate_ticker(ticker):
                with self._cache_lock:
                    self._ticker_cache[ticker] = adapter
                return adapter
        logger.warning("No suitable adapter found for ticker: %s", ticker)
        return None

    def _get_fallbacks(
        self, ticker: str, primary: BaseDataAdapter
    ) -> List[BaseDataAdapter]:
        if ":" not in ticker:
            return []
        exchange, _ = ticker.split(":", 1)
        return [
            adapter
            for adapter in self.get_adapters_for_exchange(exchange)
            if adapter is not primary and adapter.validate_ticker(ticker)
        ]

    async def _dispatch_ticker(self, method: str, ticker: str, **kwargs) -> Any:
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
                if result is not None:
                    logger.debug("%s(%s) succeeded via %s", method, ticker, adapter.source.value)
                    if adapter is not primary:
                        with self._cache_lock:
                            self._ticker_cache[ticker] = adapter
                    return result
                logger.warning(
                    "%s.%s(%s) returned None, trying next",
                    adapter.source.value,
                    method,
                    ticker,
                )
            except NotImplementedError:
                logger.debug("%s does not support %s, skipping", adapter.source.value, method)
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"timeout after {self._provider_timeout_seconds}s"
                )
                logger.warning(
                    "%s.%s(%s) timeout in %ss",
                    adapter.source.value,
                    method,
                    ticker,
                    self._provider_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "%s.%s(%s) failed: %s",
                    adapter.source.value,
                    method,
                    ticker,
                    exc,
                )

        raise ValueError(f"All adapters failed for {ticker}.{method}: {last_error}")

    async def _dispatch_market(self, method: str, **kwargs) -> Any:
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
                            extra={"method": method, "adapter": adapter.source.value},
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
                    "%s.%s() timeout in %ss",
                    adapter.source.value,
                    method,
                    self._provider_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                logger.warning("%s.%s() failed: %s", adapter.source.value, method, exc)

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
            if not sector_id.endswith(".TI"):
                normalized["sector_id"] = None
            return normalized

        if adapter.source == DataSource.AKSHARE:
            if not sector_id.startswith("BK"):
                normalized["sector_id"] = None
            return normalized

        return normalized

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
        tasks = {ticker: self.get_real_time_price(ticker) for ticker in tickers}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            ticker: (None if isinstance(result, Exception) else result)
            for ticker, result in zip(tasks.keys(), results)
        }

    async def get_financials(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_financials", ticker)

    async def get_dividend_info(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_dividend_info", ticker)

    async def get_forecast_info(self, ticker: str, limit: int = 50) -> Dict[str, Any]:
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

    async def get_earnings_history(self, ticker: str, quarters: int = 8) -> Dict[str, Any]:
        return await self._dispatch_ticker(
            "get_earnings_history", ticker, quarters=quarters
        )

    async def get_cash_flow_quality(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_cash_flow_quality", ticker)

    async def get_us_valuation_metrics(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_us_valuation_metrics", ticker)

    async def get_us_institutional_holdings(self, ticker: str) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_us_institutional_holdings", ticker)

    async def get_us_price_history(
        self, ticker: str, days: int = 60, interval: str = "1d"
    ) -> Dict[str, Any]:
        return await self._dispatch_ticker(
            "get_us_price_history", ticker, days=days, interval=interval
        )

    async def get_us_volume_analysis(self, ticker: str, days: int = 30) -> Dict[str, Any]:
        return await self._dispatch_ticker("get_us_volume_analysis", ticker, days=days)

    async def get_us_sector_etf_analysis(
        self, sector_name: str, days: int = 30
    ) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_us_sector_etf_analysis", sector_name=sector_name, days=days
        )

    async def get_us_economic_growth(self, quarters: int = 20) -> Dict[str, Any]:
        return await self._dispatch_market("get_us_economic_growth", quarters=quarters)

    async def get_us_inflation_employment(self, months: int = 24) -> Dict[str, Any]:
        return await self._dispatch_market(
            "get_us_inflation_employment", months=months
        )

    async def get_us_interest_rates(self, days: int = 180) -> Dict[str, Any]:
        return await self._dispatch_market("get_us_interest_rates", days=days)

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        if DataSource.TUSHARE in self.adapters:
            try:
                return await self.adapters[DataSource.TUSHARE].get_north_bound_flow(days)
            except Exception as exc:
                logger.warning("Tushare failed for north_bound_flow: %s", exc)
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
            canonical.get("canonical_name") or canonical.get("query_text") or ""
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

    async def resolve_sector(self, query_text: str, intent: str = "trend") -> Dict[str, Any]:
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
                    "%s.resolve_sector() timeout in %ss",
                    adapter.source.value,
                    self._provider_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                logger.warning("%s.resolve_sector() failed: %s", adapter.source.value, exc)

        if tushare_ambiguous is not None:
            return tushare_ambiguous
        if first_non_tushare_resolved is not None:
            return first_non_tushare_resolved
        if first_ambiguous is not None:
            return first_ambiguous
        if last_not_found is not None:
            return last_not_found
        raise ValueError(f"No adapter supports resolve_sector: {last_error}")

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
