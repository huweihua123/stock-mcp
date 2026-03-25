from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.server.runtime.models import RuntimeContext


def parse_period_to_days(period: str) -> int:
    if period.endswith("d"):
        return int(period[:-1])
    if period.endswith("m"):
        return int(period[:-1]) * 30
    if period.endswith("y"):
        return int(period[:-1]) * 365
    return 30


class MarketCapabilityService:
    def __init__(self, runtime: RuntimeContext):
        self._runtime = runtime

    @property
    def _provider_facade(self):
        return self._runtime.provider_facade

    async def get_multiple_prices(self, tickers: list[str]) -> dict[str, Any]:
        return await self._provider_facade.get_multiple_prices(tickers)

    async def get_real_time_price(self, symbol: str) -> dict[str, Any] | None:
        price = await self._provider_facade.get_real_time_price(symbol)
        return price.to_dict() if price and hasattr(price, "to_dict") else price

    async def get_asset_info(self, symbol: str) -> dict[str, Any] | None:
        asset = await self._provider_facade.get_asset_info(symbol)
        if asset and hasattr(asset, "model_dump"):
            return asset.model_dump(mode="json")
        return asset

    async def get_historical_prices(self, symbol: str, period: str, interval: str) -> dict[str, Any]:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=parse_period_to_days(period))
        prices = await self._provider_facade.get_historical_prices(
            symbol,
            start_date,
            end_date,
            interval,
        )
        rows = [price.to_dict() if hasattr(price, "to_dict") else price for price in prices]
        return {
            "symbol": symbol,
            "period": period,
            "interval": interval,
            "count": len(rows),
            "data": rows,
        }

    async def get_market_report(self, symbol: str) -> dict[str, Any]:
        info = await self.get_asset_info(symbol)
        price = await self.get_real_time_price(symbol)
        return {
            "symbol": symbol,
            "info": info,
            "price": price,
            "timestamp": datetime.now().isoformat(),
        }


def get_market_capability_service(runtime: RuntimeContext) -> MarketCapabilityService:
    return MarketCapabilityService(runtime)
