from __future__ import annotations

from src.server.capabilities.market.http import build_router
from src.server.capabilities.market.mcp import register_market_tools
from src.server.providers.contracts import HISTORICAL_PRICE, REALTIME_PRICE
from src.server.runtime.models import CapabilityPlugin


plugin = CapabilityPlugin(
    name="market",
    description="Market data capability",
    required_contracts=frozenset({REALTIME_PRICE, HISTORICAL_PRICE}),
    http_routers=(build_router,),
    mcp_registrars=(register_market_tools,),
)
