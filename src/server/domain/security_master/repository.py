# src/server/domain/security_master/repository.py
"""Security Master repository backed by PostgreSQL."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.server.utils.logger import logger


class SecurityMasterRepository:
    def __init__(self, postgres_conn):
        self._pg = postgres_conn

    async def _get_pool(self):
        if not self._pg.connected:
            ok = await self._pg.connect()
            if not ok:
                return None
        return self._pg.get_client()

    async def ensure_schema(self) -> bool:
        pool = await self._get_pool()
        if not pool:
            logger.warning("SecurityMaster: PostgreSQL not available, schema not created")
            return False

        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_master (
                    asset_id UUID PRIMARY KEY,
                    name TEXT,
                    asset_type TEXT NOT NULL,
                    country TEXT,
                    currency TEXT,
                    timezone TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_listing (
                    listing_id UUID PRIMARY KEY,
                    asset_id UUID NOT NULL REFERENCES asset_master(asset_id) ON DELETE CASCADE,
                    exchange TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(exchange, ticker)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_alias (
                    alias_id UUID PRIMARY KEY,
                    asset_id UUID NOT NULL REFERENCES asset_master(asset_id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    alias_type TEXT,
                    source TEXT,
                    confidence REAL,
                    locale TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(asset_id, alias)
                )
                """
            )
            # Ensure new columns exist for legacy tables
            await conn.execute(
                "ALTER TABLE asset_alias ADD COLUMN IF NOT EXISTS alias_type TEXT"
            )
            await conn.execute(
                "ALTER TABLE asset_alias ADD COLUMN IF NOT EXISTS source TEXT"
            )
            await conn.execute(
                "ALTER TABLE asset_alias ADD COLUMN IF NOT EXISTS confidence REAL"
            )
            await conn.execute(
                "ALTER TABLE asset_alias ADD COLUMN IF NOT EXISTS locale TEXT"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_identifier (
                    identifier_id UUID PRIMARY KEY,
                    asset_id UUID NOT NULL REFERENCES asset_master(asset_id) ON DELETE CASCADE,
                    id_type TEXT NOT NULL,
                    id_value TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(id_type, id_value)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_symbol (
                    provider_symbol_id UUID PRIMARY KEY,
                    asset_id UUID NOT NULL REFERENCES asset_master(asset_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    intervals_supported TEXT[],
                    exchange_override TEXT,
                    priority INT NOT NULL DEFAULT 100,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(asset_id, provider, provider_symbol, data_type)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_asset_listing_asset_id
                ON asset_listing(asset_id)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_asset_alias_alias
                ON asset_alias(alias)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_asset_identifier_value
                ON asset_identifier(id_value)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provider_symbol_asset_id
                ON provider_symbol(asset_id)
                """
            )

        logger.info("✅ SecurityMaster schema ensured")
        return True

    async def find_by_listing(self, exchange: str, ticker: str) -> Optional[Dict[str, Any]]:
        pool = await self._get_pool()
        if not pool:
            return None

        exchange = (exchange or "").upper()
        ticker = (ticker or "").upper()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                       al.exchange, al.ticker, al.is_primary
                FROM asset_listing al
                JOIN asset_master am ON al.asset_id = am.asset_id
                WHERE al.exchange = $1 AND al.ticker = $2
                """,
                exchange,
                ticker,
            )
            if not row:
                return None
            return dict(row)

    async def find_candidates(self, raw_symbol: str) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        if not pool:
            return []

        raw = (raw_symbol or "").upper()
        if not raw:
            return []

        candidates: List[Dict[str, Any]] = []

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                       al.exchange, al.ticker, al.is_primary
                FROM asset_listing al
                JOIN asset_master am ON al.asset_id = am.asset_id
                WHERE al.ticker = $1
                """,
                raw,
            )
            for row in rows:
                candidates.append(dict(row))

            alias_rows = await conn.fetch(
                """
                SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                       al.exchange, al.ticker, al.is_primary
                FROM asset_alias aa
                JOIN asset_master am ON aa.asset_id = am.asset_id
                LEFT JOIN asset_listing al ON aa.asset_id = al.asset_id AND al.is_primary = TRUE
                WHERE aa.alias = $1
                """,
                raw,
            )
            for row in alias_rows:
                candidates.append(dict(row))

        # De-duplicate by asset_id + exchange + ticker
        seen = set()
        deduped = []
        for item in candidates:
            key = (item.get("asset_id"), item.get("exchange"), item.get("ticker"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    async def upsert_asset(
        self,
        asset_id: Optional[str],
        name: str,
        asset_type: str,
        country: Optional[str] = None,
        currency: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> str:
        pool = await self._get_pool()
        if not pool:
            return asset_id or ""

        asset_id = asset_id or str(uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO asset_master (asset_id, name, asset_type, country, currency, timezone)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (asset_id) DO UPDATE
                SET name = EXCLUDED.name,
                    asset_type = EXCLUDED.asset_type,
                    country = EXCLUDED.country,
                    currency = EXCLUDED.currency,
                    timezone = EXCLUDED.timezone,
                    updated_at = NOW()
                """,
                asset_id,
                name,
                asset_type,
                country,
                currency,
                timezone,
            )
        return asset_id

    async def upsert_listing(
        self,
        asset_id: str,
        exchange: str,
        ticker: str,
        is_primary: bool = False,
    ) -> None:
        pool = await self._get_pool()
        if not pool:
            return

        exchange = exchange.upper()
        ticker = ticker.upper()
        listing_id = str(uuid4())

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO asset_listing (listing_id, asset_id, exchange, ticker, is_primary)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (exchange, ticker) DO UPDATE
                SET asset_id = EXCLUDED.asset_id,
                    is_primary = EXCLUDED.is_primary,
                    updated_at = NOW()
                """,
                listing_id,
                asset_id,
                exchange,
                ticker,
                is_primary,
            )

    async def add_alias(
        self,
        asset_id: str,
        alias: str,
        *,
        alias_type: Optional[str] = None,
        source: Optional[str] = None,
        confidence: Optional[float] = None,
        locale: Optional[str] = None,
    ) -> None:
        pool = await self._get_pool()
        if not pool:
            return

        alias = alias.upper()
        alias_id = str(uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO asset_alias (alias_id, asset_id, alias, alias_type, source, confidence, locale)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (asset_id, alias) DO NOTHING
                """,
                alias_id,
                asset_id,
                alias,
                alias_type,
                source,
                confidence,
                locale,
            )

    async def add_identifier(self, asset_id: str, id_type: str, id_value: str) -> None:
        pool = await self._get_pool()
        if not pool:
            return

        identifier_id = str(uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO asset_identifier (identifier_id, asset_id, id_type, id_value)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id_type, id_value) DO NOTHING
                """,
                identifier_id,
                asset_id,
                id_type,
                id_value,
            )

    async def upsert_alias_for_listing(
        self,
        normalized: str,
        alias: str,
        *,
        asset_type: str = "stock",
        source: Optional[str] = None,
        confidence: Optional[float] = None,
        locale: Optional[str] = None,
    ) -> None:
        if ":" not in (normalized or ""):
            return
        exchange, symbol = normalized.split(":", 1)
        exchange = exchange.strip().upper()
        symbol = symbol.strip().upper()

        existing = await self.find_by_listing(exchange, symbol)
        if existing and existing.get("asset_id"):
            asset_id = existing.get("asset_id")
        else:
            asset_id = await self.upsert_asset(
                asset_id=None,
                name=symbol,
                asset_type=asset_type,
            )
            await self.upsert_listing(
                asset_id=asset_id,
                exchange=exchange,
                ticker=symbol,
                is_primary=True,
            )
        await self.add_alias(
            asset_id,
            alias,
            alias_type="seed",
            source=source,
            confidence=confidence,
            locale=locale,
        )

    async def upsert_provider_symbol(
        self,
        asset_id: str,
        provider: str,
        provider_symbol: str,
        data_type: str = "historical",
        intervals_supported: Optional[List[str]] = None,
        exchange_override: Optional[str] = None,
        priority: int = 100,
        enabled: bool = True,
    ) -> None:
        pool = await self._get_pool()
        if not pool:
            return

        provider_symbol_id = str(uuid4())
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO provider_symbol (
                    provider_symbol_id, asset_id, provider, provider_symbol,
                    data_type, intervals_supported, exchange_override, priority, enabled
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (asset_id, provider, provider_symbol, data_type) DO UPDATE
                SET intervals_supported = EXCLUDED.intervals_supported,
                    exchange_override = EXCLUDED.exchange_override,
                    priority = EXCLUDED.priority,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                """,
                provider_symbol_id,
                asset_id,
                provider,
                provider_symbol,
                data_type,
                intervals_supported,
                exchange_override,
                priority,
                enabled,
            )

    async def get_provider_symbols(
        self,
        asset_id: str,
        data_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        if not pool:
            return []

        async with pool.acquire() as conn:
            if data_type:
                rows = await conn.fetch(
                    """
                    SELECT provider, provider_symbol, data_type, intervals_supported,
                           exchange_override, priority, enabled
                    FROM provider_symbol
                    WHERE asset_id = $1 AND data_type = $2 AND enabled = TRUE
                    ORDER BY priority ASC
                    """,
                    asset_id,
                    data_type,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT provider, provider_symbol, data_type, intervals_supported,
                           exchange_override, priority, enabled
                    FROM provider_symbol
                    WHERE asset_id = $1 AND enabled = TRUE
                    ORDER BY priority ASC
                    """,
                    asset_id,
                )

        return [dict(row) for row in rows]
