# src/server/infrastructure/connections/postgres_connection.py
"""Async PostgreSQL connection using asyncpg (wrapped via AsyncDataSourceConnection)."""

import logging
from typing import Any, Dict, Optional

import asyncpg

from .base import AsyncDataSourceConnection

logger = logging.getLogger(__name__)


class PostgresConnection(AsyncDataSourceConnection):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> bool:
        dsn = self.config.get("dsn") or self.config.get("database_url")
        if not dsn:
            # Build from parts if possible
            host = self.config.get("host", "localhost")
            port = self.config.get("port", 5432)
            user = self.config.get("user", "postgres")
            password = self.config.get("password", "")
            database = self.config.get("database", "valuecell")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"

        if isinstance(dsn, str) and dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

        try:
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=int(self.config.get("pool_min", 1)),
                max_size=int(self.config.get("pool_max", 10)),
                command_timeout=30,
            )
            self._client = self._pool
            self._connected = True
            logger.info("✅ PostgreSQL connection pool established")
            return True
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection error: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> bool:
        if self._pool:
            await self._pool.close()
            self._connected = False
            logger.info("✅ PostgreSQL connection pool closed")
            return True
        return False

    async def is_healthy(self) -> bool:
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"❌ PostgreSQL health check failed: {e}")
            return False

    def get_client(self) -> Any:
        return self._pool
