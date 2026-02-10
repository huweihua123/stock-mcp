# src/server/core/bootstrap.py
"""Application bootstrap helpers.

Provides a single initialization entrypoint for connections/adapters to avoid
duplicate registration across FastAPI and FastMCP lifespans.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


_bootstrap_lock = asyncio.Lock()
_initialized: bool = False


async def init_adapters() -> None:
    """Initialize connections and register adapters exactly once."""
    global _initialized

    if _initialized:
        return

    async with _bootstrap_lock:
        if _initialized:
            return

        logger.info("🚀 Bootstrapping application dependencies")

        redis = Container.redis()
        await redis.connect()
        logger.info("✅ Redis connection established")

        # Initialize PostgreSQL (Security Master)
        postgres = Container.postgres()
        postgres_ok = await postgres.connect()
        if postgres_ok:
            logger.info("✅ PostgreSQL connection established")
            security_master_repo = Container.security_master_repo()
            await security_master_repo.ensure_schema()
        else:
            logger.warning("⚠️ PostgreSQL not available, Security Master will be degraded")

        config = Container.config()

        # Initialize Tushare (optional)
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

        # Initialize FinnHub (optional)
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

        # Initialize Baostock
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
            "✅ All adapters registered (A-share: %sAkshare > Baostock)",
            "Tushare > " if tushare_available else "",
        )

        _initialized = True


async def shutdown_adapters() -> None:
    """Placeholder for future graceful shutdown logic."""
    try:
        postgres = Container.postgres()
        await postgres.disconnect()
    except Exception:
        pass
    return None
