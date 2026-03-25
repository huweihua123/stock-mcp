"""Compatibility wrapper for the new MCP transport."""

from src.server.transports.mcp.server import (
    create_filtered_mcp_server,
    create_mcp_server,
    get_all_tags,
    get_enabled_tool_count,
    get_server_info,
    get_tool_group_info,
    get_tools_by_tag,
)

__all__ = [
    "create_filtered_mcp_server",
    "create_mcp_server",
    "get_all_tags",
    "get_enabled_tool_count",
    "get_server_info",
    "get_tool_group_info",
    "get_tools_by_tag",
]
