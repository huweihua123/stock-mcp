from __future__ import annotations

import os
import sys

from mcp.types import CallToolResult, TextContent

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.server.mcp.envelope import normalize_tool_exception, normalize_tool_result
from src.server.transports.mcp.artifacts import (
    create_artifact_envelope,
    create_artifact_list_response,
    create_artifact_response,
    create_file_artifact_envelope,
)


def _assert_mcp_result(result: CallToolResult) -> None:
    assert isinstance(result, CallToolResult)
    assert isinstance(result.content, list)
    assert isinstance(result.structuredContent, dict)
    assert "resources" in result.structuredContent
    assert "result_status" not in result.structuredContent


def test_create_artifact_response_defaults_to_ok() -> None:
    artifact = create_artifact_envelope(
        variant="table",
        name="demo",
        content={"rows": []},
    )
    result = create_artifact_response("摘要", artifact)

    _assert_mcp_result(result)
    assert result.isError is False
    assert result.structuredContent["resources"][0]["name"] == "demo"
    assert isinstance(result.content[0], TextContent)
    assert len(result.content) == 1


def test_create_artifact_list_response_defaults_to_ok() -> None:
    artifacts = [
        create_artifact_envelope(
            variant="table",
            name="demo",
            content={"rows": []},
        )
    ]
    result = create_artifact_list_response("摘要", artifacts)

    _assert_mcp_result(result)
    assert result.isError is False
    assert result.structuredContent["resources"][0]["name"] == "demo"
    assert len(result.content) == 1


def test_normalize_success_result_to_minimal_contract() -> None:
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

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"
    assert result.content[0].text == "OK"


def test_normalize_multiple_artifacts_keeps_artifacts_list() -> None:
    raw = {
        "summary": "OK",
        "artifacts": [
            {
                "artifact_id": "a1",
                "name": "demo",
                "component_type": "table",
                "content": {"rows": []},
            },
            {
                "artifact_id": "a2",
                "name": "demo2",
                "component_type": "table",
                "content": {"rows": []},
            },
        ],
    }
    result = normalize_tool_result("get_financial_reports", raw)

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"
    assert len(result.structuredContent["resources"]) == 0


def test_normalize_no_data_dict_keeps_reason_and_reroute() -> None:
    raw = {
        "result_status": "no_data",
        "summary": "",
        "no_data_reason": "sector universe is empty",
        "suggested_reroute": "switch to filings",
        "artifacts": [
            {
                "artifact_id": "a1",
                "name": "sector-flow-empty",
                "component_type": "sector_flow",
                "content": {"records": []},
            }
        ],
    }
    result = normalize_tool_result("build_sector_universe", raw)

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"
    assert result.structuredContent["resources"] == []


def test_normalize_error_dict_to_minimal_contract() -> None:
    raw = {"error": "symbol or ts_code is required"}
    result = normalize_tool_result("get_money_flow", raw)

    _assert_mcp_result(result)
    assert result.isError is True
    assert "required" in result.content[0].text
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"


def test_legacy_status_without_result_status_is_contract_violation() -> None:
    raw = {"status": "error", "message": "rate limit exceeded"}
    result = normalize_tool_result("get_technical_indicators", raw)

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"


def test_normalize_file_artifact_shape() -> None:
    artifact = create_file_artifact_envelope(
        name="final_report.md",
        workspace_path="/mnt/user-data/outputs/final_report.md",
        mime_type="text/markdown",
    )
    raw = create_artifact_response("生成了最终报告文件", artifact)
    result = normalize_tool_result("present_files", raw)

    _assert_mcp_result(result)
    assert len(result.structuredContent["resources"]) == 1


def test_normalize_string_file_artifact_shape_rejected() -> None:
    result = normalize_tool_result("present_files", "/mnt/user-data/outputs/final_report.md")

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTRACT_VIOLATION"


def test_timeout_exception_normalized_to_minimal_error_contract() -> None:
    result = normalize_tool_exception(
        "fetch_event_sec_filings",
        TimeoutError(),
        timeout_seconds=30.0,
    )

    _assert_mcp_result(result)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "UPSTREAM_TIMEOUT"
