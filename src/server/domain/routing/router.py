# src/server/domain/routing/router.py
"""Market router that selects providers based on asset type and routing policy."""

from __future__ import annotations

import time
import asyncio
from typing import List, Optional

from src.server.domain.symbols.types import InstrumentRef
from src.server.utils.logger import logger


class MarketRouter:
    def __init__(
        self,
        adapter_manager,
        security_master_repo,
        routing_policy,
        health_tracker,
        provider_timeout_seconds: float = 12.0,
    ):
        self._adapters = adapter_manager
        self._repo = security_master_repo
        self._policy = routing_policy
        self._health = health_tracker
        self._provider_timeout_seconds = max(float(provider_timeout_seconds), 1.0)

    async def get_real_time_price(self, instrument: InstrumentRef):
        providers = self._policy.select_providers(
            instrument.asset_type, instrument.exchange, data_type="realtime"
        )
        if not providers:
            return await self._adapters.get_real_time_price(instrument.normalized)

        provider_symbols = await self._get_provider_symbols(
            instrument=instrument, data_type="realtime"
        )

        for provider in providers:
            if not self._health.is_available(
                provider, instrument.asset_type, "realtime"
            ):
                continue
            adapter = self._adapters.get_adapter_by_provider(provider)
            if not adapter:
                continue

            provider_symbol = provider_symbols.get(provider)
            start = time.time()
            try:
                if provider_symbol:
                    price = await asyncio.wait_for(
                        adapter.get_real_time_price_by_provider_symbol(
                            provider_symbol, internal_ticker=instrument.normalized
                        ),
                        timeout=self._provider_timeout_seconds,
                    )
                else:
                    price = await asyncio.wait_for(
                        adapter.get_real_time_price(instrument.normalized),
                        timeout=self._provider_timeout_seconds,
                    )

                latency_ms = (time.time() - start) * 1000
                if price is None:
                    self._health.record(provider, instrument.asset_type, "realtime", "empty", latency_ms)
                    continue
                self._health.record(provider, instrument.asset_type, "realtime", "success", latency_ms)
                return price
            except Exception as e:
                latency_ms = (time.time() - start) * 1000
                self._health.record(provider, instrument.asset_type, "realtime", "error", latency_ms)
                logger.warning("Provider realtime fetch failed", provider=provider, error=str(e))
                continue

        # Fallback to legacy adapter routing
        return await self._adapters.get_real_time_price(instrument.normalized)

    async def get_historical_prices(
        self, instrument: InstrumentRef, start_date, end_date, interval: str = "1d"
    ) -> List:
        providers = self._policy.select_providers(
            instrument.asset_type, instrument.exchange, data_type="historical"
        )
        if not providers:
            return await self._adapters.get_historical_prices(
                instrument.normalized, start_date, end_date, interval
            )

        provider_symbols = await self._get_provider_symbols(
            instrument=instrument, data_type="historical", interval=interval
        )

        for provider in providers:
            if not self._health.is_available(
                provider, instrument.asset_type, "historical"
            ):
                continue
            adapter = self._adapters.get_adapter_by_provider(provider)
            if not adapter:
                continue

            provider_symbol = provider_symbols.get(provider)
            start = time.time()
            try:
                if provider_symbol:
                    prices = await asyncio.wait_for(
                        adapter.get_historical_prices_by_provider_symbol(
                            provider_symbol,
                            start_date,
                            end_date,
                            interval,
                            internal_ticker=instrument.normalized,
                        ),
                        timeout=self._provider_timeout_seconds,
                    )
                else:
                    prices = await asyncio.wait_for(
                        adapter.get_historical_prices(
                            instrument.normalized, start_date, end_date, interval
                        ),
                        timeout=self._provider_timeout_seconds,
                    )

                latency_ms = (time.time() - start) * 1000
                if not prices:
                    self._health.record(provider, instrument.asset_type, "historical", "empty", latency_ms)
                    continue
                self._health.record(provider, instrument.asset_type, "historical", "success", latency_ms)
                return prices
            except Exception as e:
                latency_ms = (time.time() - start) * 1000
                self._health.record(provider, instrument.asset_type, "historical", "error", latency_ms)
                logger.warning("Provider historical fetch failed", provider=provider, error=str(e))
                continue

        # Fallback to legacy adapter routing
        return await self._adapters.get_historical_prices(
            instrument.normalized, start_date, end_date, interval
        )

    async def _get_provider_symbols(
        self,
        instrument: InstrumentRef,
        data_type: str,
        interval: Optional[str] = None,
    ) -> dict:
        if not instrument or not instrument.normalized:
            return {}
        exchange, symbol = instrument.normalized.split(":", 1)
        listing = await self._repo.find_by_listing(exchange, symbol)
        asset_id = listing.get("asset_id") if listing else None
        if not asset_id:
            return {}
        symbols = await self._repo.get_provider_symbols(asset_id, data_type=data_type)
        mapped = {}
        for item in symbols:
            if interval and item.get("intervals_supported"):
                if interval not in item.get("intervals_supported"):
                    continue
            mapped[item.get("provider")] = item.get("provider_symbol")
        return mapped
