# src/server/core/dependencies.py
"""Dependency injection container for the project.
All services, adapters, connections, cache, and settings are registered here.
Modules can obtain instances via `Container.xxx()`.
"""

from dependency_injector import containers, providers
from src.server.config.settings import get_settings
from src.server.infrastructure.connections.redis_connection import RedisConnection
from src.server.infrastructure.connections.tushare_connection import TushareConnection
from src.server.infrastructure.connections.finnhub_connection import FinnhubConnection
from src.server.infrastructure.connections.baostock_connection import BaostockConnection

# Adapters
from src.server.domain.adapters.yahoo_adapter import YahooAdapter
from src.server.domain.adapters.akshare_adapter import AkshareAdapter
from src.server.domain.adapters.crypto_adapter import CryptoAdapter
from src.server.domain.adapters.tushare_adapter import TushareAdapter
from src.server.domain.adapters.finnhub_adapter import FinnhubAdapter
from src.server.domain.adapters.baostock_adapter import BaostockAdapter
from src.server.domain.adapters.ccxt_adapter import CCXTAdapter

# Services
from src.server.domain.services.fundamental_service import FundamentalService
from src.server.domain.services.news_service import NewsService
from src.server.domain.services.technical_service import TechnicalService
from src.server.domain.services.filings_service import FilingsService

# Cache wrapper (aiocache)
from src.server.infrastructure.cache.redis_cache import AsyncRedisCache


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(packages=["src.server"])

    config = providers.Singleton(get_settings)

    # Connections
    redis = providers.Singleton(
        RedisConnection,
        config=providers.Callable(lambda cfg: cfg.redis.model_dump(), config),
    )
    tushare = providers.Singleton(
        TushareConnection,
        config=providers.Callable(lambda cfg: cfg.tushare.model_dump(), config),
    )
    finnhub = providers.Singleton(
        FinnhubConnection,
        config=providers.Callable(lambda cfg: cfg.finnhub.model_dump(), config),
    )
    baostock = providers.Singleton(
        BaostockConnection,
        config=providers.Callable(lambda cfg: cfg.baostock.model_dump(), config),
    )

    # Cache (wrap Redis client)
    cache = providers.Singleton(AsyncRedisCache, redis_client=redis)

    # Adapters (each receives cache for result caching)
    yahoo_adapter = providers.Singleton(
        YahooAdapter,
        cache=cache,
        proxy_url=providers.Callable(
            lambda cfg: f"http://{cfg.proxy.host}:{cfg.proxy.port}" if cfg.proxy.enabled else None,
            config
        )
    )
    akshare_adapter = providers.Singleton(AkshareAdapter, cache=cache)
    crypto_adapter = providers.Singleton(CryptoAdapter, cache=cache)
    ccxt_adapter = providers.Singleton(CCXTAdapter, cache=cache)
    tushare_adapter = providers.Singleton(
        TushareAdapter, tushare_conn=tushare, cache=cache
    )
    finnhub_adapter = providers.Singleton(
        FinnhubAdapter, finnhub_conn=finnhub, cache=cache
    )
    baostock_adapter = providers.Singleton(
        BaostockAdapter, cache=cache
    )
    
    from src.server.domain.adapters.edgar_adapter import EdgarAdapter
    edgar_adapter = providers.Singleton(
        EdgarAdapter, cache=cache
    )

    # Adapter manager
    from src.server.domain.adapter_manager import AdapterManager

    adapter_manager = providers.Singleton(
        AdapterManager
    )

    # MinIO Client
    from src.server.infrastructure.minio_client import MinioClient
    minio_client = providers.Singleton(MinioClient)

    # Services (receive adapter manager and cache)
    fundamental_service = providers.Factory(
        FundamentalService,
        adapter_manager=adapter_manager,
        cache=cache,
    )
    news_service = providers.Factory(
        NewsService,
        adapter_manager=adapter_manager,
        cache=cache,
        api_keys=providers.Callable(lambda cfg: cfg.api_keys.model_dump(), config),
    )
    technical_service = providers.Factory(
        TechnicalService,
        adapter_manager=adapter_manager,
    )
    filings_service = providers.Factory(
        FilingsService,
        adapter_manager=adapter_manager,
        minio_client=minio_client,
    )
