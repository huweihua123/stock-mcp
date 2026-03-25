# src/server/core/dependencies.py
"""Dependency injection container for stable runtime resources."""

from dependency_injector import containers, providers
from src.server.config.settings import get_settings
from src.server.infrastructure.connections.redis_connection import RedisConnection
from src.server.infrastructure.connections.tushare_connection import TushareConnection
from src.server.infrastructure.connections.finnhub_connection import FinnhubConnection
from src.server.infrastructure.connections.baostock_connection import BaostockConnection
from src.server.infrastructure.connections.postgres_connection import PostgresConnection

# Adapters
from src.server.domain.adapters.yahoo_adapter import YahooAdapter
from src.server.domain.adapters.akshare_adapter import AkshareAdapter
from src.server.domain.adapters.crypto_adapter import CryptoAdapter
from src.server.domain.adapters.tushare_adapter import TushareAdapter
from src.server.domain.adapters.finnhub_adapter import FinnhubAdapter
from src.server.domain.adapters.baostock_adapter import BaostockAdapter
from src.server.domain.adapters.ccxt_adapter import CCXTAdapter
from src.server.domain.adapters.futures_adapter import FuturesAdapter
from src.server.domain.adapters.alpha_vantage_adapter import AlphaVantageAdapter
from src.server.domain.adapters.twelve_data_adapter import TwelveDataAdapter
from src.server.domain.adapters.fred_adapter import FredAdapter

# Services
from src.server.domain.services.fundamental_service import FundamentalService
from src.server.domain.services.technical_service import TechnicalService
from src.server.domain.services.filings_service import FilingsService
from src.server.utils.proxy_utils import build_proxy_url

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
        config=providers.Callable(
            lambda cfg: {
                **cfg.finnhub.model_dump(),
                "proxy_url": build_proxy_url(
                    cfg.proxy.enabled,
                    cfg.proxy.host,
                    cfg.proxy.port,
                ),
            },
            config,
        ),
    )
    baostock = providers.Singleton(
        BaostockConnection,
        config=providers.Callable(lambda cfg: cfg.baostock.model_dump(), config),
    )
    postgres = providers.Singleton(
        PostgresConnection,
        config=providers.Callable(
            lambda cfg: {
                "dsn": cfg.postgres.build_asyncpg_dsn(),
                "host": cfg.postgres.host,
                "port": cfg.postgres.port,
                "user": cfg.postgres.user,
                "password": cfg.postgres.password,
                "database": cfg.postgres.database,
                "pool_min": cfg.postgres.pool_min,
                "pool_max": cfg.postgres.pool_max,
            },
            config,
        ),
    )

    # Cache (wrap Redis client)
    cache = providers.Singleton(AsyncRedisCache, redis_client=redis)

    # Adapters (each receives cache for result caching)
    proxy_url = providers.Callable(
        lambda cfg: build_proxy_url(cfg.proxy.enabled, cfg.proxy.host, cfg.proxy.port),
        config,
    )

    yahoo_adapter = providers.Singleton(
        YahooAdapter,
        cache=cache,
        proxy_url=proxy_url,
    )
    akshare_adapter = providers.Singleton(AkshareAdapter, cache=cache)
    crypto_adapter = providers.Singleton(CryptoAdapter, cache=cache, proxy_url=proxy_url)
    ccxt_adapter = providers.Singleton(CCXTAdapter, cache=cache, proxy_url=proxy_url)
    tushare_adapter = providers.Singleton(
        TushareAdapter, tushare_conn=tushare, cache=cache
    )
    finnhub_adapter = providers.Singleton(
        FinnhubAdapter, finnhub_conn=finnhub, cache=cache
    )
    baostock_adapter = providers.Singleton(BaostockAdapter, cache=cache)
    futures_adapter = providers.Singleton(
        FuturesAdapter,
        cache=cache,
        proxy_url=proxy_url,
    )
    alpha_vantage_adapter = providers.Singleton(
        AlphaVantageAdapter,
        api_key=providers.Callable(lambda cfg: cfg.api_keys.alpha_vantage or "", config),
        cache=cache,
        proxy_url=proxy_url,
    )
    twelve_data_adapter = providers.Singleton(
        TwelveDataAdapter,
        api_key=providers.Callable(lambda cfg: cfg.api_keys.twelve_data or "", config),
        cache=cache,
        proxy_url=proxy_url,
    )
    fred_adapter = providers.Singleton(
        FredAdapter,
        api_key=providers.Callable(lambda cfg: cfg.api_keys.fred or "", config),
        cache=cache,
        proxy_url=proxy_url,
    )

    from src.server.domain.adapters.edgar_adapter import EdgarAdapter

    edgar_adapter = providers.Singleton(EdgarAdapter, cache=cache, proxy_url=proxy_url)

    # Runtime-native provider coordination
    from src.server.runtime.provider_runtime import ProviderRuntime

    provider_runtime = providers.Singleton(
        ProviderRuntime,
        provider_timeout_seconds=providers.Callable(
            lambda cfg: cfg.timeout.provider_call_seconds,
            config,
        ),
    )

    # Security Master
    from src.server.domain.security_master import SecurityMasterRepository

    security_master_repo = providers.Singleton(
        SecurityMasterRepository,
        postgres_conn=postgres,
        backend_mode=providers.Callable(lambda cfg: cfg.security_master_backend, config),
        sqlite_path=providers.Callable(lambda cfg: cfg.security_master_sqlite_path, config),
    )

    # Symbol resolver & gateway
    from src.server.domain.symbols import SymbolResolver
    from src.server.domain.routing import MarketRouter, ProviderHealthTracker, RoutingPolicy
    from src.server.runtime.provider_facade import ProviderFacade

    symbol_resolver = providers.Singleton(
        SymbolResolver,
        security_master_repo=security_master_repo,
        adapter_manager=provider_runtime,
    )
    routing_policy = providers.Singleton(RoutingPolicy.load)
    provider_health = providers.Singleton(ProviderHealthTracker)
    market_router = providers.Singleton(
        MarketRouter,
        adapter_manager=provider_runtime,
        security_master_repo=security_master_repo,
        routing_policy=routing_policy,
        health_tracker=provider_health,
        provider_timeout_seconds=providers.Callable(
            lambda cfg: cfg.timeout.provider_call_seconds,
            config,
        ),
    )
    provider_facade = providers.Singleton(
        ProviderFacade,
        provider_runtime=provider_runtime,
        symbol_resolver=symbol_resolver,
        market_router=market_router,
    )

    # MinIO Client
    from src.server.infrastructure.minio_client import MinioClient

    minio_client = providers.Singleton(MinioClient)

    # Services (receive adapter manager and cache)
    fundamental_service = providers.Factory(
        FundamentalService,
        provider_facade=provider_facade,
        cache=cache,
    )
    technical_service = providers.Factory(
        TechnicalService,
        provider_facade=provider_facade,
    )
    filings_service = providers.Factory(
        FilingsService,
        provider_facade=provider_facade,
        minio_client=minio_client,
    )
