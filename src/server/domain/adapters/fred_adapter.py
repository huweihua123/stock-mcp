# src/server/domain/adapters/fred_adapter.py
"""FRED adapter for US macroeconomic indicators.

Implements competitor-aligned US macro methods:
- get_us_economic_growth
- get_us_inflation_employment
- get_us_interest_rates
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from src.server.domain.adapters.base import BaseDataAdapter
from src.server.domain.types import (
    AdapterCapability,
    Asset,
    AssetPrice,
    AssetType,
    DataSource,
    Exchange,
)

class FredAdapter(BaseDataAdapter):
    """US macro data adapter backed by FRED API."""

    name = "fred"

    def __init__(self, api_key: str, cache, proxy_url: Optional[str] = None):
        super().__init__(DataSource.FRED)
        self.api_key = api_key
        self.cache = cache
        self.proxy_url = proxy_url
        self.base_url = "https://api.stlouisfed.org/fred"

    def get_capabilities(self) -> List[AdapterCapability]:
        # Market-wide macro endpoints are exchange-agnostic; keep US exchanges so
        # adapter remains discoverable in capability-based diagnostics.
        return [
            AdapterCapability(
                asset_type=AssetType.STOCK,
                exchanges={Exchange.NASDAQ, Exchange.NYSE, Exchange.AMEX},
            )
        ]

    # ------------------------------------------------------------------
    # Required abstract methods (not used for macro adapter)
    # ------------------------------------------------------------------

    async def get_asset_info(self, ticker: str) -> Optional[Asset]:
        return None

    async def get_real_time_price(self, ticker: str) -> Optional[AssetPrice]:
        return None

    async def get_historical_prices(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d",
    ) -> List[AssetPrice]:
        return []

    def convert_to_source_ticker(self, internal_ticker: str) -> str:
        return internal_ticker

    def convert_to_internal_ticker(self, source_ticker: str) -> str:
        return source_ticker

    # ------------------------------------------------------------------
    # FRED-backed methods
    # ------------------------------------------------------------------

    async def get_us_economic_growth(self, quarters: int = 20) -> Dict[str, Any]:
        if quarters <= 0:
            raise ValueError("quarters must be > 0")
        self._ensure_api_key()

        cache_key = f"fred:us_economic_growth:q{quarters}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        series = await self._fetch_series(
            "GDPC1",
            start=self._quarter_start_ago(quarters + 6),
            frequency="q",
        )
        if not series:
            return {
                "variant": "us_gdp",
                "source": "fred",
                "data": [],
            }

        rows: List[Dict[str, Any]] = []
        for idx, item in enumerate(series):
            val = item.get("value")
            if val is None:
                continue

            yoy = None
            if idx >= 4:
                prev = series[idx - 4].get("value")
                if prev and prev != 0:
                    yoy = (val / prev - 1.0) * 100.0

            qoq_annualized = None
            if idx >= 1:
                prev_q = series[idx - 1].get("value")
                if prev_q and prev_q > 0:
                    qoq_annualized = ((val / prev_q) ** 4 - 1.0) * 100.0

            rows.append(
                {
                    "quarter": item["date"][:7],
                    "real_gdp": round(val, 2),
                    "gdp_yoy": self._round_or_none(yoy),
                    "gdp_qoq_annualized": self._round_or_none(qoq_annualized),
                }
            )

        rows = rows[-quarters:]
        result = {
            "variant": "us_gdp",
            "source": "fred",
            "data": rows,
        }
        await self.cache.set(cache_key, result, ttl=3600)
        return result

    async def get_us_inflation_employment(self, months: int = 24) -> Dict[str, Any]:
        if months <= 0:
            raise ValueError("months must be > 0")
        self._ensure_api_key()

        cache_key = f"fred:us_inflation_employment:m{months}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        start = self._month_start_ago(months + 15)
        cpi_series = await self._fetch_series("CPIAUCSL", start=start, frequency="m")
        unrate_series = await self._fetch_series("UNRATE", start=start, frequency="m")

        cpi_map = {r["date"][:7]: r["value"] for r in cpi_series if r.get("value") is not None}
        unrate_map = {
            r["date"][:7]: r["value"] for r in unrate_series if r.get("value") is not None
        }

        months_sorted = sorted(set(cpi_map.keys()) | set(unrate_map.keys()))

        rows: List[Dict[str, Any]] = []
        for idx, month_key in enumerate(months_sorted):
            cpi_val = cpi_map.get(month_key)
            unemployment = unrate_map.get(month_key)

            cpi_yoy = None
            if cpi_val is not None and idx >= 12:
                prev_month = months_sorted[idx - 12]
                prev_val = cpi_map.get(prev_month)
                if prev_val and prev_val != 0:
                    cpi_yoy = (cpi_val / prev_val - 1.0) * 100.0

            rows.append(
                {
                    "month": month_key,
                    "cpi": self._round_or_none(cpi_val, digits=3),
                    "cpi_yoy": self._round_or_none(cpi_yoy),
                    "unemployment_rate": self._round_or_none(unemployment, digits=2),
                }
            )

        rows = rows[-months:]
        result = {
            "variant": "us_inflation",
            "source": "fred",
            "data": rows,
        }
        await self.cache.set(cache_key, result, ttl=3600)
        return result

    async def get_us_interest_rates(self, days: int = 180) -> Dict[str, Any]:
        if days <= 0:
            raise ValueError("days must be > 0")
        self._ensure_api_key()

        cache_key = f"fred:us_interest_rates:d{days}"
        cached = await self.cache.get(cache_key)
        if cached:
            return cached

        start = (datetime.now(UTC).date() - timedelta(days=days + 40)).isoformat()

        dgs2 = await self._fetch_series("DGS2", start=start, frequency="d")
        dgs10 = await self._fetch_series("DGS10", start=start, frequency="d")
        fed_funds = await self._fetch_series("FEDFUNDS", start=self._month_start_ago(24), frequency="m")

        dgs2_map = {r["date"]: r["value"] for r in dgs2 if r.get("value") is not None}
        dgs10_map = {r["date"]: r["value"] for r in dgs10 if r.get("value") is not None}

        fed_points = [
            (r["date"][:7], r["value"]) for r in fed_funds if r.get("value") is not None
        ]
        fed_points.sort(key=lambda x: x[0])

        all_days = sorted(set(dgs2_map.keys()) | set(dgs10_map.keys()))
        rows: List[Dict[str, Any]] = []
        for day in all_days:
            y2 = dgs2_map.get(day)
            y10 = dgs10_map.get(day)
            fed_rate = self._lookup_latest_monthly_rate(day[:7], fed_points)

            spread = None
            if y2 is not None and y10 is not None:
                spread = y10 - y2

            rows.append(
                {
                    "date": day,
                    "us2y": self._round_or_none(y2, digits=3),
                    "us10y": self._round_or_none(y10, digits=3),
                    "spread_10y_2y": self._round_or_none(spread, digits=3),
                    "fed_funds": self._round_or_none(fed_rate, digits=3),
                }
            )

        rows = rows[-days:]
        result = {
            "variant": "us_interest_rates",
            "source": "fred",
            "data": rows,
        }
        await self.cache.set(cache_key, result, ttl=3600)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_api_key(self) -> None:
        if not self.api_key:
            raise ValueError("FRED API key is not configured")

    @staticmethod
    def _round_or_none(val: Optional[float], digits: int = 2) -> Optional[float]:
        if val is None:
            return None
        try:
            return round(float(val), digits)
        except Exception:
            return None

    @staticmethod
    def _month_start_ago(months: int) -> str:
        today = datetime.now(UTC).date().replace(day=1)
        approx = today - timedelta(days=months * 31)
        return approx.replace(day=1).isoformat()

    @staticmethod
    def _quarter_start_ago(quarters: int) -> str:
        today = datetime.now(UTC).date()
        approx = today - timedelta(days=quarters * 95)
        return approx.isoformat()

    @staticmethod
    def _lookup_latest_monthly_rate(
        day_month: str, fed_points: List[tuple[str, float]]
    ) -> Optional[float]:
        latest = None
        for month_key, rate in fed_points:
            if month_key <= day_month:
                latest = rate
            else:
                break
        return latest

    async def _fetch_series(
        self,
        series_id: str,
        start: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "asc",
        }
        if start:
            params["observation_start"] = start
        if frequency:
            params["frequency"] = frequency

        timeout = httpx.Timeout(20.0)
        proxy = self.proxy_url if self.proxy_url else None

        url = f"{self.base_url}/series/observations"
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()

        observations = payload.get("observations", [])
        rows: List[Dict[str, Any]] = []
        for obs in observations:
            raw = str(obs.get("value", "")).strip()
            if not raw or raw == ".":
                value = None
            else:
                try:
                    value = float(raw)
                except Exception:
                    value = None

            d = str(obs.get("date", "")).strip()
            if not d:
                continue
            rows.append({"date": d, "value": value})

        return rows
