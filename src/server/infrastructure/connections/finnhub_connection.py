# src/server/infrastructure/connections/finnhub_connection.py
"""Async FinnHub connection wrapper."""

import asyncio
import logging
from typing import Any, Dict
import requests
from .base import AsyncDataSourceConnection

logger = logging.getLogger(__name__)


class FinnhubConnection(AsyncDataSourceConnection):
    """FinnHub API connection wrapper."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._api_key = config.get("api_key")
        self._base_url = "https://finnhub.io/api/v1"
        self._proxy_url = config.get("proxy_url")
        self._session: Any = None

    async def connect(self) -> bool:
        """Establish FinnHub connection."""
        try:
            if not self._api_key:
                logger.warning("⚠️ FinnHub API key not configured")
                self._connected = False
                return False

            loop = asyncio.get_event_loop()

            def create_session():
                session = requests.Session()
                session.trust_env = False
                session.headers.update(
                    {"X-Finnhub-Token": self._api_key, "User-Agent": "Mozilla/5.0"}
                )
                if self._proxy_url:
                    session.proxies.update(
                        {
                            "http": self._proxy_url,
                            "https": self._proxy_url,
                        }
                    )
                    logger.info("✅ FinnHub connection configured with explicit proxy")
                else:
                    logger.info("ℹ️ FinnHub connection running without proxy")
                return session

            self._session = await loop.run_in_executor(None, create_session)

            def health_check():
                try:
                    url = f"{self._base_url}/stock/profile2"
                    params = {"symbol": "AAPL", "token": self._api_key}
                    r = self._session.get(url, params=params, timeout=10)
                    return r.status_code == 200 and r.json()
                except Exception as e:
                    logger.warning(f"FinnHub check failed: {e}")
                    return False

            result = await loop.run_in_executor(None, health_check)
            self._connected = bool(result)

            if self._connected:
                logger.info("✅ FinnHub connected")
            else:
                logger.warning("⚠️ FinnHub check failed (may still work)")
                self._connected = True

            return self._connected

        except Exception as e:
            logger.warning(f"⚠️ FinnHub error: {e}")
            self._connected = True
            return True

    async def disconnect(self) -> bool:
        """Close connection."""
        if self._session:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._session.close)
        self._connected = False
        self._session = None
        logger.info("✅ FinnHub closed")
        return True

    async def is_healthy(self) -> bool:
        """Check health."""
        if not self._session:
            return False
        try:
            loop = asyncio.get_event_loop()

            def check():
                url = f"{self._base_url}/stock/profile2"
                params = {"symbol": "AAPL", "token": self._api_key}
                r = self._session.get(url, params=params, timeout=5)
                return r.status_code == 200

            return await loop.run_in_executor(None, check)
        except Exception as e:
            logger.error(f"❌ FinnHub health check error: {e}")
            return False

    def get_client(self) -> Any:
        return self._session

    def get_api_key(self) -> str:
        return self._api_key

    def get_base_url(self) -> str:
        return self._base_url
