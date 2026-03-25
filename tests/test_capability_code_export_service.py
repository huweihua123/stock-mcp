from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.capabilities.code_export.service import CodeExportCapabilityService


class _FakeTushareConn:
    def __init__(self, client):
        self._client = client

    def get_client(self):
        return self._client


class _FakeTushareClient:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def index_daily(self, **kwargs):
        _ = kwargs
        return self._frame


class _FakeTushareAdapter:
    def __init__(self, frame: pd.DataFrame):
        self.tushare_conn = _FakeTushareConn(_FakeTushareClient(frame))
        self.last_kwargs = None

    async def _run(self, func, **kwargs):
        self.last_kwargs = dict(kwargs)
        return func(**kwargs)


class _FakeAlphaVantageAdapter:
    def __init__(self, payload):
        self.api_key = "demo"
        self._payload = payload

    async def _fetch_json(self, params):
        _ = params
        return self._payload


def _runtime(frame: pd.DataFrame | None = None, payload=None):
    container = SimpleNamespace(
        tushare_adapter=lambda: _FakeTushareAdapter(frame if frame is not None else pd.DataFrame()),
        alpha_vantage_adapter=lambda: _FakeAlphaVantageAdapter(payload or {}),
    )
    return SimpleNamespace(container=container)


def test_code_export_service_exports_tushare_csv_and_normalizes_dates():
    frame = pd.DataFrame([{"trade_date": "20260320", "close": 3999.1}])
    runtime = _runtime(frame=frame)
    service = CodeExportCapabilityService(runtime)

    result = asyncio.run(
        service.export_tushare_csv(
            "index_daily",
            {
                "ts_code": "000300.SH",
                "start_date": "2026-03-01",
                "end_date": "2026-03-20",
            },
        )
    )

    dumped = result.model_dump(mode="json")
    assert result.isError is False
    assert dumped["structuredContent"]["rows"] == 1
    assert dumped["structuredContent"]["kwargs"]["start_date"] == "20260301"
    assert dumped["structuredContent"]["kwargs"]["end_date"] == "20260320"
    assert dumped["structuredContent"]["resources"][0]["mimeType"] == "text/csv"


def test_code_export_service_exports_alphavantage_json():
    payload = {"Meta Data": {"2. Symbol": "AAPL"}, "Time Series (Daily)": {"2026-03-20": {"4. close": "201.3"}}}
    runtime = _runtime(payload=payload)
    service = CodeExportCapabilityService(runtime)

    result = asyncio.run(service.export_alphavantage_json("TIME_SERIES_DAILY", "AAPL", {"outputsize": "full"}))
    dumped = result.model_dump(mode="json")

    assert result.isError is False
    assert dumped["structuredContent"]["extra_params"]["outputsize"] == "full"
    assert dumped["structuredContent"]["resources"][0]["mimeType"] == "application/json"
