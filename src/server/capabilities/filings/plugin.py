from __future__ import annotations

from src.server.capabilities.filings.http import build_router
from src.server.capabilities.filings.mcp import register_filings_tools
from src.server.providers.contracts import FILINGS
from src.server.runtime.models import CapabilityPlugin

plugin = CapabilityPlugin(
    name="filings",
    description="US SEC and A-share filings capabilities",
    required_contracts=frozenset({FILINGS}),
    http_routers=(build_router,),
    mcp_registrars=(register_filings_tools,),
)
