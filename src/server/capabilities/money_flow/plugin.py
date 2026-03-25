from __future__ import annotations

from src.server.capabilities.money_flow.http import build_router
from src.server.capabilities.money_flow.mcp import register_money_flow_tools
from src.server.providers.contracts import MONEY_FLOW
from src.server.runtime.models import CapabilityPlugin

plugin = CapabilityPlugin(
    name="money_flow",
    description="Money flow and chip distribution capability",
    required_contracts=frozenset({MONEY_FLOW}),
    http_routers=(build_router,),
    mcp_registrars=(register_money_flow_tools,),
)
