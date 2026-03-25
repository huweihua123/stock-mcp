# src/server/api/models/requests.py
"""
API request models using Pydantic for validation.

Symbol/Ticker 格式说明:
- 美股: NASDAQ:AAPL, NYSE:TSLA
- A股: SSE:600519 (上交所), SZSE:000001 (深交所)
- 加密货币: CRYPTO:BTC, CRYPTO:ETH
"""

from pydantic import BaseModel, Field, field_validator
from typing import Any, List, Optional


class GetMultiplePricesRequest(BaseModel):
    """批量获取价格请求模型
    
    用于验证批量价格查询的请求参数,确保 ticker 格式正确。
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    tickers: List[str] = Field(
        ..., 
        description="资产代码列表(支持带前缀或无前缀): 美股NASDAQ:xx/NYSE:xx 或 AAPL, A股SSE:xx/SZSE:xx 或 600519, 加密货币CRYPTO:xx 或 BTC",
        min_length=1,
        max_length=100,
        examples=[["CRYPTO:BTC", "NASDAQ:AAPL", "SSE:600519"]]
    )
    
    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, v):
        """验证 ticker 格式必须为 PREFIX:SYMBOL"""
        for ticker in v:
            if ":" in ticker:
                parts = ticker.split(":")
                if len(parts) != 2 or not all(part.strip() for part in parts):
                    raise ValueError(f"Invalid ticker format: '{ticker}'")
            else:
                if not ticker or not ticker.strip():
                    raise ValueError(f"Invalid ticker format: '{ticker}'")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "tickers": ["CRYPTO:BTC", "CRYPTO:ETH", "NASDAQ:AAPL"]
            }
        }


class CalculateTechnicalIndicatorsRequest(BaseModel):
    """计算技术指标请求模型
    
    用于验证技术指标计算的请求参数。
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    symbol: str = Field(
        ..., 
        description="资产代码(支持带前缀或无前缀): 美股NASDAQ:xx 或 AAPL, A股SSE:xx/SZSE:xx 或 600519, 加密货币CRYPTO:xx 或 BTC",
        examples=["CRYPTO:BTC", "NASDAQ:AAPL", "SSE:600519", "AAPL", "600519"]
    )
    period: str = Field(
        default="30d",
        description="数据周期 (支持格式: 30d, 90d, 1y)",
        pattern=r"^\d+[dmy]$",
        examples=["30d", "90d", "1y"]
    )
    interval: str = Field(
        default="1d",
        description="K线间隔 (支持: 1d=日线, 1h=小时线, 15m=15分钟线)",
        examples=["1d", "1h", "15m", "5m"]
    )
    
    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v):
        """验证 symbol 格式必须为 PREFIX:SYMBOL"""
        if ":" in v:
            parts = v.split(":")
            if len(parts) != 2 or not all(part.strip() for part in parts):
                raise ValueError(f"Invalid symbol format: '{v}'")
        else:
            if not v or not v.strip():
                raise ValueError(f"Invalid symbol format: '{v}'")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "CRYPTO:BTC",
                "period": "30d",
                "interval": "1d"
            }
        }


# ============================================================
# 新增请求模型
# ============================================================

class GetHistoricalPricesRequest(BaseModel):
    """
    获取历史价格/K线数据请求模型
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    symbol: str = Field(
        ..., 
        description="资产代码(必须带前缀): NASDAQ:AAPL, SSE:600519, CRYPTO:BTC等",
        examples=["CRYPTO:BTC", "NASDAQ:AAPL", "SSE:600519"]
    )
    period: str = Field(
        default="30d",
        description="时间周期 (如: 7d, 30d, 90d, 1y)",
        examples=["7d", "30d", "90d", "1y"]
    )
    interval: str = Field(
        default="1d",
        description="K线间隔 (如: 1d, 1h, 15m, 5m)",
        examples=["1d", "1h", "15m"]
    )


class GetAssetInfoRequest(BaseModel):
    """
    获取资产信息请求模型
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    symbol: str = Field(
        ..., 
        description="资产代码(必须带前缀): NASDAQ:AAPL, SSE:600519, CRYPTO:BTC等",
        examples=["NASDAQ:AAPL", "CRYPTO:BTC", "SSE:600519"]
    )


class GenerateTradingSignalRequest(BaseModel):
    """
    生成交易信号请求模型
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    symbol: str = Field(
        ..., 
        description="资产代码(必须带前缀): NASDAQ:AAPL, SSE:600519, CRYPTO:BTC等",
        examples=["CRYPTO:BTC", "NASDAQ:AAPL", "SSE:600519"]
    )
    period: str = Field(
        default="30d",
        description="分析周期",
        examples=["30d", "90d"]
    )
    interval: str = Field(
        default="1d",
        description="数据间隔",
        examples=["1d", "1h"]
    )


class CalculateSupportResistanceRequest(BaseModel):
    """
    计算支撑阻力位请求模型
    
    Symbol 格式: 美股(NASDAQ:AAPL), A股(SSE:600519), 加密货币(CRYPTO:BTC)
    """
    
    symbol: str = Field(
        ..., 
        description="资产代码(必须带前缀): NASDAQ:AAPL, SSE:600519, CRYPTO:BTC等",
        examples=["CRYPTO:BTC", "NASDAQ:AAPL"]
    )
    period: str = Field(
        default="90d",
        description="分析周期",
        examples=["30d", "90d", "1y"]
    )


class GetStockNewsRequest(BaseModel):
    """获取股票新闻请求模型"""
    
    symbol: str = Field(
        ..., 
        description="资产代码",
        examples=["NASDAQ:AAPL", "SSE:600519"]
    )
    days_back: int = Field(
        default=7,
        ge=1,
        le=30,
        description="回溯天数"
    )


class GetFundamentalReportRequest(BaseModel):
    """获取财务报告请求模型"""
    
    symbol: str = Field(
        ..., 
        description="股票代码",
        examples=["AAPL", "NASDAQ:AAPL", "600519"]
    )


class TushareCsvExportRequest(BaseModel):
    """Code runtime CSV export request for Tushare datasets."""

    api_name: str = Field(..., description="Tushare API name", examples=["index_daily", "daily", "stock_basic"])
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments forwarded to the selected Tushare API",
    )


class AlphaVantageJsonExportRequest(BaseModel):
    """Code runtime JSON export request for Alpha Vantage datasets."""

    function: str = Field(..., description="Alpha Vantage function name", examples=["TIME_SERIES_DAILY", "OVERVIEW"])
    symbol: str = Field(..., description="Ticker symbol", examples=["AAPL", "MSFT"])
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional Alpha Vantage query parameters",
    )


class CodeExportResponse(BaseModel):
    """MCP-shaped contract for code export endpoints."""

    content: list[dict[str, Any]] = Field(default_factory=list, description="MCP content blocks")
    structuredContent: dict[str, Any] = Field(
        default_factory=dict,
        description="MCP structuredContent payload",
    )
    isError: bool = Field(default=False, description="Whether the export failed")
