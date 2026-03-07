# src/server/infrastructure/connections/tushare_connection.py
"""Async Tushare connection using tushare.pro_api.
Wraps the synchronous Tushare client in an async interface.
"""

import asyncio
import logging
import os
from typing import Any, Dict
from urllib.parse import urlparse

import tushare as ts
from .base import AsyncDataSourceConnection

logger = logging.getLogger(__name__)

class TushareConnection(AsyncDataSourceConnection):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._client: Any = None
        self._token = config.get("token")
        self._http_url = config.get("http_url") or ""
        self._ensure_no_proxy_hosts()

    def _ensure_no_proxy_hosts(self) -> None:
        """Ensure Tushare hosts bypass proxy even if global proxy env is present."""
        hosts = {"api.tushare.pro", "tushare.pro"}
        if self._http_url:
            parsed = urlparse(self._http_url)
            if parsed.hostname:
                hosts.add(parsed.hostname)

        no_proxy = os.getenv("NO_PROXY", "")
        no_proxy_lower = os.getenv("no_proxy", "")
        existing = {h.strip() for h in (no_proxy + "," + no_proxy_lower).split(",") if h.strip()}
        merged = sorted(existing | hosts)
        merged_value = ",".join(merged)
        os.environ["NO_PROXY"] = merged_value
        os.environ["no_proxy"] = merged_value
        logger.info(f"✅ Tushare NO_PROXY hosts configured: {merged_value}")

    def _build_client(self) -> Any:
        """Build Tushare pro client with optional custom endpoint override."""
        client = ts.pro_api(self._token)

        # Some private deployments require explicit internal fields.
        if self._token:
            client._DataApi__token = self._token
        if self._http_url:
            client._DataApi__http_url = self._http_url

        return client

    async def connect(self) -> bool:
        try:
            loop = asyncio.get_event_loop()
            self._client = await loop.run_in_executor(None, self._build_client)
            # simple health check: request a tiny piece of data
            df = await loop.run_in_executor(
                None, lambda: self._client.stock_basic(fields="ts_code")
            )
            self._connected = not df.empty
            if self._connected:
                self._client = self._client
                if self._http_url:
                    logger.info(
                        "✅ Tushare async connection established (custom url enabled)"
                    )
                else:
                    logger.info("✅ Tushare async connection established")
            else:
                logger.error("❌ Tushare health check returned empty data")
            return self._connected
        except Exception as e:
            logger.error(f"❌ Tushare connection error: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> bool:
        # Tushare client does not need explicit close
        self._connected = False
        self._client = None
        logger.info("✅ Tushare connection closed")
        return True

    async def is_healthy(self) -> bool:
        if not self._client:
            return False
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(
                None, lambda: self._client.stock_basic(fields="ts_code")
            )
            healthy = not df.empty
            if not healthy:
                logger.warning("⚠️ Tushare health check returned empty data")
            return healthy
        except Exception as e:
            logger.error(f"❌ Tushare health check exception: {e}")
            return False

    def get_client(self) -> Any:
        return self._client
