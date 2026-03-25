from __future__ import annotations

from pydantic import BaseModel


class CalculateTechnicalIndicatorsRequest(BaseModel):
    symbol: str
    period: str = "30d"
    interval: str = "1d"


class GenerateTradingSignalRequest(BaseModel):
    symbol: str
    period: str = "30d"
    interval: str = "1d"


class CalculateSupportResistanceRequest(BaseModel):
    symbol: str
    period: str = "90d"
