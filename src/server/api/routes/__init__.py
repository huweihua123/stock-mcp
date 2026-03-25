# src/server/api/routes/__init__.py
"""API routes."""

from .market_data import router as market_data_router
from .filings import router as filings_router
from .news import router as news_router
from .fundamental import router as fundamental_router
from .money_flow import router as money_flow_router
from .code_export import router as code_export_router

__all__ = [
    "market_data_router",
    "filings_router",
    "news_router",
    "fundamental_router",
    "money_flow_router",
    "code_export_router",
]
