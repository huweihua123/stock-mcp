# src/server/mcp/registry.py
"""Central MCP tool registry.

Keeps tool registration and metadata in one place to avoid drift between
server setup, docs, and helper functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from fastmcp import FastMCP

from src.server.mcp.tools.fundamental_tools import register_fundamental_tools
from src.server.mcp.tools.asset_tools import register_asset_tools
from src.server.mcp.tools.technical_tools import register_technical_tools
from src.server.mcp.tools.money_flow_tools import register_money_flow_tools
from src.server.mcp.tools.filings_tools import register_filings_tools
from src.server.mcp.tools.trade_tools import register_trade_tools
from src.server.mcp.tools.chunking_tools import register_chunking_tools
from src.server.mcp.tools.news_tools import register_news_tools
from src.server.mcp.tools.us_fundamental_tools import register_us_fundamental_tools
from src.server.mcp.tools.us_technical_tools import register_us_technical_tools
from src.server.mcp.tools.us_sector_tools import register_us_sector_tools
from src.server.mcp.tools.us_macro_tools import register_us_macro_tools


@dataclass(frozen=True)
class ToolGroup:
    name: str
    register: Callable[[FastMCP], None]
    enabled: bool
    description: str
    count: int


TOOL_GROUPS: List[ToolGroup] = [
    ToolGroup(
        name="fundamental",
        register=register_fundamental_tools,
        enabled=True,
        description="基本面分析",
        count=6,
    ),
    ToolGroup(
        name="asset",
        register=register_asset_tools,
        enabled=True,
        description="资产搜索与管理",
        count=5,
    ),
    ToolGroup(
        name="technical",
        register=register_technical_tools,
        enabled=True,
        description="技术分析",
        count=4,
    ),
    ToolGroup(
        name="money-flow",
        register=register_money_flow_tools,
        enabled=True,
        description="资金流向",
        count=15,
    ),
    ToolGroup(
        name="filings",
        register=register_filings_tools,
        enabled=False,
        description="公告文件",
        count=4,
    ),
    ToolGroup(
        name="trade",
        register=register_trade_tools,
        enabled=False,
        description="交易执行",
        count=2,
    ),
    ToolGroup(
        name="chunking",
        register=register_chunking_tools,
        enabled=False,
        description="文档切片",
        count=1,
    ),
    ToolGroup(
        name="news",
        register=register_news_tools,
        enabled=False,
        description="新闻与检索",
        count=2,
    ),
    # ---- 美股工具集 (ValueCell 竞品对齐) ----
    ToolGroup(
        name="us-fundamental",
        register=register_us_fundamental_tools,
        enabled=True,
        description="美股基本面 (EPS历史/现金流/估值/机构持仓)",
        count=4,
    ),
    ToolGroup(
        name="us-technical",
        register=register_us_technical_tools,
        enabled=True,
        description="美股技术分析 (指标/量价/K线/综合摘要)",
        count=4,
    ),
    ToolGroup(
        name="us-sector",
        register=register_us_sector_tools,
        enabled=True,
        description="美股行业ETF分析",
        count=1,
    ),
    ToolGroup(
        name="us-macro",
        register=register_us_macro_tools,
        enabled=True,
        description="美股宏观分析 (GDP/通胀就业/利率)",
        count=3,
    ),
]


def register_tools(mcp: FastMCP) -> None:
    """Register all enabled tools with the MCP instance."""
    for group in TOOL_GROUPS:
        if group.enabled:
            group.register(mcp)


def get_tool_group_info() -> Dict[str, Dict[str, str | int | bool]]:
    """Return tool group metadata for server info endpoints."""
    info: Dict[str, Dict[str, str | int | bool]] = {}
    for group in TOOL_GROUPS:
        info[group.name] = {
            "count": group.count if group.enabled else 0,
            "description": group.description,
            "enabled": group.enabled,
        }
    return info


def get_enabled_tool_count() -> int:
    return sum(group.count for group in TOOL_GROUPS if group.enabled)
