from __future__ import annotations

from pydantic import BaseModel


class StockMoneyFlowQuery(BaseModel):
    symbol: str
    days: int = 20
