from __future__ import annotations

from src.server.capabilities.technical.http import build_router
from src.server.capabilities.technical.mcp import register_technical_tools
from src.server.providers.contracts import TECHNICAL_INDICATORS
from src.server.runtime.models import CapabilityPlugin


plugin = CapabilityPlugin(
    name="technical",
    description="Technical indicators and signal generation capability",
    required_contracts=frozenset({TECHNICAL_INDICATORS}),
    http_routers=(build_router,),
    mcp_registrars=(register_technical_tools,),
)
