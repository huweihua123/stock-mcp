# src/server/domain/market_gateway.py
"""MarketGateway: symbol resolution + adapter routing wrapper.

Design (v2):
- Explicit methods only for operations that need custom routing logic
  (router delegation, multi-symbol batching, dual-kwarg signatures).
- All other ticker-scoped / market-wide operations are handled via
  __getattr__ + two declarative registries:
    _TICKER_METHODS  – set of method names that take (raw_symbol, **kwargs)
    _MARKET_METHODS  – set of method names that take (**kwargs), no symbol
  Adding a new operation: add one line to the appropriate registry set.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols.types import InstrumentRef, ResolutionStatus
from src.server.utils.logger import logger  # noqa: F401 – kept for subclass use

# ---------------------------------------------------------------------------
# Declarative routing registries
# ---------------------------------------------------------------------------
# ticker-scoped: gateway will resolve raw_symbol → ticker, then forward.
# To add a new ticker-scoped operation, append its name here — that's it.
_TICKER_METHODS: Set[str] = {
    # core
    "get_asset_info",
    "get_financials",
    "get_mainbz_info",
    "get_shareholder_info",
    "get_dividend_info",
    "get_valuation_metrics",
    "get_money_flow",
    "get_chip_distribution",
    "get_filings",
    # US fundamental
    "get_earnings_history",
    "get_cash_flow_quality",
    "get_us_valuation_metrics",
    "get_us_institutional_holdings",
    # US technical
    "get_us_price_history",
    "get_us_volume_analysis",
}

# market-wide: no symbol resolution needed, forward kwargs as-is.
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
    "get_sector_trend",
    "get_sector_money_flow_history",
    "get_ggt_daily",
    # US sector (market-wide, no symbol)
    "get_us_sector_etf_analysis",
}


class MarketGateway:
    """Unified gateway: symbol resolution + adapter routing.

    Explicit async methods are kept **only** when they need special logic
    (router delegation, batching, dual-signature handling).

    Everything in _TICKER_METHODS / _MARKET_METHODS is handled by __getattr__
    which synthesises a coroutine-returning callable on first attribute access
    and caches it so subsequent calls pay zero overhead.
    """

    def __init__(self, adapter_manager, symbol_resolver, market_router=None):
        self._adapter_manager = adapter_manager
        self._resolver = symbol_resolver
        self._router = market_router
        # Cache synthesised bound methods to avoid rebuilding closures
        self._method_cache: Dict[str, Any] = {}

    @property
    def adapters(self):
        return getattr(self._adapter_manager, "adapters", {})

    # ------------------------------------------------------------------
    # Symbol resolution helpers
    # ------------------------------------------------------------------

    async def resolve_ticker(self, raw_symbol: str) -> str:
        resolution = await self._resolver.resolve(raw_symbol)
        if resolution.status == ResolutionStatus.RESOLVED and resolution.normalized:
            return resolution.normalized
        if resolution.status == ResolutionStatus.AMBIGUOUS:
            raise SymbolResolutionError(
                code="SYMBOL_AMBIGUOUS",
                message="symbol is ambiguous; specify exchange",
                raw=raw_symbol,
                candidates=[c.ticker for c in resolution.candidates],
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
                candidates=[c.ticker for c in resolution.candidates],
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

    # ------------------------------------------------------------------
    # Explicit methods — kept because they have non-trivial routing logic
    # ------------------------------------------------------------------

    async def get_real_time_price(self, raw_symbol: str):
        instrument = await self.resolve_instrument(raw_symbol)
        if self._router and hasattr(instrument, "normalized"):
            return await self._router.get_real_time_price(instrument)
        ticker = (
            instrument.normalized
            if hasattr(instrument, "normalized")
            else await self.resolve_ticker(raw_symbol)
        )
        return await self._adapter_manager.get_real_time_price(ticker)

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
        return await self._adapter_manager.get_historical_prices(
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
                except SymbolResolutionError as e:
                    results[raw] = {"error": e.to_dict()}
            return results

        resolutions = await asyncio.gather(
            *[self._resolver.resolve(sym) for sym in raw_symbols],
            return_exceptions=True,
        )
        resolved_map: Dict[str, Optional[str]] = {}
        errors: Dict[str, dict] = {}

        for raw, res in zip(raw_symbols, resolutions):
            if isinstance(res, Exception):
                errors[raw] = {
                    "error": {"code": "RESOLVE_FAILED", "message": str(res), "raw": raw}
                }
                continue
            if res.status == ResolutionStatus.RESOLVED and res.normalized:
                resolved_map[raw] = res.normalized
            elif res.status == ResolutionStatus.AMBIGUOUS:
                errors[raw] = {
                    "error": {
                        "code": "SYMBOL_AMBIGUOUS",
                        "message": "symbol is ambiguous; specify exchange",
                        "raw": raw,
                        "candidates": [c.ticker for c in res.candidates],
                    }
                }
            elif res.status == ResolutionStatus.NOT_FOUND:
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
                        "message": res.reason or "invalid symbol",
                        "raw": raw,
                    }
                }

        resolved_tickers = [t for t in resolved_map.values() if t]
        results: Dict[str, Any] = {}
        if resolved_tickers:
            prices = await self._adapter_manager.get_multiple_prices(resolved_tickers)
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
        """Dual-signature: accepts raw_symbol or pre-resolved ticker kwarg."""
        if ticker:
            resolved = ticker if ":" in ticker else await self.resolve_ticker(ticker)
        else:
            if not raw_symbol:
                raise ValueError("raw_symbol or ticker is required")
            resolved = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_technical_indicators(
            ticker=resolved,
            indicators=indicators or [],
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

    # ------------------------------------------------------------------
    # __getattr__: synthesise ticker-scoped and market-wide methods
    # ------------------------------------------------------------------

    def __getattr__(self, item: str):
        # Avoid infinite recursion on private/dunder attrs during __init__
        if item.startswith("_"):
            raise AttributeError(item)

        # Return cached synthesised method if available
        cache = object.__getattribute__(self, "_method_cache")
        if item in cache:
            return cache[item]

        adapter_manager = object.__getattribute__(self, "_adapter_manager")

        if item in _TICKER_METHODS:
            # Synthesise: resolve raw_symbol → ticker, then forward to adapter_manager
            async def _ticker_method(raw_symbol: str, **kwargs):
                ticker = await self.resolve_ticker(raw_symbol)
                return await getattr(adapter_manager, item)(ticker, **kwargs)

            _ticker_method.__name__ = item
            _ticker_method.__qualname__ = f"MarketGateway.{item}"
            cache[item] = _ticker_method
            return _ticker_method

        if item in _MARKET_METHODS:
            # Synthesise: forward positional and keyword args to adapter_manager
            async def _market_method(*args, **kwargs):
                return await getattr(adapter_manager, item)(*args, **kwargs)

            _market_method.__name__ = item
            _market_method.__qualname__ = f"MarketGateway.{item}"
            cache[item] = _market_method
            return _market_method

        # Final fallback: pass-through to adapter_manager
        return getattr(adapter_manager, item)
