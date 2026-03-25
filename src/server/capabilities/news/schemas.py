from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    publish_time: str | None = None
    source: str | None = None


class StockNewsResponse(BaseModel):
    ticker: str
    source: str
    news: list[NewsItem]
    timestamp: str


class NewsSearchResponse(BaseModel):
    query: str
    items: list[NewsItem]


class NewsSentimentHeadline(BaseModel):
    title: str
    url: str = ""
    source: str = ""
    sentiment: Literal["positive", "neutral", "negative"]
    confidence: float = Field(ge=0.0, le=1.0)


class NewsSentimentSummary(BaseModel):
    ticker: str
    days_back: int
    overall_sentiment: Literal["positive", "neutral", "negative"]
    composite_score: float
    avg_confidence: float
    counts: dict[str, int]
    headlines: list[NewsSentimentHeadline]
    note: str
