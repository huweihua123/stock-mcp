from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TushareCsvExportRequest(BaseModel):
    api_name: str = Field(..., description="Tushare API name")
    kwargs: dict[str, Any] = Field(default_factory=dict)


class AlphaVantageJsonExportRequest(BaseModel):
    function: str = Field(..., description="Alpha Vantage function name")
    symbol: str = Field(..., description="Ticker symbol")
    extra_params: dict[str, Any] = Field(default_factory=dict)


class CodeExportResponse(BaseModel):
    content: list[dict[str, Any]] = Field(default_factory=list)
    structuredContent: dict[str, Any] = Field(default_factory=dict)
    isError: bool = Field(default=False)
