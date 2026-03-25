from __future__ import annotations

from typing import Any

from src.server.runtime.models import RuntimeContext


class MoneyFlowCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime

    @property
    def _provider_facade(self):
        return self._runtime.provider_facade

    async def get_money_flow(self, symbol: str, days: int = 20) -> dict[str, Any]:
        try:
            return await self._provider_facade.get_money_flow(symbol, days=days)
        except Exception as exc:
            return {"error": str(exc), "symbol": symbol, "variant": "money_flow"}

    async def get_north_bound_flow(self, days: int = 30) -> dict[str, Any]:
        try:
            return await self._provider_facade.get_north_bound_flow(days)
        except Exception as exc:
            return {"error": str(exc), "variant": "north_bound_flow"}

    async def get_chip_distribution(self, symbol: str, days: int = 30) -> dict[str, Any]:
        return await self._provider_facade.get_chip_distribution(symbol, days)


def get_money_flow_capability_service(runtime: RuntimeContext) -> MoneyFlowCapabilityService:
    return MoneyFlowCapabilityService(runtime)
