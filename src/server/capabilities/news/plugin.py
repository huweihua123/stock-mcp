from __future__ import annotations

from src.server.capabilities.news.http import build_router
from src.server.capabilities.news.mcp import register_news_tools
from src.server.providers.contracts import NEWS_SEARCH
from src.server.runtime.models import CapabilityPlugin


plugin = CapabilityPlugin(
    name="news",
    description="Tavily-backed unified news search capability",
    required_contracts=frozenset({NEWS_SEARCH}),
    http_routers=(build_router,),
    mcp_registrars=(register_news_tools,),
)
