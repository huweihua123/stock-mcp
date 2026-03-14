from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.mcp.envelope import (
    ALLOWED_ERROR_CODES,
    normalize_tool_exception,
    normalize_tool_result,
)


def _assert_envelope(result: dict) -> None:
    assert set(result.keys()) == {"ok", "error", "data", "meta"}
    assert set(result["data"].keys()) == {
        "summary",
        "artifacts",
        "payload",
        "partial_failures",
    }
    assert set(result["meta"].keys()) == {
        "status",
        "tool_name",
        "schema_version",
        "timestamp",
    }
    assert result["meta"]["status"] in {"completed", "partial", "failed"}


def test_normalize_dict_error_to_failed_envelope() -> None:
    raw = {"error": "symbol or ts_code is required"}
    result = normalize_tool_result("get_money_flow", raw)

    _assert_envelope(result)
    assert result["ok"] is False
    assert result["meta"]["status"] == "failed"
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["error"]["code"] in ALLOWED_ERROR_CODES
    assert result["data"]["artifacts"]


def test_normalize_list_error_to_failed_envelope() -> None:
    raw = [{"error": "upstream 503"}]
    result = normalize_tool_result("fetch_periodic_sec_filings", raw)

    _assert_envelope(result)
    assert result["ok"] is False
    assert result["meta"]["status"] == "failed"
    assert result["error"]["code"] == "UPSTREAM_5XX"


def test_normalize_status_error_to_failed_envelope() -> None:
    raw = {"status": "error", "message": "rate limit exceeded"}
    result = normalize_tool_result("get_technical_indicators", raw)

    _assert_envelope(result)
    assert result["ok"] is False
    assert result["meta"]["status"] == "failed"
    assert result["error"]["code"] == "RATE_LIMIT"


def test_normalize_mixed_partial_result() -> None:
    raw = [{"error": "symbol not found"}, {"symbol": "600519.SH", "price": 1800}]
    result = normalize_tool_result("get_kline_data", raw)

    _assert_envelope(result)
    assert result["ok"] is True
    assert result["error"] is None
    assert result["meta"]["status"] == "partial"
    assert result["data"]["partial_failures"]
    assert result["data"]["partial_failures"][0]["code"] == "SYMBOL_NOT_FOUND"


def test_normalize_success_result_to_completed_envelope() -> None:
    raw = {
        "summary": "OK",
        "artifact": {
            "id": "a1",
            "name": "demo",
            "component_type": "table",
            "content": {"rows": []},
        },
    }
    result = normalize_tool_result("get_financial_reports", raw)

    _assert_envelope(result)
    assert result["ok"] is True
    assert result["meta"]["status"] == "completed"
    assert result["error"] is None
    assert len(result["data"]["artifacts"]) == 1
    assert "artifact" not in result["data"]["payload"]


def test_payload_strips_artifact_and_artifacts_keys_from_source() -> None:
    raw = {
        "summary": "OK",
        "artifact": {
            "id": "a1",
            "name": "demo",
            "component_type": "table",
            "content": {"rows": []},
        },
        "artifacts": [
            {
                "id": "a2",
                "name": "demo2",
                "component_type": "chart",
                "content": {"rows": []},
            }
        ],
        "payload": {
            "artifact": {"id": "nested-a"},
            "artifacts": [{"id": "nested-b"}],
            "rows": [1, 2, 3],
        },
        "ok_field": True,
    }
    result = normalize_tool_result("get_financial_reports", raw)

    _assert_envelope(result)
    payload = result["data"]["payload"]
    assert "artifact" not in payload
    assert "artifacts" not in payload
    assert "artifact" not in payload["payload"]
    assert "artifacts" not in payload["payload"]
    assert payload["payload"]["rows"] == [1, 2, 3]


def test_timeout_exception_normalized_to_failed_envelope() -> None:
    result = normalize_tool_exception(
        "fetch_event_sec_filings",
        TimeoutError(),
        timeout_seconds=30.0,
    )

    _assert_envelope(result)
    assert result["ok"] is False
    assert result["meta"]["status"] == "failed"
    assert result["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_generic_exception_normalized_to_failed_envelope() -> None:
    result = normalize_tool_exception("get_money_supply", RuntimeError("boom"))

    _assert_envelope(result)
    assert result["ok"] is False
    assert result["meta"]["status"] == "failed"
    assert result["error"]["code"] == "INTERNAL_ERROR"


def test_key_tool_legacy_errors_are_strict_envelopes() -> None:
    filings_result = normalize_tool_result(
        "fetch_periodic_sec_filings",
        [{"error": "fetch failed"}],
    )
    money_flow_result = normalize_tool_result(
        "get_money_flow",
        {"error": "symbol or ts_code is required", "component_type": "money_flow"},
    )
    technical_result = normalize_tool_result(
        "get_technical_indicators",
        {"error": "indicator crash"},
    )

    for item in (filings_result, money_flow_result, technical_result):
        _assert_envelope(item)
        assert item["meta"]["status"] == "failed"
