# src/server/infrastructure/cache/redis_cache.py
"""Async cache wrapper using aiocache with Redis backend.
All services can use `cache.get/set` without worrying about client details.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Optional

import aiocache
from aiocache import Cache
from aiocache.serializers import BaseSerializer
from src.server.infrastructure.connections.redis_connection import RedisConnection

logger = logging.getLogger(__name__)


class DateAwareJsonSerializer(BaseSerializer):
    """JSON serializer that handles date and datetime objects."""
    
    DEFAULT_ENCODING = "utf-8"
    
    def _default(self, obj):
        if isinstance(obj, datetime):
            return {"__datetime__": obj.isoformat()}
        elif isinstance(obj, date):
            return {"__date__": obj.isoformat()}
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    
    def _object_hook(self, dct):
        if "__datetime__" in dct:
            return datetime.fromisoformat(dct["__datetime__"])
        if "__date__" in dct:
            return date.fromisoformat(dct["__date__"])
        return dct
    
    def dumps(self, value: Any) -> str:
        return json.dumps(value, default=self._default)
    
    def loads(self, value: Optional[str]) -> Any:
        if value is None:
            return None
        return json.loads(value, object_hook=self._object_hook)


class AsyncRedisCache:
    def __init__(self, redis_client: RedisConnection, ttl_default: int = 300):
        # Ensure the underlying Redis connection is established
        self._redis_conn = redis_client
        self._ttl_default = ttl_default
        # aiocache will use the same Redis URL with custom serializer
        self._cache = Cache(
            Cache.REDIS,
            endpoint=redis_client.config.get("host", "localhost"),
            port=redis_client.config.get("port", 6379),
            db=redis_client.config.get("db", 0),
            password=redis_client.config.get("password"),
            ttl=self._ttl_default,
            serializer=DateAwareJsonSerializer(),
        )

    async def get(self, key: str) -> Optional[Any]:
        try:
            return await self._cache.get(key)
        except Exception as e:
            logger.error(f"❌ Cache get error for {key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        try:
            await self._cache.set(key, value, ttl=ttl or self._ttl_default)
            return True
        except Exception as e:
            logger.error(f"❌ Cache set error for {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            await self._cache.delete(key)
            return True
        except Exception as e:
            logger.error(f"❌ Cache delete error for {key}: {e}")
            return False
