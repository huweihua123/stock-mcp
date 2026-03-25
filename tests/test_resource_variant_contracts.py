from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _run(coro):
    return asyncio.run(coro)


def _capture_tools(mcp_mock):
    captured = {}

    def fake_tool(**_deco_kwargs):
        def decorator(fn):
            captured[fn.__name__] = fn
            return fn

        return decorator

    mcp_mock.tool = fake_tool
    return captured


def test_a_share_tool_uses_variant_resource_descriptor() -> None:
    from src.server.mcp.tools.technical_tools import register_technical_tools

    mcp = MagicMock()
    tools = _capture_tools(mcp)
    rows = [
        {
            "trade_date": "20260320",
            "close": 123.4,
            "RSI": 58.2,
            "MACD": 1.2,
            "MACD_signal": 0.8,
        }
    ]

    with patch("src.server.mcp.tools.technical_tools.technical_use_cases") as uc:
        uc.calculate_technical_indicators = AsyncMock(return_value={"rows": rows})
        register_technical_tools(mcp)
        result = _run(tools["get_technical_indicators"]("600519.SH", limit=1))

    dumped = result.model_dump(mode="json")
    resource = dumped["structuredContent"]["resources"][0]
    serialized = json.dumps(dumped, ensure_ascii=False)

    assert result.isError is False
    assert len(dumped["content"]) == 1
    assert dumped["content"][0]["type"] == "text"
    assert resource["uri"].startswith("/mnt/user-data/outputs/")
    assert "render" not in resource
    assert resource["name"] == "Technical Indicators: 600519.SH"
    assert resource["mimeType"] == "application/json"
    assert "component_type" not in serialized
    assert "result_status" not in serialized
    assert "blob" not in serialized


def test_us_tool_uses_variant_resource_descriptor() -> None:
    from src.server.mcp.tools.us_sector_tools import register_us_sector_tools

    mcp = MagicMock()
    tools = _capture_tools(mcp)
    payload = {
        "etf_ticker": "XLK",
        "total_change_pct": 3.2,
        "trend_summary": "科技板块动能改善。",
        "bars": [{"date": "2026-03-20", "close": 210.5}],
    }

    with patch("src.server.mcp.tools.us_sector_tools.technical_use_cases") as uc:
        uc.get_us_sector_etf_analysis = AsyncMock(return_value=payload)
        register_us_sector_tools(mcp)
        result = _run(tools["get_us_sector_etf_analysis"]("technology", days=30))

    dumped = result.model_dump(mode="json")
    resource = dumped["structuredContent"]["resources"][0]
    serialized = json.dumps(dumped, ensure_ascii=False)

    assert result.isError is False
    assert len(dumped["content"]) == 1
    assert dumped["content"][0]["type"] == "text"
    assert resource["uri"].startswith("/mnt/user-data/outputs/")
    assert "render" not in resource
    assert resource["name"] == "technology 板块ETF (XLK)"
    assert resource["mimeType"] == "application/json"
    assert "component_type" not in serialized
    assert "result_status" not in serialized
    assert "blob" not in serialized


def test_kline_tool_returns_summary_and_resource_descriptor_only() -> None:
    from src.server.mcp.tools.asset_tools import register_asset_tools

    mcp = MagicMock()
    tools = _capture_tools(mcp)
    prices = [
        {
            "timestamp": "2026-03-20",
            "open_price": 10.0,
            "high_price": 10.8,
            "low_price": 9.9,
            "close_price": 10.5,
            "volume": 1000,
        },
        {
            "timestamp": "2026-03-21",
            "open_price": 10.5,
            "high_price": 10.9,
            "low_price": 10.2,
            "close_price": 10.8,
            "volume": 1200,
        },
    ]

    with patch("src.server.mcp.tools.asset_tools.market_use_cases") as uc:
        uc.get_historical_prices = AsyncMock(return_value=prices)
        register_asset_tools(mcp)
        result = _run(
            tools["get_kline_data"](
                "SZSE:000001",
                start_date="2026-03-20",
                end_date="2026-03-21",
                interval="1d",
            )
        )

    dumped = result.model_dump(mode="json")
    resource = dumped["structuredContent"]["resources"][0]
    serialized = json.dumps(dumped, ensure_ascii=False)

    assert result.isError is False
    assert len(dumped["content"]) == 1
    assert dumped["content"][0]["type"] == "text"
    assert "SZSE:000001 K线(1d, 2026-03-20~2026-03-21)" in dumped["content"][0]["text"]
    assert resource["uri"].startswith("/mnt/user-data/outputs/")
    assert resource["name"] == "SZSE:000001 历史价格"
    assert resource["mimeType"] == "application/json"
    assert "blob" not in serialized


def test_money_flow_tool_returns_summary_and_resource_descriptor_only() -> None:
    from src.server.mcp.tools.money_flow_tools import register_money_flow_tools

    mcp = MagicMock()
    tools = _capture_tools(mcp)
    payload = {
        "ts_code": "600519.SH",
        "summary": {
            "total_main_net": 2500000,
            "total_retail_net": -1200000,
            "trend": "净流入增强",
            "amount_unit": "cny",
        },
        "amount_unit": "cny",
        "records": [{"trade_date": "20260320", "main_net_inflow": 2500000}],
    }

    with patch("src.server.mcp.tools.money_flow_tools.money_flow_use_cases") as uc:
        uc.get_money_flow = AsyncMock(return_value=payload)
        register_money_flow_tools(mcp)
        result = _run(tools["get_money_flow"]("600519.SH", days=20))

    dumped = result.model_dump(mode="json")
    resource = dumped["structuredContent"]["resources"][0]
    serialized = json.dumps(dumped, ensure_ascii=False)

    assert result.isError is False
    assert len(dumped["content"]) == 1
    assert dumped["content"][0]["type"] == "text"
    assert "600519.SH主力资金流（20日）" in dumped["content"][0]["text"]
    assert resource["uri"].startswith("/mnt/user-data/outputs/")
    assert resource["name"] == "Money Flow: 600519.SH"
    assert resource["mimeType"] == "application/json"
    assert "blob" not in serialized
