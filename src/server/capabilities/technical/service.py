from __future__ import annotations

from typing import Any

from src.server.runtime.models import RuntimeContext


class TechnicalCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime

    @property
    def _service(self):
        return self._runtime.container.technical_service()

    async def calculate_technical_indicators(
        self, symbol: str, period: str = "30d", interval: str = "1d", limit: int | None = None
    ) -> dict[str, Any]:
        return await self._service.calculate_indicators(symbol=symbol, period=period, interval=interval, limit=limit)

    async def generate_trading_signal(self, symbol: str, period: str = "90d", interval: str = "1d") -> dict[str, Any]:
        return await self._service.generate_trading_signal(symbol=symbol, period=period, interval=interval)

    async def calculate_support_resistance(self, symbol: str, period: str = "90d") -> dict[str, Any]:
        return await self._service.calculate_support_resistance(symbol=symbol, period=period)


def get_technical_capability_service(runtime: RuntimeContext) -> TechnicalCapabilityService:
    return TechnicalCapabilityService(runtime)
