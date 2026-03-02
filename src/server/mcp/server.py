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

from contextlib import asynccontextmanager
from fastmcp import FastMCP
from src.server.core.bootstrap import init_adapters
from src.server.utils.logger import logger
from src.server.mcp.registry import (
    register_tools,
    get_tool_group_info,
    get_enabled_tool_count,
)


@asynccontextmanager
async def mcp_lifespan(mcp: FastMCP):
    """MCP server lifespan - manages Redis connection and adapters."""
    # Startup
    logger.info("🚀 Starting MCP server")

    # Adapters are initialized by FastAPI app lifespan.
    yield

    # Shutdown
    logger.info("🛑 Shutting down MCP server")


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

    # Register all tool groups via registry
    logger.info("📦 Registering tool groups...")
    register_tools(mcp)

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
    from src.server.mcp.tools.fundamental_tools import register_fundamental_tools
    from src.server.mcp.tools.news_tools import register_news_tools
    # from src.server.mcp.tools.research_tools import register_research_tools
    from src.server.mcp.tools.asset_tools import register_asset_tools
    from src.server.mcp.tools.technical_tools import register_technical_tools
    from src.server.mcp.tools.filings_tools import register_filings_tools
    from src.server.mcp.tools.trade_tools import register_trade_tools

    server_name = name or "stock-tool-mcp-filtered"

    mcp = FastMCP(
        name=server_name,
        version="1.0.0",
        lifespan=mcp_lifespan,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )

    # Register all tools - FastMCP will filter based on tags
    register_fundamental_tools(mcp)
    # register_news_tools(mcp)
    # register_research_tools(mcp)
    register_asset_tools(mcp)
    register_technical_tools(mcp)
    # register_filings_tools(mcp)
    # register_trade_tools(mcp)

    logger.info(f"✅ Filtered MCP server '{server_name}' created with tag filters")
    if include_tags:
        logger.info(f"   Include tags: {include_tags}")
    if exclude_tags:
        logger.info(f"   Exclude tags: {exclude_tags}")

    return mcp


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
            "get_sector_trend",
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
    }

    return tools_by_tag.get(tag, [])


def get_server_info() -> dict:
    """Get MCP server information."""
    return {
        "name": "Stock Tool MCP",
        "version": "1.0.0",
        "total_tools": get_enabled_tool_count(),
        "tags": get_tool_group_info(),
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
        "fundamental",
        "asset",
        "technical",
        "filings",
        "trade",
        "money-flow",
        "chunking",
        "us-macro",
        "core",
        "extended",
    }
