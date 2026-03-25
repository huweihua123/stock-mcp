from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult, TextContent

from src.server.transports.mcp.artifacts import create_mcp_error_result, create_mcp_tool_result


def _normalize_summary(raw: Any, fallback: str = "") -> str:
    if isinstance(raw, dict):
        summary = raw.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        error = raw.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "").strip()
            if message:
                return message
    text = str(raw or fallback).strip()
    return text or fallback


def _as_call_tool_result(result: Any) -> CallToolResult | None:
    if isinstance(result, CallToolResult):
        return result
    if not isinstance(result, dict):
        return None
    if not {"content", "structuredContent", "isError"}.issubset(result.keys()):
        return None
    content = result.get("content") or []
    structured_content = result.get("structuredContent") or {"resources": []}
    is_error = bool(result.get("isError", False))
    return CallToolResult(
        content=content,
        structuredContent=structured_content,
        isError=is_error,
    )


def normalize_tool_result(tool_name: str, result: Any) -> CallToolResult:
    direct = _as_call_tool_result(result)
    if direct is not None:
        return direct
    summary = _normalize_summary(
        result,
        fallback=f"{tool_name} returned a non-MCP payload; expected content/structuredContent/isError.",
    )
    return create_mcp_error_result(summary, error_code="CONTRACT_VIOLATION")


def normalize_tool_exception(tool_name: str, exc: Exception, *, timeout_seconds: float | None = None) -> CallToolResult:
    if isinstance(exc, TimeoutError):
        code = "UPSTREAM_TIMEOUT"
        summary = f"{tool_name} timed out after {timeout_seconds or 0:.1f}s"
    else:
        code = "INTERNAL_ERROR"
        summary = str(exc).strip() or f"{tool_name} failed"
    return create_mcp_error_result(summary, error_code=code)


__all__ = ["normalize_tool_exception", "normalize_tool_result"]
