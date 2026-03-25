import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.domain.adapters.yahoo_adapter import YahooAdapter


def _run(coro):
    return asyncio.run(coro)

class _DummyCache:
    async def get(self, *_args, **_kwargs):
        return None

    async def set(self, *_args, **_kwargs):
        return None


@pytest.mark.integration
def test_yahoo_spot_silver_returns_empty():
    adapter = YahooAdapter(cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("OTC:XAGUSD", start_date, end_date, "1d")
    )
    assert prices == [], "Expected Yahoo spot silver (OTC:XAGUSD) to be unavailable"


@pytest.mark.integration
def test_yahoo_spot_gold_returns_empty():
    adapter = YahooAdapter(cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("OTC:XAUUSD", start_date, end_date, "1d")
    )
    assert prices == [], "Expected Yahoo spot gold (OTC:XAUUSD) to be unavailable"


@pytest.mark.integration
def test_yahoo_comex_silver_future_has_data():
    adapter = YahooAdapter(cache=_DummyCache(), proxy_url=None)
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)
    prices = _run(
        adapter.get_historical_prices("COMEX:SI", start_date, end_date, "1d")
    )
    assert prices, "Yahoo silver futures (COMEX:SI) returned no data"
