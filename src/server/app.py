"""
Author: weihua hu
Date: 2025-11-21 22:36:52
LastEditTime: 2025-11-22 19:04:47
LastEditors: weihua hu
Description:
"""

# src/server/app.py
"""
Main MCP server application with Nacos auto-registration.
"""

from contextlib import asynccontextmanager

import structlog

from nacos_mcp_wrapper.server.nacos_mcp import NacosMCP
from nacos_mcp_wrapper.server.nacos_settings import NacosSettings

from src.server.config.settings import get_settings
from src.server.core.dependencies import Container

# Apply monkey patch for MCP initialization issue
# See: https://github.com/modelcontextprotocol/python-sdk/issues/423
from src.server.mcp import monkey_patch  # noqa: F401

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def mcp_lifespan(mcp: NacosMCP):
    """MCP server lifespan - manages cleanup on shutdown.

    Note: Dependencies are pre-initialized in main() before server starts,
    so this only handles cleanup.
    """
    # Dependencies already initialized in main()
    yield

    # Shutdown
    logger.info("🛑 Shutting down MCP server")
    redis = Container.redis()
    await redis.disconnect()
    logger.info("✅ Redis connection closed")


def create_nacos_mcp_server() -> NacosMCP:
    """Create Nacos MCP server with all tools registered.

    Returns:
        NacosMCP instance with all tools
    """
    settings = get_settings()

    # Nacos 配置
    nacos_settings = NacosSettings()
    nacos_settings.SERVER_ADDR = settings.nacos.server_addr
    nacos_settings.NAMESPACE = settings.nacos.namespace
    nacos_settings.USERNAME = settings.nacos.username
    nacos_settings.PASSWORD = settings.nacos.password

    # 创建 Nacos MCP 实例
    # port is passed via **settings, not as a direct parameter
    mcp = NacosMCP(
        name=settings.nacos.mcp_server_name,
        nacos_settings=nacos_settings,
        version=settings.nacos.mcp_server_version,
        port=settings.nacos.mcp_server_port,  # Passed to FastMCP via **settings
        host="0.0.0.0",  # Bind to all interfaces
    )

    logger.info("📦 Registering tools to Nacos MCP server...")

    # 注册所有工具
    from src.server.mcp.tools.fundamental_tools import register_fundamental_tools

    register_fundamental_tools(mcp)
    logger.info("  ✓ Fundamental tools registered")

    from src.server.mcp.tools.news_tools import register_news_tools

    register_news_tools(mcp)
    logger.info("  ✓ News tools registered")

    from src.server.mcp.tools.research_tools import register_research_tools

    register_research_tools(mcp)
    logger.info("  ✓ Research tools registered")

    from src.server.mcp.tools.asset_tools import register_asset_tools

    register_asset_tools(mcp)
    logger.info("  ✓ Asset tools registered")

    from src.server.mcp.tools.technical_tools import register_technical_tools

    register_technical_tools(mcp)
    logger.info("  ✓ Technical tools registered")

    from src.server.mcp.tools.filings_tools import register_filings_tools

    register_filings_tools(mcp)
    logger.info("  ✓ Filings tools registered")

    from src.server.mcp.tools.trade_tools import register_trade_tools

    register_trade_tools(mcp)
    logger.info("  ✓ Trade tools registered")

    logger.info("✅ Nacos MCP server created with all tools")

    return mcp


def create_app():
    """Create and run the Nacos MCP server application.

    This will start the server with the configured transport protocol.
    """
    settings = get_settings()

    # 检查是否启用 Nacos 注册
    if not settings.nacos.register_enabled:
        logger.warning("🔧 Nacos register is disabled")
        return None

    logger.info("🔧 Nacos auto-register enabled")

    # 创建 Nacos MCP 服务器
    mcp = create_nacos_mcp_server()

    return mcp


async def initialize_dependencies():
    """Initialize all dependencies before starting the server."""
    logger.info("🔧 Pre-initializing dependencies...")

    # Initialize Redis
    redis = Container.redis()
    await redis.connect()
    logger.info("✅ Redis connection established")

    # Initialize Tushare connection
    tushare = Container.tushare()
    tushare_connected = await tushare.connect()
    if tushare_connected:
        logger.info("✅ Tushare connection established")
    else:
        logger.warning("⚠️ Tushare connection failed - will use fallback adapters")

    # Initialize FinnHub connection
    finnhub = Container.finnhub()
    await finnhub.connect()
    logger.info("✅ FinnHub connection established")

    # Initialize Baostock connection
    baostock = Container.baostock()
    await baostock.connect()
    logger.info("✅ Baostock connection established")

    # Register adapters
    logger.info("📦 Registering data adapters...")
    adapter_manager = Container.adapter_manager()

    # A股数据源 - 按优先级注册
    adapter_manager.register_adapter(Container.tushare_adapter())
    adapter_manager.register_adapter(Container.akshare_adapter())
    adapter_manager.register_adapter(Container.baostock_adapter())

    # 美股数据源
    adapter_manager.register_adapter(Container.yahoo_adapter())
    adapter_manager.register_adapter(Container.finnhub_adapter())

    # 加密货币数据源
    adapter_manager.register_adapter(Container.ccxt_adapter())
    adapter_manager.register_adapter(Container.crypto_adapter())

    logger.info("✅ All adapters registered\n")


def main():
    """Main entry point."""
    import asyncio

    settings = get_settings()

    # Pre-initialize all dependencies synchronously BEFORE creating server
    asyncio.run(initialize_dependencies())

    # Now create the MCP server
    mcp = create_app()

    if mcp is None:
        logger.error("Failed to create MCP server")
        return

    # 打印服务器信息
    logger.info("\n" + "=" * 70)
    logger.info("🚀 Stock Tool Nacos MCP Server")
    logger.info("=" * 70)
    logger.info(f"\n📋 Server Information:")
    logger.info(f"   Name: {settings.nacos.mcp_server_name}")
    logger.info(f"   Version: {settings.nacos.mcp_server_version}")
    logger.info(f"   Port: {settings.nacos.mcp_server_port}")
    logger.info(f"   Transport: {settings.nacos.mcp_transport}")
    logger.info(f"\n📡 Nacos Configuration:")
    logger.info(f"   Server: {settings.nacos.server_addr}")
    logger.info(f"   Namespace: {settings.nacos.namespace}")
    logger.info(f"   Auto Register: {settings.nacos.register_enabled}")
    logger.info(f"\n✅ Server ready and waiting for connections...")
    logger.info("=" * 70 + "\n")

    # 启动服务器
    logger.info(
        f"🚀 Starting MCP server with transport: {settings.nacos.mcp_transport}"
    )
    mcp.run(transport=settings.nacos.mcp_transport)


if __name__ == "__main__":
    main()
