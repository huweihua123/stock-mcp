"""Runtime lifecycle management."""

from __future__ import annotations

from src.server.providers.registry import load_alias_seeds
from src.server.utils.logger import logger
from src.server.utils.proxy_utils import disable_global_proxy_env


class RuntimeLifecycle:
    def __init__(self, runtime):
        self.runtime = runtime
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return

        logger.info("🚀 Starting stock-mcp runtime")
        disable_global_proxy_env()

        redis = self.runtime.container.redis()
        await redis.connect()
        self.runtime.state["redis"] = True
        logger.info("✅ Redis connection established")

        postgres = self.runtime.container.postgres()
        postgres_ok = await postgres.connect()
        self.runtime.state["postgres"] = postgres_ok
        if postgres_ok:
            logger.info("✅ PostgreSQL connection established")
        else:
            logger.warning("⚠️ PostgreSQL not available, will use fallback storage")

        security_master_repo = self.runtime.container.security_master_repo()
        await security_master_repo.ensure_schema()
        await load_alias_seeds(self.runtime)

        for plugin in self.runtime.provider_registry.list_enabled():
            if plugin.startup is None:
                continue
            try:
                started = await plugin.startup(self.runtime)
                self.runtime.state[f"provider:{plugin.name}"] = bool(started)
                logger.info(
                    "Provider startup completed",
                    provider=plugin.name,
                    started=bool(started),
                )
            except Exception as exc:
                self.runtime.state[f"provider:{plugin.name}"] = False
                logger.warning(
                    "Provider startup failed",
                    provider=plugin.name,
                    error=str(exc),
                )

        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return

        logger.info("🛑 Shutting down stock-mcp runtime")

        for plugin in reversed(self.runtime.provider_registry.list_enabled()):
            if plugin.shutdown is None:
                continue
            try:
                await plugin.shutdown(self.runtime)
            except Exception as exc:
                logger.warning(
                    "Provider shutdown failed",
                    provider=plugin.name,
                    error=str(exc),
                )

        try:
            await self.runtime.container.postgres().disconnect()
        except Exception:
            pass

        self._started = False
