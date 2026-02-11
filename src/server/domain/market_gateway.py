# src/server/domain/market_gateway.py
"""MarketGateway: symbol resolution + adapter routing wrapper."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols.types import InstrumentRef, ResolutionStatus
from src.server.utils.logger import logger


class MarketGateway:
    def __init__(self, adapter_manager, symbol_resolver, market_router=None):
        self._adapter_manager = adapter_manager
        self._resolver = symbol_resolver
        self._router = market_router

    @property
    def adapters(self):
        # Expose underlying adapters for legacy usage
        return getattr(self._adapter_manager, "adapters", {})

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

    async def get_asset_info(self, raw_symbol: str):
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_asset_info(ticker)

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
                        price.to_dict() if price and hasattr(price, "to_dict") else price
                    )
                except SymbolResolutionError as e:
                    results[raw] = {"error": e.to_dict()}
            return results

        # Legacy batch path
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

    async def get_financials(self, raw_symbol: str) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_financials(ticker)

    async def get_mainbz_info(self, raw_symbol: str) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_mainbz_info(ticker)

    async def get_shareholder_info(self, raw_symbol: str) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_shareholder_info(ticker)

    async def get_dividend_info(self, raw_symbol: str) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_dividend_info(ticker)

    async def get_money_flow(self, raw_symbol: str, days: int = 20) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_money_flow(ticker, days)

    async def get_north_bound_flow(self, days: int = 30) -> Dict[str, Any]:
        return await self._adapter_manager.get_north_bound_flow(days)

    async def get_chip_distribution(self, raw_symbol: str, days: int = 30) -> Dict[str, Any]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_chip_distribution(ticker, days)

    async def get_money_supply(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_money_supply()

    async def get_inflation_data(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_inflation_data()

    async def get_pmi_data(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_pmi_data()

    async def get_gdp_data(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_gdp_data()

    async def get_social_financing(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_social_financing()

    async def get_interest_rates(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_interest_rates()

    async def get_market_liquidity(self, days: int = 60) -> Dict[str, Any]:
        return await self._adapter_manager.get_market_liquidity(days)

    async def get_market_money_flow(self) -> Dict[str, Any]:
        return await self._adapter_manager.get_market_money_flow()

    async def get_sector_trend(self, sector_name: str, days: int = 10) -> Dict[str, Any]:
        return await self._adapter_manager.get_sector_trend(sector_name, days)

    async def get_ggt_daily(self, days: int = 60) -> Dict[str, Any]:
        return await self._adapter_manager.get_ggt_daily(days)

    async def get_filings(
        self,
        raw_symbol: str,
        start_date=None,
        end_date=None,
        limit: int = 10,
        filing_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        ticker = await self.resolve_ticker(raw_symbol)
        return await self._adapter_manager.get_filings(
            ticker, start_date, end_date, limit, filing_types
        )

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
            if ":" in ticker:
                resolved = ticker
            else:
                resolved = await self.resolve_ticker(ticker)
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

    def __getattr__(self, item):
        # Fallback to adapter manager for legacy access
        return getattr(self._adapter_manager, item)
