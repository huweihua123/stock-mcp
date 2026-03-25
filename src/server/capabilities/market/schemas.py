from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GetMultiplePricesRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=100)


class GetHistoricalPricesRequest(BaseModel):
    symbol: str
    period: str = "30d"
    interval: str = "1d"


class MarketReportResponse(BaseModel):
    symbol: str
    info: dict[str, Any] | None = None
    price: dict[str, Any] | None = None
    timestamp: datetime
