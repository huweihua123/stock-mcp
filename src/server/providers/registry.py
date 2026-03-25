"""Built-in provider plugin registration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.server.providers.contracts import (
    CODE_EXPORT,
    FILINGS,
    FUNDAMENTAL,
    HISTORICAL_PRICE,
    MACRO_SERIES,
    MONEY_FLOW,
    NEWS_SEARCH,
    REALTIME_PRICE,
    SECTOR_RESEARCH,
    TECHNICAL_INDICATORS,
)
from src.server.runtime.models import ProviderPlugin
from src.server.utils.logger import logger


async def _startup_yahoo(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.yahoo_adapter())
    return True


async def _startup_akshare(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.akshare_adapter())
    return True


async def _startup_crypto(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.crypto_adapter())
    runtime.provider_runtime.register_adapter(runtime.container.ccxt_adapter())
    return True


async def _startup_futures(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.futures_adapter())
    return True


async def _startup_twelve_data(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.twelve_data_adapter())
    return True


async def _startup_alpha_vantage(runtime):
    runtime.provider_runtime.register_adapter(runtime.container.alpha_vantage_adapter())
    return True


async def _startup_fred(runtime):
    if runtime.settings.api_keys.fred:
        runtime.provider_runtime.register_adapter(runtime.container.fred_adapter())
        return True
    return False


async def _startup_edgar(runtime):
    # Edgar adapter is not registered into market routing; capability uses its own service path.
    runtime.state["edgar_adapter"] = runtime.container.edgar_adapter()
    return True


async def _startup_tavily(runtime):
    del runtime
    return True


async def _startup_tushare(runtime):
    if not runtime.settings.tushare.is_available:
        return False
    conn = runtime.container.tushare()
    ok = await conn.connect()
    if ok:
        runtime.provider_runtime.register_adapter(runtime.container.tushare_adapter())
    return ok


async def _startup_finnhub(runtime):
    if not runtime.settings.finnhub.is_available:
        return False
    conn = runtime.container.finnhub()
    await conn.connect()
    runtime.provider_runtime.register_adapter(runtime.container.finnhub_adapter())
    return True


async def _startup_baostock(runtime):
    conn = runtime.container.baostock()
    await conn.connect()
    runtime.provider_runtime.register_adapter(runtime.container.baostock_adapter())
    return True


BUILTIN_PROVIDERS: list[ProviderPlugin] = [
    ProviderPlugin(
        name="tushare",
        description="A-share market data and money flow provider",
        contracts=frozenset(
            {
                REALTIME_PRICE,
                HISTORICAL_PRICE,
                TECHNICAL_INDICATORS,
                MONEY_FLOW,
                FUNDAMENTAL,
                CODE_EXPORT,
            }
        ),
        startup=_startup_tushare,
    ),
    ProviderPlugin(
        name="akshare",
        description="Domestic market analytics and sector data provider",
        contracts=frozenset(
            {
                REALTIME_PRICE,
                HISTORICAL_PRICE,
                MONEY_FLOW,
                SECTOR_RESEARCH,
                FILINGS,
            }
        ),
        startup=_startup_akshare,
    ),
    ProviderPlugin(
        name="baostock",
        description="Domestic market data provider",
        contracts=frozenset({HISTORICAL_PRICE, MACRO_SERIES}),
        startup=_startup_baostock,
    ),
    ProviderPlugin(
        name="yahoo",
        description="Global market and US sector data provider",
        contracts=frozenset(
            {
                REALTIME_PRICE,
                HISTORICAL_PRICE,
                TECHNICAL_INDICATORS,
                SECTOR_RESEARCH,
            }
        ),
        startup=_startup_yahoo,
    ),
    ProviderPlugin(
        name="finnhub",
        description="US market and filings provider",
        contracts=frozenset({REALTIME_PRICE, FILINGS, FUNDAMENTAL}),
        startup=_startup_finnhub,
    ),
    ProviderPlugin(
        name="crypto",
        description="Crypto market data providers",
        contracts=frozenset({REALTIME_PRICE, HISTORICAL_PRICE}),
        startup=_startup_crypto,
    ),
    ProviderPlugin(
        name="futures",
        description="Futures market data provider",
        contracts=frozenset({HISTORICAL_PRICE, REALTIME_PRICE}),
        startup=_startup_futures,
    ),
    ProviderPlugin(
        name="twelve_data",
        description="Foreign market data provider",
        contracts=frozenset({HISTORICAL_PRICE, REALTIME_PRICE}),
        startup=_startup_twelve_data,
    ),
    ProviderPlugin(
        name="alpha_vantage",
        description="Foreign macro and commodity provider",
        contracts=frozenset({HISTORICAL_PRICE, MACRO_SERIES, CODE_EXPORT}),
        startup=_startup_alpha_vantage,
    ),
    ProviderPlugin(
        name="fred",
        description="US macro series provider",
        contracts=frozenset({MACRO_SERIES}),
        startup=_startup_fred,
    ),
    ProviderPlugin(
        name="edgar",
        description="US filings and document provider",
        contracts=frozenset({FILINGS}),
        startup=_startup_edgar,
    ),
    ProviderPlugin(
        name="tavily",
        description="Unified web and news search provider",
        contracts=frozenset({NEWS_SEARCH}),
        startup=_startup_tavily,
    ),
]


async def load_alias_seeds(runtime) -> None:
    try:
        seed_path = os.getenv(
            "ALIASES_SEED_PATH",
            str(Path(__file__).resolve().parents[1] / "config" / "aliases_seed.json"),
        )
        if not seed_path or not Path(seed_path).exists():
            return
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return
        repo = runtime.container.security_master_repo()
        for item in data:
            alias = item.get("alias")
            normalized = item.get("normalized")
            if not alias or not normalized:
                continue
            await repo.upsert_alias_for_listing(
                normalized=normalized,
                alias=alias,
                asset_type=item.get("asset_type") or "stock",
                source=item.get("source") or "seed",
                confidence=item.get("confidence"),
                locale=item.get("locale"),
            )
        logger.info("✅ Alias seeds loaded", count=len(data))
    except Exception as exc:
        logger.warning("Alias seed load failed", error=str(exc))


def get_builtin_provider_plugins() -> list[ProviderPlugin]:
    return BUILTIN_PROVIDERS
