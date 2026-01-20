# src/server/mcp/server.py
"""FastMCP server creation.

This module provides the main MCP server instance with all stock tools.
All tools are tagged for easy filtering by different agents.

Architecture:
- Single MCP instance with all tools
- Tools organized by tags for filtering
- Each agent can filter tools using include_tags/exclude_tags

Tags:
- news: 新闻相关工具 (1 tool)
- research: 研究相关工具 (6 tools)
- market: 市场数据工具 (2 tools)
- fundamental: 基本面分析工具 (1 tool)
- asset: 资产配置工具 (5 tools)
- technical: 技术分析工具 (5 tools)
- core: 核心工具标签
- extended: 扩展工具标签
"""

from contextlib import asynccontextmanager
from fastmcp import FastMCP
from src.server.core.dependencies import Container
from src.server.utils.logger import logger


@asynccontextmanager
async def mcp_lifespan(mcp: FastMCP):
    """MCP server lifespan - manages Redis connection and adapters."""
    # Startup
    logger.info("🚀 Starting MCP server")

    # Initialize Redis
    redis = Container.redis()
    await redis.connect()
    logger.info("✅ Redis connection established")

    # Get config
    config = Container.config()

    # Initialize Tushare connection (only if enabled)
    tushare_available = False
    if config.tushare.is_available:
        tushare = Container.tushare()
        tushare_available = await tushare.connect()
        if tushare_available:
            logger.info("✅ Tushare connection established")
        else:
            logger.warning("⚠️ Tushare connection failed - will use fallback adapters")
    else:
        logger.info(
            "ℹ️  Tushare disabled (set TUSHARE_ENABLED=True and provide token to enable)"
        )

    # Initialize FinnHub connection (only if enabled)
    finnhub_available = False
    if config.finnhub.is_available:
        finnhub = Container.finnhub()
        await finnhub.connect()
        finnhub_available = True
        logger.info("✅ FinnHub connection established")
    else:
        logger.info(
            "ℹ️  FinnHub disabled (set FINNHUB_ENABLED=True and provide API key to enable)"
        )

    # Initialize Baostock connection
    baostock = Container.baostock()
    await baostock.connect()
    logger.info("✅ Baostock connection established")

    # Register adapters
    logger.info("📦 Registering data adapters...")
    adapter_manager = Container.adapter_manager()

    # A股数据源 - 按优先级注册
    if tushare_available:
        adapter_manager.register_adapter(Container.tushare_adapter())
    adapter_manager.register_adapter(Container.akshare_adapter())
    adapter_manager.register_adapter(Container.baostock_adapter())

    # 加密货币数据源
    adapter_manager.register_adapter(Container.crypto_adapter())
    adapter_manager.register_adapter(Container.ccxt_adapter())

    # 美股数据源
    adapter_manager.register_adapter(Container.yahoo_adapter())
    if finnhub_available:
        adapter_manager.register_adapter(Container.finnhub_adapter())

    logger.info(
        f"✅ All adapters registered (A-share: {'Tushare > ' if tushare_available else ''}Akshare > Baostock)"
    )

    yield

    # Shutdown
    logger.info("🛑 Shutting down MCP server")


def create_mcp_server() -> FastMCP:
    """Create the main MCP server with all tools.

    All 20 tools are registered with appropriate tags for filtering:
    - [news] 1 tool: 新闻和情绪分析
    - [research] 6 tools: 深度研究和公告
    - [market] 2 tools: 市场数据
    - [fundamental] 1 tool: 基本面分析
    - [asset] 5 tools: 资产搜索与管理
    - [technical] 5 tools: 技术分析

    Returns:
        FastMCP: MCP server instance with all tools and tags
    """
    # Create MCP instance with lifespan
    mcp = FastMCP(name="stock-tool-mcp", version="1.0.0", lifespan=mcp_lifespan)

    # Register all tool groups with their respective tags
    logger.info("📦 Registering tool groups...")

    # Import all tool registration functions
    # Import all tool registration functions
    from src.server.mcp.tools.fundamental_tools import register_fundamental_tools
    from src.server.mcp.tools.news_tools import register_news_tools
    from src.server.mcp.tools.research_tools import register_research_tools
    from src.server.mcp.tools.asset_tools import register_asset_tools
    from src.server.mcp.tools.technical_tools import register_technical_tools
    from src.server.mcp.tools.filings_tools import register_filings_tools
    from src.server.mcp.tools.trade_tools import register_trade_tools
    from src.server.mcp.tools.chunking_tools import register_chunking_tools
    from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

    # Register core tools
    register_fundamental_tools(mcp)
    logger.info("  ✓ Fundamental tools registered (1 tool)")

    register_news_tools(mcp)
    logger.info("  ✓ News tools registered (1 tool)")

    register_research_tools(mcp)
    logger.info("  ✓ Research tools registered (1 tool)")

    # Register extended tools
    register_asset_tools(mcp)
    logger.info("  ✓ Asset tools registered (5 tools)")

    register_technical_tools(mcp)
    logger.info("  ✓ Technical tools registered (5 tools)")

    register_filings_tools(mcp)
    logger.info("  ✓ Filings tools registered (5 tools)")

    register_trade_tools(mcp)
    logger.info("  ✓ Trade tools registered (2 tools)")

    register_money_flow_tools(mcp)
    logger.info("  ✓ Money flow tools registered (4 tools)")  # Updated count

    register_chunking_tools(mcp)
    logger.info("  ✓ Chunking tools registered (1 tool)")

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
    from src.server.mcp.tools.research_tools import register_research_tools
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
    register_news_tools(mcp)
    register_research_tools(mcp)
    register_asset_tools(mcp)
    register_technical_tools(mcp)
    register_filings_tools(mcp)
    register_trade_tools(mcp)

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
        "news": [
            "get_latest_news",
        ],
        "research": [
            "perform_deep_research",
        ],
        "fundamental": [
            "get_financial_report",
        ],
        "asset": [
            "search_assets",
            "get_asset_info",
            "get_real_time_price",
            "get_multiple_prices",
            "get_historical_prices",
            "get_market_report",
        ],
        "technical": [
            "calculate_technical_indicators",
            "generate_trading_signal",
            "analyze_price_patterns",
            "detect_support_resistance",
            "calculate_volatility",
        ],
        "filings": [
            "fetch_periodic_sec_filings",
            "fetch_event_sec_filings",
            "fetch_ashare_filings",
        ],
        "trade": [
            "execute_order",
            "get_account_balance",
        ],
        "chunking": [
            "get_document_chunks",
        ],
    }

    return tools_by_tag.get(tag, [])


def get_server_info() -> dict:
    """Get MCP server information."""
    return {
        "name": "Stock Tool MCP",
        "version": "1.0.0",
        "version": "1.0.0",
        "total_tools": 22,
        "tags": {
            "news": {"count": 1, "description": "新闻和情绪分析"},
            "research": {"count": 1, "description": "深度研究报告"},
            "fundamental": {"count": 1, "description": "基本面分析"},
            "asset": {"count": 6, "description": "资产搜索与管理"},
            "technical": {"count": 5, "description": "技术分析"},
            "filings": {"count": 5, "description": "公告文件"},
            "trade": {"count": 2, "description": "交易执行"},
        },
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
        "core",
        "extended",
    }
