"""Runtime-native capability-facing provider facade.

This replaces the old `domain.market_gateway` as the single high-level
interface used by capability services and legacy domain services.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols.types import InstrumentRef, ResolutionStatus

_TICKER_METHODS: Set[str] = {
    "get_asset_info",
    "get_financials",
    "get_mainbz_info",
    "get_shareholder_info",
    "get_dividend_info",
    "get_forecast_info",
    "get_valuation_metrics",
    "get_money_flow",
    "get_chip_distribution",
    "get_filings",
    "get_earnings_history",
    "get_cash_flow_quality",
    "get_us_valuation_metrics",
    "get_us_institutional_holdings",
    "get_us_price_history",
    "get_us_volume_analysis",
}

_MARKET_METHODS: Set[str] = {
    "get_north_bound_flow",
    "get_money_supply",
    "get_inflation_data",
    "get_pmi_data",
    "get_gdp_data",
    "get_social_financing",
    "get_interest_rates",
    "get_market_liquidity",
    "get_market_money_flow",
    "resolve_sector",
    "get_sector_trend",
    "get_sector_money_flow_history",
    "get_sector_valuation_metrics",
    "get_ggt_daily",
    "get_us_sector_etf_analysis",
    "get_us_economic_growth",
    "get_us_inflation_employment",
    "get_us_interest_rates",
}


class ProviderFacade:
    """Resolve symbols and route calls through runtime-native provider plumbing."""

    def __init__(self, provider_runtime, symbol_resolver, market_router=None):
        self._provider_runtime = provider_runtime
        self._resolver = symbol_resolver
        self._router = market_router
        self._method_cache: Dict[str, Any] = {}

    @property
    def adapters(self):
        return getattr(self._provider_runtime, "adapters", {})

    async def resolve_ticker(self, raw_symbol: str) -> str:
        resolution = await self._resolver.resolve(raw_symbol)
        if resolution.status == ResolutionStatus.RESOLVED and resolution.normalized:
            return resolution.normalized
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            raise SymbolResolutionError(
                code="SYMBOL_AMBIGUOUS",
                message="symbol is ambiguous; specify exchange",
                raw=raw_symbol,
                candidates=[candidate.ticker for candidate in resolution.candidates],
            )
        if resolution.status == ResolutionStatus.NOT_FOUND:
            raise SymbolResolutionError(
                code="SYMBOL_NOT_FOUND",
                message="symbol not found",
                raw=raw_symbol,
            )
        raise SymbolResolutionError(
            code="SYMBOL_INVALID",
            message=resolution.reason or "invalid symbol",
            raw=raw_symbol,
        )

    async def resolve_instrument(self, raw_symbol: str):
        resolution = await self._resolver.resolve(raw_symbol)
        if resolution.status == ResolutionStatus.RESOLVED and resolution.instrument:
            return resolution.instrument
        if resolution.status == ResolutionStatus.RESOLVED and resolution.normalized:
            exchange, symbol = resolution.normalized.split(":", 1)
            return resolution.instrument or InstrumentRef(
                canonical_id=f"stock|{exchange}|{symbol}",
                normalized=resolution.normalized,
                asset_type=resolution.asset_type or "stock",
                exchange=exchange,
                raw_input=raw_symbol,
            )
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            raise SymbolResolutionError(
                code="SYMBOL_AMBIGUOUS",
                message="symbol is ambiguous; specify exchange",
                raw=raw_symbol,
                candidates=[candidate.ticker for candidate in resolution.candidates],
            )
        if resolution.status == ResolutionStatus.NOT_FOUND:
            raise SymbolResolutionError(
                code="SYMBOL_NOT_FOUND",
                message="symbol not found",
                raw=raw_symbol,
            )
        raise SymbolResolutionError(
            code="SYMBOL_INVALID",
            message=resolution.reason or "invalid symbol",
            raw=raw_symbol,
        )

    async def get_real_time_price(self, raw_symbol: str):
        instrument = await self.resolve_instrument(raw_symbol)
        if self._router and hasattr(instrument, "normalized"):
            return await self._router.get_real_time_price(instrument)
        ticker = (
            instrument.normalized
            if hasattr(instrument, "normalized")
            else await self.resolve_ticker(raw_symbol)
        )
        return await self._provider_runtime.get_real_time_price(ticker)

    async def get_historical_prices(
        self, raw_symbol: str, start_date, end_date, interval: str = "1d"
    ):
        instrument = await self.resolve_instrument(raw_symbol)
        if self._router and hasattr(instrument, "normalized"):
            return await self._router.get_historical_prices(
                instrument, start_date, end_date, interval
            )
        ticker = (
            instrument.normalized
            if hasattr(instrument, "normalized")
            else await self.resolve_ticker(raw_symbol)
        )
        return await self._provider_runtime.get_historical_prices(
            ticker, start_date, end_date, interval
        )

    async def get_multiple_prices(self, raw_symbols: List[str]) -> Dict[str, Any]:
        if self._router:
            results: Dict[str, Any] = {}
            for raw in raw_symbols:
                try:
                    instrument = await self.resolve_instrument(raw)
                    price = await self._router.get_real_time_price(instrument)
                    results[raw] = (
                        price.to_dict()
                        if price and hasattr(price, "to_dict")
                        else price
                    )
                except SymbolResolutionError as exc:
                    results[raw] = {"error": exc.to_dict()}
            return results

        resolutions = await asyncio.gather(
            *[self._resolver.resolve(symbol) for symbol in raw_symbols],
            return_exceptions=True,
        )
        resolved_map: Dict[str, Optional[str]] = {}
        errors: Dict[str, dict] = {}

        for raw, result in zip(raw_symbols, resolutions):
            if isinstance(result, Exception):
                errors[raw] = {
                    "error": {
                        "code": "RESOLVE_FAILED",
                        "message": str(result),
                        "raw": raw,
                    }
                }
                continue
            if result.status == ResolutionStatus.RESOLVED and result.normalized:
                resolved_map[raw] = result.normalized
            elif result.status == ResolutionStatus.AMBIGUOUS:
                errors[raw] = {
                    "error": {
                        "code": "SYMBOL_AMBIGUOUS",
                        "message": "symbol is ambiguous; specify exchange",
                        "raw": raw,
                        "candidates": [candidate.ticker for candidate in result.candidates],
                    }
                }
            elif result.status == ResolutionStatus.NOT_FOUND:
                errors[raw] = {
                    "error": {
                        "code": "SYMBOL_NOT_FOUND",
                        "message": "symbol not found",
                        "raw": raw,
                    }
                }
            else:
                errors[raw] = {
                    "error": {
                        "code": "SYMBOL_INVALID",
                        "message": result.reason or "invalid symbol",
                        "raw": raw,
                    }
                }

        resolved_tickers = [ticker for ticker in resolved_map.values() if ticker]
        results: Dict[str, Any] = {}
        if resolved_tickers:
            prices = await self._provider_runtime.get_multiple_prices(resolved_tickers)
            for raw, resolved in resolved_map.items():
                price = prices.get(resolved)
                if price is not None and hasattr(price, "to_dict"):
                    data = price.to_dict()
                    data["resolved_ticker"] = resolved
                    results[raw] = data
                else:
                    results[raw] = None

        for raw, err in errors.items():
            results[raw] = err
        for raw in raw_symbols:
            results.setdefault(raw, None)
        return results

    async def get_technical_indicators(
        self,
        raw_symbol: str | None = None,
        indicators: List[str] | None = None,
        period: str = "daily",
        start_date=None,
        end_date=None,
        *,
        ticker: str | None = None,
    ) -> Dict[str, Any]:
        if ticker:
            resolved = ticker if ":" in ticker else await self.resolve_ticker(ticker)
        else:
            if not raw_symbol:
                raise ValueError("raw_symbol or ticker is required")
            resolved = await self.resolve_ticker(raw_symbol)
        return await self._provider_runtime.get_technical_indicators(
            ticker=resolved,
            indicators=indicators or [],
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

    def __getattr__(self, item: str):
        if item.startswith("_"):
            raise AttributeError(item)

        cache = object.__getattribute__(self, "_method_cache")
        if item in cache:
            return cache[item]

        provider_runtime = object.__getattribute__(self, "_provider_runtime")

        if item in _TICKER_METHODS:
            async def _ticker_method(raw_symbol: str, *args, **kwargs):
                ticker = await self.resolve_ticker(raw_symbol)
                return await getattr(provider_runtime, item)(ticker, *args, **kwargs)

            _ticker_method.__name__ = item
            _ticker_method.__qualname__ = f"ProviderFacade.{item}"
            cache[item] = _ticker_method
            return _ticker_method

        if item in _MARKET_METHODS:
            async def _market_method(*args, **kwargs):
                return await getattr(provider_runtime, item)(*args, **kwargs)

            _market_method.__name__ = item
            _market_method.__qualname__ = f"ProviderFacade.{item}"
            cache[item] = _market_method
            return _market_method

        return getattr(provider_runtime, item)
