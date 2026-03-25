import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.domain.adapters.alpha_vantage_adapter import AlphaVantageAdapter


def _run(coro):
    return asyncio.run(coro)


class _DummyCache:
    async def get(self, *_args, **_kwargs):
        return None

    async def set(self, *_args, **_kwargs):
        return None


class _DebugAlphaVantageAdapter(AlphaVantageAdapter):
    async def _fetch_json(self, params):
        data = await super()._fetch_json(params)
        if data.get("Note") or data.get("Information") or data.get("Error Message"):
            raise AssertionError(f"Alpha Vantage error: {data}")
        return data


def _api_key():
    value = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if value:
        return value
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("ALPHA_VANTAGE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


@pytest.mark.integration
def test_alpha_spot_gold_has_data():
    if not _api_key():
        pytest.skip("ALPHA_VANTAGE_API_KEY not set")
    adapter = _DebugAlphaVantageAdapter(api_key=_api_key(), cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("OTC:XAUUSD", start_date, end_date, "1d")
    )
    assert prices, "Alpha Vantage spot gold (OTC:XAUUSD) returned no data"


@pytest.mark.integration
def test_alpha_spot_silver_has_data():
    if not _api_key():
        pytest.skip("ALPHA_VANTAGE_API_KEY not set")
    adapter = _DebugAlphaVantageAdapter(api_key=_api_key(), cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("OTC:XAGUSD", start_date, end_date, "1d")
    )
    assert prices, "Alpha Vantage spot silver (OTC:XAGUSD) returned no data"


@pytest.mark.integration
def test_alpha_fx_eurusd_has_data():
    if not _api_key():
        pytest.skip("ALPHA_VANTAGE_API_KEY not set")
    adapter = _DebugAlphaVantageAdapter(api_key=_api_key(), cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("FOREX:EURUSD", start_date, end_date, "1d")
    )
    assert prices, "Alpha Vantage FX (FOREX:EURUSD) returned no data"
