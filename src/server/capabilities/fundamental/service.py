from __future__ import annotations

from typing import Any

from src.server.runtime.models import RuntimeContext


class FundamentalCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime

    @property
    def _provider_facade(self):
        return self._runtime.provider_facade

    @property
    def _service(self):
        return self._runtime.container.fundamental_service()

    async def get_fundamental_analysis(self, symbol: str) -> dict[str, Any]:
        resolved = await self._provider_facade.resolve_ticker(symbol)
        return await self._service.get_fundamental_analysis(resolved)

    async def get_financials(self, symbol: str) -> dict[str, Any]:
        return await self._provider_facade.get_financials(symbol)

    async def get_valuation_metrics(self, symbol: str, days: int = 250) -> dict[str, Any]:
        return await self._provider_facade.get_valuation_metrics(symbol, days=days)

    async def get_us_valuation_metrics(self, symbol: str) -> dict[str, Any]:
        return await self._provider_facade.get_us_valuation_metrics(symbol)


def get_fundamental_capability_service(runtime: RuntimeContext) -> FundamentalCapabilityService:
    return FundamentalCapabilityService(runtime)
