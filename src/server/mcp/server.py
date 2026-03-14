# src/server/mcp/server.py
"""FastMCP server creation.

This module provides the main MCP server instance with all stock tools.
All tools are tagged for easy filtering by different agents.

Architecture:
- Single MCP instance with all tools
- Tools organized by tags for filtering
- Each agent can filter tools using include_tags/exclude_tags
- stock-mcp 专注于股票数据获取，不包含新闻搜索和聚合逻辑

Tags:
- fundamental: 基本面分析工具 (1 tool)
- asset: 资产配置工具 (4 tools)  # get_market_report 已禁用
- technical: 技术分析工具 (4 tools)  # generate_trading_signal 已禁用
- money-flow: 资金流向工具 (4 tools)
- filings: 公告文档工具 (5 tools)
- trade: 交易工具 (2 tools)
- core: 核心工具标签
- extended: 扩展工具标签

Disabled (建议使用专门的 MCP 服务):
- news: 新闻搜索 → 使用 Tavily/Exa MCP
- research: 聚合研究 → 由 Java Agent 层实现
- generate_trading_signal: 交易信号 → LLM 应基于技术指标自行判断
- get_market_report: 市场报告 → 聚合工具，由 Agent 层组合调用
"""

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any
from fastmcp import FastMCP
from src.server.core.bootstrap import init_adapters
from src.server.config.settings import get_settings
from src.server.utils.logger import logger
from src.server.mcp.registry import (
    register_tools,
    get_tool_group_info,
    get_enabled_tool_count,
)
from src.server.mcp.capability_contract import (
    get_capability_contract_overview,
    install_capability_contract,
    register_capability_tools,
)
from src.server.mcp.envelope import (
    normalize_tool_exception as _normalize_tool_exception_impl,
    normalize_tool_result as _normalize_tool_result_impl,
)


@asynccontextmanager
async def mcp_lifespan(mcp: FastMCP):
    """MCP server lifespan - manages Redis connection and adapters."""
    # Startup
    logger.info("🚀 Starting MCP server")
    # Important: stdio / standalone MCP mode does not go through FastAPI lifespan.
    # init_adapters is idempotent, so calling it here is safe for all modes.
    await init_adapters()
    yield

    # Shutdown
    logger.info("🛑 Shutting down MCP server")


def _normalize_tool_result(tool_name: str, result: Any) -> Any:
    """Normalize all tool outputs into strict envelope format."""
    return _normalize_tool_result_impl(tool_name, result)


def _normalize_tool_exception(
    tool_name: str, error: Exception, *, timeout_seconds: float | None = None
) -> Any:
    """Normalize tool exceptions into strict failed envelopes."""
    return _normalize_tool_exception_impl(
        tool_name, error, timeout_seconds=timeout_seconds
    )


