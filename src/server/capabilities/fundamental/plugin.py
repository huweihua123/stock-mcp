from __future__ import annotations

from src.server.capabilities.fundamental.http import build_router
from src.server.capabilities.fundamental.mcp import register_fundamental_tools
from src.server.providers.contracts import FUNDAMENTAL
from src.server.runtime.models import CapabilityPlugin

plugin = CapabilityPlugin(
    name="fundamental",
    description="Fundamental analysis and valuation capabilities",
    required_contracts=frozenset({FUNDAMENTAL}),
    http_routers=(build_router,),
    mcp_registrars=(register_fundamental_tools,),
)
