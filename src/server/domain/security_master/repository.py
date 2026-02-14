# src/server/domain/security_master/repository.py
"""Security Master repository with adaptive storage backends.

Primary backend: PostgreSQL
Fallback backend: SQLite (file-based)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.server.utils.logger import logger


class PostgresSecurityMasterRepository:
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
            result = dict(row)
            if result.get("asset_id") is not None:
                result["asset_id"] = str(result["asset_id"])
            return result

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
                item = dict(row)
                if item.get("asset_id") is not None:
                    item["asset_id"] = str(item["asset_id"])
                candidates.append(item)

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
                item = dict(row)
                if item.get("asset_id") is not None:
                    item["asset_id"] = str(item["asset_id"])
                candidates.append(item)

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


class SQLiteSecurityMasterRepository:
    def __init__(self, path: str):
        self._path = path
        self._lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    async def _connect(self):
        if self._conn is None:
            path = self._path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def ensure_schema(self) -> bool:
        try:
            conn = await self._connect()
            async with self._lock:
                await asyncio.to_thread(self._ensure_schema_sync, conn)
            logger.info("✅ SecurityMaster SQLite schema ensured", path=self._path)
            return True
        except Exception as e:
            logger.warning("SecurityMaster SQLite schema failed", error=str(e))
            return False

    def _ensure_schema_sync(self, conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_master (
                asset_id TEXT PRIMARY KEY,
                name TEXT,
                asset_type TEXT NOT NULL,
                country TEXT,
                currency TEXT,
                timezone TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_listing (
                listing_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                ticker TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, ticker)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_alias (
                alias_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                alias_type TEXT,
                source TEXT,
                confidence REAL,
                locale TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(asset_id, alias)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_identifier (
                identifier_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                id_type TEXT NOT NULL,
                id_value TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(id_type, id_value)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_symbol (
                provider_symbol_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                data_type TEXT NOT NULL,
                intervals_supported TEXT,
                exchange_override TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(asset_id, provider, provider_symbol, data_type)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_listing_asset_id ON asset_listing(asset_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_alias_alias ON asset_alias(alias)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_identifier_value ON asset_identifier(id_value)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_provider_symbol_asset_id ON provider_symbol(asset_id)")
        conn.commit()

    async def _execute(self, sql: str, params: Tuple = ()):  # type: ignore
        conn = await self._connect()
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, conn, sql, params)

    def _execute_sync(self, conn: sqlite3.Connection, sql: str, params: Tuple):
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount

    async def _fetchone(self, sql: str, params: Tuple = ()):  # type: ignore
        conn = await self._connect()
        async with self._lock:
            return await asyncio.to_thread(self._fetchone_sync, conn, sql, params)

    def _fetchone_sync(self, conn: sqlite3.Connection, sql: str, params: Tuple):
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    async def _fetchall(self, sql: str, params: Tuple = ()):  # type: ignore
        conn = await self._connect()
        async with self._lock:
            return await asyncio.to_thread(self._fetchall_sync, conn, sql, params)

    def _fetchall_sync(self, conn: sqlite3.Connection, sql: str, params: Tuple):
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    async def find_by_listing(self, exchange: str, ticker: str) -> Optional[Dict[str, Any]]:
        exchange = (exchange or "").upper()
        ticker = (ticker or "").upper()
        return await self._fetchone(
            """
            SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                   al.exchange, al.ticker, al.is_primary
            FROM asset_listing al
            JOIN asset_master am ON al.asset_id = am.asset_id
            WHERE al.exchange = ? AND al.ticker = ?
            """,
            (exchange, ticker),
        )

    async def find_candidates(self, raw_symbol: str) -> List[Dict[str, Any]]:
        raw = (raw_symbol or "").upper()
        if not raw:
            return []
        rows = await self._fetchall(
            """
            SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                   al.exchange, al.ticker, al.is_primary
            FROM asset_listing al
            JOIN asset_master am ON al.asset_id = am.asset_id
            WHERE al.ticker = ?
            """,
            (raw,),
        )
        alias_rows = await self._fetchall(
            """
            SELECT am.asset_id, am.name, am.asset_type, am.country, am.currency, am.timezone,
                   al.exchange, al.ticker, al.is_primary
            FROM asset_alias aa
            JOIN asset_master am ON aa.asset_id = am.asset_id
            LEFT JOIN asset_listing al ON aa.asset_id = al.asset_id AND al.is_primary = 1
            WHERE aa.alias = ?
            """,
            (raw,),
        )
        candidates = rows + alias_rows
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
        asset_id = asset_id or str(uuid4())
        await self._execute(
            """
            INSERT INTO asset_master (asset_id, name, asset_type, country, currency, timezone)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                name=excluded.name,
                asset_type=excluded.asset_type,
                country=excluded.country,
                currency=excluded.currency,
                timezone=excluded.timezone,
                updated_at=CURRENT_TIMESTAMP
            """,
            (asset_id, name, asset_type, country, currency, timezone),
        )
        return asset_id

    async def upsert_listing(
        self,
        asset_id: str,
        exchange: str,
        ticker: str,
        is_primary: bool = False,
    ) -> None:
        exchange = exchange.upper()
        ticker = ticker.upper()
        listing_id = str(uuid4())
        await self._execute(
            """
            INSERT INTO asset_listing (listing_id, asset_id, exchange, ticker, is_primary)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(exchange, ticker) DO UPDATE SET
                asset_id=excluded.asset_id,
                is_primary=excluded.is_primary,
                updated_at=CURRENT_TIMESTAMP
            """,
            (listing_id, asset_id, exchange, ticker, int(is_primary)),
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
        alias_id = str(uuid4())
        alias = alias.upper()
        await self._execute(
            """
            INSERT INTO asset_alias (alias_id, asset_id, alias, alias_type, source, confidence, locale)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, alias) DO NOTHING
            """,
            (alias_id, asset_id, alias, alias_type, source, confidence, locale),
        )

    async def add_identifier(self, asset_id: str, id_type: str, id_value: str) -> None:
        identifier_id = str(uuid4())
        await self._execute(
            """
            INSERT INTO asset_identifier (identifier_id, asset_id, id_type, id_value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id_type, id_value) DO NOTHING
            """,
            (identifier_id, asset_id, id_type, id_value),
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
        provider_symbol_id = str(uuid4())
        intervals_json = json.dumps(intervals_supported or [])
        await self._execute(
            """
            INSERT INTO provider_symbol (
                provider_symbol_id, asset_id, provider, provider_symbol,
                data_type, intervals_supported, exchange_override, priority, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, provider, provider_symbol, data_type) DO UPDATE SET
                intervals_supported=excluded.intervals_supported,
                exchange_override=excluded.exchange_override,
                priority=excluded.priority,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                provider_symbol_id,
                asset_id,
                provider,
                provider_symbol,
                data_type,
                intervals_json,
                exchange_override,
                priority,
                int(enabled),
            ),
        )

    async def get_provider_symbols(
        self,
        asset_id: str,
        data_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if data_type:
            rows = await self._fetchall(
                """
                SELECT provider, provider_symbol, data_type, intervals_supported,
                       exchange_override, priority, enabled
                FROM provider_symbol
                WHERE asset_id = ? AND data_type = ? AND enabled = 1
                ORDER BY priority ASC
                """,
                (asset_id, data_type),
            )
        else:
            rows = await self._fetchall(
                """
                SELECT provider, provider_symbol, data_type, intervals_supported,
                       exchange_override, priority, enabled
                FROM provider_symbol
                WHERE asset_id = ? AND enabled = 1
                ORDER BY priority ASC
                """,
                (asset_id,),
            )
        for row in rows:
            if row.get("intervals_supported"):
                try:
                    row["intervals_supported"] = json.loads(row.get("intervals_supported"))
                except Exception:
                    row["intervals_supported"] = []
        return rows

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


class SecurityMasterRepository:
    """Adaptive repository that falls back to SQLite when Postgres is unavailable."""

    def __init__(
        self,
        postgres_conn,
        backend_mode: str = "auto",
        sqlite_path: Optional[str] = None,
    ):
        self._backend_mode = (backend_mode or "auto").lower()
        self._pg_repo = PostgresSecurityMasterRepository(postgres_conn) if postgres_conn else None
        if not sqlite_path:
            sqlite_path = str(Path(__file__).resolve().parents[2] / "data" / "security_master.sqlite")
        self._sqlite_repo = SQLiteSecurityMasterRepository(sqlite_path)
        self._backend = None
        self._backend_name = None

    async def ensure_schema(self) -> bool:
        if self._backend_mode in {"postgres", "auto"} and self._pg_repo:
            ok = await self._pg_repo.ensure_schema()
            if ok:
                self._backend = self._pg_repo
                self._backend_name = "postgres"
                return True
            if self._backend_mode == "postgres":
                return False

        ok = await self._sqlite_repo.ensure_schema()
        if ok:
            self._backend = self._sqlite_repo
            self._backend_name = "sqlite"
            return True
        return False

    async def _with_backend(self, method: str, *args, **kwargs):
        if self._backend is None:
            await self.ensure_schema()
        backend = self._backend
        if backend is None:
            raise RuntimeError("No available security master backend")
        try:
            return await getattr(backend, method)(*args, **kwargs)
        except Exception as e:
            if (
                self._backend_name == "postgres"
                and self._backend_mode == "auto"
                and self._sqlite_repo is not None
            ):
                logger.warning("SecurityMaster: switching to sqlite backend", error=str(e))
                await self._sqlite_repo.ensure_schema()
                self._backend = self._sqlite_repo
                self._backend_name = "sqlite"
                return await getattr(self._backend, method)(*args, **kwargs)
            raise

    async def find_by_listing(self, exchange: str, ticker: str) -> Optional[Dict[str, Any]]:
        return await self._with_backend("find_by_listing", exchange, ticker)

    async def find_candidates(self, raw_symbol: str) -> List[Dict[str, Any]]:
        return await self._with_backend("find_candidates", raw_symbol)

    async def upsert_asset(
        self,
        asset_id: Optional[str],
        name: str,
        asset_type: str,
        country: Optional[str] = None,
        currency: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> str:
        return await self._with_backend(
            "upsert_asset",
            asset_id,
            name,
            asset_type,
            country,
            currency,
            timezone,
        )

    async def upsert_listing(
        self,
        asset_id: str,
        exchange: str,
        ticker: str,
        is_primary: bool = False,
    ) -> None:
        await self._with_backend(
            "upsert_listing",
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
        await self._with_backend(
            "add_alias",
            asset_id,
            alias,
            alias_type=alias_type,
            source=source,
            confidence=confidence,
            locale=locale,
        )

    async def add_identifier(self, asset_id: str, id_type: str, id_value: str) -> None:
        await self._with_backend("add_identifier", asset_id, id_type, id_value)

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
        await self._with_backend(
            "upsert_provider_symbol",
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
        self, asset_id: str, data_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return await self._with_backend("get_provider_symbols", asset_id, data_type)

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
        await self._with_backend(
            "upsert_alias_for_listing",
            normalized,
            alias,
            asset_type=asset_type,
            source=source,
            confidence=confidence,
            locale=locale,
        )

    @property
    def backend_name(self) -> Optional[str]:
        return self._backend_name