def _install_tool_guard(mcp: FastMCP, tool_timeout_seconds: float) -> None:
    """Inject global timeout + error-summary normalization for all MCP tools."""
    original_tool = mcp.tool

    def guarded_tool(*tool_args, **tool_kwargs):
        base_decorator = original_tool(*tool_args, **tool_kwargs)

        def decorator(func):
            @wraps(func)
            async def wrapped(*args, **kwargs):
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs), timeout=tool_timeout_seconds
                    )
                    return _normalize_tool_result(func.__name__, result)
                except asyncio.TimeoutError:
                    logger.error(
                        f"工具 {func.__name__} 超时: 超过 {tool_timeout_seconds:.1f}s 全局限制"
                    )
                    return _normalize_tool_exception(
                        func.__name__,
                        asyncio.TimeoutError(),
                        timeout_seconds=tool_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("工具执行异常", tool_name=func.__name__)
                    return _normalize_tool_exception(func.__name__, exc)

            return base_decorator(wrapped)

        return decorator

    mcp.tool = guarded_tool


def create_mcp_server() -> FastMCP:
    """Create the main MCP server with all tools.

    Active tools (16 total):
    - [fundamental] 1 tool: 基本面分析
    - [asset] 4 tools: 资产搜索与管理 (get_market_report 已禁用)
    - [technical] 4 tools: 技术分析 (generate_trading_signal 已禁用)
    - [money-flow] 4 tools: 资金流向
    - [filings] 5 tools: 公告文档
    - [trade] 2 tools: 交易工具
    - [chunking] 1 tool: 文档切片

    Disabled tools:
    - [news] → 建议使用 Tavily/Exa MCP
    - [research] → 聚合逻辑在 Java Agent 层实现
    - generate_trading_signal → LLM 应基于技术指标自行判断
    - get_market_report → 聚合工具，由 Agent 层组合调用

    Returns:
        FastMCP: MCP server instance with all tools and tags
    """
    # Create MCP instance with lifespan
    mcp = FastMCP(name="stock-tool-mcp", version="1.0.0", lifespan=mcp_lifespan)

    settings = get_settings()
    _install_tool_guard(mcp, settings.timeout.mcp_tool_seconds)

    # Register all tool groups via registry
    logger.info("📦 Registering tool groups...")
    register_tools(mcp)
    contract_state: dict[str, Any] = {}
    register_capability_tools(mcp, contract_state)
    contract_state["catalog"] = install_capability_contract(mcp, strict=True)

    logger.info("✅ MCP server created with all tools")

    # Print simple startup banner
    logger.info("\n" + "=" * 70)
    logger.info("🚀 Stock Tool MCP Server")
    logger.info("=" * 70)
    logger.info("\n📋 Server Information:")
    logger.info(f"   Name: {mcp.name}")
    logger.info(f"   Version: {mcp.version}")
    logger.info("   Protocol: MCP (Model Context Protocol)")
    logger.info("   Transport: Streamable HTTP")
    logger.info("\n✅ Server ready and waiting for connections...")
    logger.info("=" * 70 + "\n")
    
    return mcp


def create_filtered_mcp_server(
    include_tags: set[str] | None = None,
    exclude_tags: set[str] | None = None,
    name: str | None = None,
) -> FastMCP:
    """Create a filtered MCP server with specific tags.

    This allows creating specialized servers programmatically:

    Examples:
        # Create a server with only news tools
        news_server = create_filtered_mcp_server(include_tags={"news"})

        # Create a server without extended tools
        core_server = create_filtered_mcp_server(exclude_tags={"extended"})

        # Create a market-focused server
        market_server = create_filtered_mcp_server(
            include_tags={"market"},
            name="stock-market-mcp"
        )

    Args:
        include_tags: Only include tools with these tags
        exclude_tags: Exclude tools with these tags
        name: Custom server name (defaults to "stock-tool-mcp-filtered")

    Returns:
        FastMCP: Filtered MCP server instance
    """
    server_name = name or "stock-tool-mcp-filtered"

    mcp = FastMCP(
        name=server_name,
        version="1.0.0",
        lifespan=mcp_lifespan,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    settings = get_settings()
    _install_tool_guard(mcp, settings.timeout.mcp_tool_seconds)

    # Register all enabled groups, then filter by tags/capability tags.
    register_tools(mcp)
    contract_state: dict[str, Any] = {}
    register_capability_tools(mcp, contract_state)
    contract_state["catalog"] = install_capability_contract(mcp, strict=True)

    logger.info(f"✅ Filtered MCP server '{server_name}' created with tag filters")
    if include_tags:
        logger.info(f"   Include tags: {include_tags}")
    if exclude_tags:
        logger.info(f"   Exclude tags: {exclude_tags}")

    return mcp


def create_capability_filtered_mcp_server(
    include_capabilities: set[str] | None = None,
    exclude_capabilities: set[str] | None = None,
    name: str | None = None,
) -> FastMCP:
    """Create filtered server by capability ids (not tool names)."""
    include_tags = (
        {f"cap:{cap.strip()}" for cap in include_capabilities if cap.strip()}
        if include_capabilities
        else None
    )
    exclude_tags = (
        {f"cap:{cap.strip()}" for cap in exclude_capabilities if cap.strip()}
        if exclude_capabilities
        else None
    )
    server_name = name or "stock-tool-mcp-capability-filtered"
    return create_filtered_mcp_server(
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        name=server_name,
    )


def get_tools_by_tag(tag: str) -> list[str]:
    """Get list of tool names by tag.

    Args:
        tag: Tool tag (news, research, market, fundamental, asset, technical)

    Returns:
        List of tool names for the specified tag
    """
    tools_by_tag = {
        "news": [],
        "research": [],
        "contract": ["list_capabilities"],
        "meta": ["list_capabilities"],
        "fundamental": [
            "get_financial_reports",
            "get_dividend_info",
            "get_forecast_info",
            "get_mainbz_info",
            "get_shareholder_info",
        ],
        "money-flow": [
            "get_money_flow",
            "get_north_bound_flow",
            "get_chip_distribution",
            "get_money_supply",
            "get_inflation_data",
            "get_pmi_data",
            "get_gdp_data",
            "get_social_financing",
            "get_interest_rates",
            "get_market_liquidity",
            "get_market_money_flow",
            "resolve_sector",
            "get_sector_trend",
            "get_sector_money_flow_history",
            "get_sector_valuation_metrics",
            "get_ggt_daily",
        ],
        "asset": [
            "get_kline_data",
        ],
        "technical": [
            "get_technical_indicators",
        ],
        "filings": [
            "fetch_periodic_sec_filings",
            "fetch_event_sec_filings",
            "fetch_ashare_filings",
            "process_document",
        ],
        "trade": ["execute_order", "get_account_balance"],
        "chunking": ["get_document_chunks"],
        "us-macro": [
            "get_us_economic_growth",
            "get_us_inflation_employment",
            "get_us_interest_rates",
        ],
        "sector-research": [
            "resolve_sector_scope",
            "build_sector_universe",
            "build_peer_benchmark_table",
            "build_sector_evidence_pack",
            "build_sector_structure_snapshot",
            "quality_gate_sector_report",
        ],
    }

    return tools_by_tag.get(tag, [])


def get_server_info() -> dict:
    """Get MCP server information."""
    tags = get_tool_group_info()
    tags["contract"] = {
        "count": 1,
        "description": "能力契约目录与版本元数据",
        "enabled": True,
    }
    tags["meta"] = {
        "count": 1,
        "description": "协议层元信息工具",
        "enabled": True,
    }
    return {
        "name": "Stock Tool MCP",
        "version": "1.0.0",
        "total_tools": get_enabled_tool_count() + 1,
        "capability_contract": get_capability_contract_overview(),
        "tags": tags,
        "endpoint": "http://localhost:9898/",
    }


def get_all_tags() -> set[str]:
    """Get all available tags in the server.

    Returns:
        Set of all tag names
    """
    return {
        "news",
        "research",
        "contract",
        "meta",
        "fundamental",
        "asset",
        "technical",
        "filings",
        "trade",
        "money-flow",
        "chunking",
        "us-macro",
        "sector-research",
        "core",
        "extended",
    }
