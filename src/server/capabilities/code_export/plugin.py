from __future__ import annotations

from src.server.capabilities.code_export.http import build_router
from src.server.capabilities.code_export.mcp import register_code_export_tools
from src.server.providers.contracts import CODE_EXPORT
from src.server.runtime.models import CapabilityPlugin

plugin = CapabilityPlugin(
    name="code_export",
    description="CSV/JSON export for code execution workflows",
    required_contracts=frozenset({CODE_EXPORT}),
    http_routers=(build_router,),
    mcp_registrars=(register_code_export_tools,),
)
