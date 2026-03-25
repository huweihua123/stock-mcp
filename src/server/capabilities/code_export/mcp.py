from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from src.server.capabilities.code_export.service import get_code_export_capability_service
from src.server.runtime import get_runtime_context


def register_code_export_tools(mcp: FastMCP) -> None:
    runtime = get_runtime_context()
    service = get_code_export_capability_service(runtime)

    @mcp.tool(tags={"code-export-tushare"})
    async def tushare_fetch_to_csv(api_name: str, kwargs: dict[str, Any] | None = None) -> Any:
        return await service.export_tushare_csv(api_name, kwargs or {})

    @mcp.tool(tags={"code-export-alphavantage"})
    async def alphavantage_fetch_to_json(function: str, symbol: str, extra_params: dict[str, Any] | None = None) -> Any:
        return await service.export_alphavantage_json(function, symbol, extra_params or {})
