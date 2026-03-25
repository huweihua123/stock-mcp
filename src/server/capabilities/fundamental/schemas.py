from __future__ import annotations

from pydantic import BaseModel


class FundamentalSymbolRequest(BaseModel):
    symbol: str
