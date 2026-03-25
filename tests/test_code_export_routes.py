from __future__ import annotations

import asyncio
import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.api.routes.code_export import (  # noqa: E402
    _export_alphavantage_json_payload,
    _export_tushare_csv_payload,
)
from src.server.core.dependencies import Container  # noqa: E402


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

    def daily(self, **kwargs):
        _ = kwargs
        return self._frame

    def index_weight(self, **kwargs):
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


def test_export_tushare_csv_success(monkeypatch) -> None:
    frame = pd.DataFrame([{"trade_date": "20260320", "close": 3999.1}])
    monkeypatch.setattr(Container, "tushare_adapter", lambda: _FakeTushareAdapter(frame))

    result = asyncio.run(_export_tushare_csv_payload("index_daily", {"ts_code": "000300.SH"}))

    dumped = result.model_dump(mode="json")
    assert result.isError is False
    assert dumped["structuredContent"]["rows"] == 1
    assert dumped["structuredContent"]["resources"][0]["mimeType"] == "text/csv"
    assert dumped["content"][1]["type"] == "file"
    assert "target_path" not in dumped


def test_export_tushare_csv_no_data(monkeypatch) -> None:
    frame = pd.DataFrame(columns=["trade_date", "close"])
    monkeypatch.setattr(Container, "tushare_adapter", lambda: _FakeTushareAdapter(frame))

    result = asyncio.run(_export_tushare_csv_payload("index_weight", {"index_code": "000300.SH"}))

    dumped = result.model_dump(mode="json")
    assert result.isError is False
    assert dumped["structuredContent"]["noDataReason"] == "Query returned no data"
    assert dumped["structuredContent"]["resources"] == []


def test_export_tushare_csv_normalizes_iso_dates(monkeypatch) -> None:
    frame = pd.DataFrame([{"trade_date": "20260320", "close": 3999.1}])
    adapter = _FakeTushareAdapter(frame)
    monkeypatch.setattr(Container, "tushare_adapter", lambda: adapter)

    result = asyncio.run(
        _export_tushare_csv_payload(
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
    assert adapter.last_kwargs == {
        "ts_code": "000300.SH",
        "start_date": "20260301",
        "end_date": "20260320",
    }
    assert dumped["structuredContent"]["kwargs"]["start_date"] == "20260301"
    assert dumped["structuredContent"]["kwargs"]["end_date"] == "20260320"


def test_export_alphavantage_json_success(monkeypatch) -> None:
    payload = {"Meta Data": {"2. Symbol": "AAPL"}, "Time Series (Daily)": {"2026-03-20": {"4. close": "201.3"}}}
    monkeypatch.setattr(Container, "alpha_vantage_adapter", lambda: _FakeAlphaVantageAdapter(payload))

    result = asyncio.run(_export_alphavantage_json_payload("TIME_SERIES_DAILY", "AAPL", {"outputsize": "full"}))

    dumped = result.model_dump(mode="json")
    assert result.isError is False
    assert dumped["structuredContent"]["extra_params"]["outputsize"] == "full"
    assert dumped["structuredContent"]["resources"][0]["mimeType"] == "application/json"
    assert dumped["content"][1]["type"] == "file"


def test_export_alphavantage_json_no_target_path_in_response(monkeypatch) -> None:
    payload = {"Symbol": "AAPL", "MarketCapitalization": "1000"}
    monkeypatch.setattr(Container, "alpha_vantage_adapter", lambda: _FakeAlphaVantageAdapter(payload))

    result = asyncio.run(_export_alphavantage_json_payload("OVERVIEW", "AAPL", {}))
    dumped = result.model_dump(mode="json")

    assert result.isError is False
    assert "target_path" not in dumped
