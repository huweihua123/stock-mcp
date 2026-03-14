"""Strict tool result envelope normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

ENVELOPE_SCHEMA_VERSION = "1.0.0"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
ALLOWED_STATUSES = {STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED}

ALLOWED_ERROR_CODES = {
    "INVALID_ARGUMENT",
    "NO_DATA",
    "SYMBOL_NOT_FOUND",
    "RATE_LIMIT",
    "UPSTREAM_TIMEOUT",
    "NETWORK_ERROR",
    "UPSTREAM_5XX",
    "AUTH_INVALID",
    "PERMISSION_DENIED",
    "INTERNAL_ERROR",
    "UNKNOWN_ERROR",
}

_ERROR_META: dict[str, tuple[str, bool]] = {
    "INVALID_ARGUMENT": ("validation", False),
    "NO_DATA": ("business", False),
    "SYMBOL_NOT_FOUND": ("business", False),
    "RATE_LIMIT": ("upstream", True),
    "UPSTREAM_TIMEOUT": ("timeout", True),
    "NETWORK_ERROR": ("network", True),
    "UPSTREAM_5XX": ("upstream", True),
    "AUTH_INVALID": ("auth", False),
    "PERMISSION_DENIED": ("permission", False),
    "INTERNAL_ERROR": ("internal", False),
    "UNKNOWN_ERROR": ("unknown", False),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_code(code: str | None, default: str = "UNKNOWN_ERROR") -> str:
    candidate = str(code or "").strip().upper()
    if candidate in ALLOWED_ERROR_CODES:
        return candidate
    return default


def _error_payload(
    *,
    code: str,
    message: str,
    details: Any = None,
) -> dict[str, Any]:
    norm_code = _normalize_code(code)
    err_type, retriable = _ERROR_META[norm_code]
    payload: dict[str, Any] = {
        "code": norm_code,
        "message": str(message or ""),
        "type": err_type,
        "retriable": retriable,
    }
    if details not in (None, "", {}, []):
        payload["details"] = details
    return payload


def _is_envelope(obj: Any) -> bool:
    return isinstance(obj, dict) and {"ok", "error", "data", "meta"}.issubset(obj.keys())


def _coerce_summary(raw: Any, fallback: str = "") -> str:
    if isinstance(raw, str):
        return raw.strip()
    if raw is None:
        return fallback
    return str(raw).strip() or fallback


def _extract_artifacts(raw: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    if raw is None:
        return artifacts

    if isinstance(raw, list):
        for item in raw:
            artifacts.extend(_extract_artifacts(item))
        return artifacts

    if not isinstance(raw, dict):
        return artifacts

    if "structured_content" in raw and isinstance(raw["structured_content"], dict):
        return _extract_artifacts(raw["structured_content"])

    if "component_type" in raw:
        artifacts.append(raw)
        return artifacts

    if "artifact" in raw:
        artifacts.extend(_extract_artifacts(raw.get("artifact")))

    if "artifacts" in raw:
        artifacts.extend(_extract_artifacts(raw.get("artifacts")))

    if "data" in raw and isinstance(raw.get("data"), dict):
        nested_data = raw["data"]
        if "artifacts" in nested_data:
            artifacts.extend(_extract_artifacts(nested_data.get("artifacts")))
    return artifacts


def _looks_error_dict(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").strip().lower()
    return "error" in result or status in {"error", "failed", "failure"}


def _looks_partial_dict(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").strip().lower()
    partial_failures = result.get("partial_failures")
    if status == STATUS_PARTIAL:
        return True
    if isinstance(partial_failures, list) and partial_failures:
        return True
    if not _looks_error_dict(result):
        return False
    for key in ("artifact", "artifacts", "data", "payload", "items", "rows", "records", "result"):
        value = result.get(key)
        if value not in (None, "", {}, []):
            return True
    return False


def _extract_error_message(raw_error: Any) -> str:
    if isinstance(raw_error, str):
        return raw_error.strip() or "unknown error"
    if isinstance(raw_error, dict):
        for key in ("message", "reason", "detail", "error", "msg"):
            value = raw_error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(raw_error)
    if isinstance(raw_error, list) and raw_error:
        first = raw_error[0]
        return _extract_error_message(first)
    if raw_error is None:
        return "unknown error"
    return str(raw_error)


def _infer_error_code(message: str, *, default_code: str = "UNKNOWN_ERROR") -> str:
    msg = (message or "").lower()
    if not msg:
        return _normalize_code(default_code, default=default_code)

    if "rate limit" in msg or "too many requests" in msg or "429" in msg:
        return "RATE_LIMIT"
    if "timeout" in msg or "timed out" in msg or "超时" in msg:
        return "UPSTREAM_TIMEOUT"
    if "connection" in msg or "network" in msg or "dns" in msg or "连接" in msg:
        return "NETWORK_ERROR"
    if "unauthorized" in msg or "invalid token" in msg or "401" in msg or "认证" in msg:
        return "AUTH_INVALID"
    if "forbidden" in msg or "permission denied" in msg or "403" in msg or "权限" in msg:
        return "PERMISSION_DENIED"
    if "502" in msg or "503" in msg or "504" in msg or "upstream 5" in msg or "server error" in msg:
        return "UPSTREAM_5XX"
    if "symbol" in msg and ("not found" in msg or "不存在" in msg):
        return "SYMBOL_NOT_FOUND"
    if "no data" in msg or "empty data" in msg or "暂无数据" in msg or "无数据" in msg:
        return "NO_DATA"
    if "invalid argument" in msg or "参数" in msg or "required" in msg:
        return "INVALID_ARGUMENT"
    return _normalize_code(default_code, default=default_code)


def _normalize_partial_failures(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and {"code", "message", "type", "retriable"}.issubset(item.keys()):
            code = _normalize_code(str(item.get("code")), default="UNKNOWN_ERROR")
            normalized.append(
                _error_payload(
                    code=code,
                    message=str(item.get("message") or "partial failure"),
                    details=item.get("details"),
                )
            )
            continue
        message = _extract_error_message(item)
        code = _infer_error_code(message, default_code="UNKNOWN_ERROR")
        normalized.append(_error_payload(code=code, message=message, details=item))
    return normalized


def _make_error_artifact(tool_name: str, error: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "name": f"{tool_name} error",
        "content": {"error": error},
        "component_type": "tool_call",
        "description": error.get("message", ""),
        "metadata": {"tool_name": tool_name, "error_code": error.get("code")},
        "timestamp": _now_iso(),
        "visible_to_llm": True,
        "display_in_report": True,
    }


def _sanitize_payload(payload: Any) -> Any:
    """Strip duplicated artifact channels from payload to avoid dual source of truth."""
    if payload is None:
        return {}

    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"artifact", "artifacts"}:
                continue
            cleaned[key] = _sanitize_payload(value)
        return cleaned

    if isinstance(payload, list):
        return [_sanitize_payload(item) for item in payload]

    if isinstance(payload, tuple):
        return tuple(_sanitize_payload(item) for item in payload)

    return payload


def _build_envelope(
    *,
    tool_name: str,
    status: str,
    ok: bool,
    error: dict[str, Any] | None,
    summary: str,
    artifacts: list[dict[str, Any]],
    payload: Any,
    partial_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "error": error,
        "data": {
            "summary": summary,
            "artifacts": artifacts,
            "payload": _sanitize_payload(payload),
            "partial_failures": partial_failures,
        },
        "meta": {
            "status": status,
            "tool_name": tool_name,
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "timestamp": _now_iso(),
        },
    }


def success_envelope(tool_name: str, result: Any) -> dict[str, Any]:
    summary = ""
    if isinstance(result, dict):
        summary = _coerce_summary(result.get("summary"), "")
    elif isinstance(result, str):
        summary = result.strip()
    if not summary:
        summary = f"工具 {tool_name} 执行完成"
    artifacts = _extract_artifacts(result)
    return _build_envelope(
        tool_name=tool_name,
        status=STATUS_COMPLETED,
        ok=True,
        error=None,
        summary=summary,
        artifacts=artifacts,
        payload=result,
        partial_failures=[],
    )


def partial_envelope(
    tool_name: str,
    result: Any,
    *,
    summary: str | None = None,
    partial_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    artifacts = _extract_artifacts(result)
    pf = list(partial_failures or [])
    resolved_summary = _coerce_summary(summary, "")
    if not resolved_summary:
        resolved_summary = f"工具 {tool_name} 部分成功，存在 {len(pf)} 个失败项"
    return _build_envelope(
        tool_name=tool_name,
        status=STATUS_PARTIAL,
        ok=True,
        error=None,
        summary=resolved_summary,
        artifacts=artifacts,
        payload=result,
        partial_failures=pf,
    )


def failed_envelope(
    tool_name: str,
    *,
    code: str,
    message: str,
    details: Any = None,
    result: Any = None,
) -> dict[str, Any]:
    error = _error_payload(code=code, message=message, details=details)
    artifacts = _extract_artifacts(result)
    if not artifacts:
        artifacts = [_make_error_artifact(tool_name, error)]
    return _build_envelope(
        tool_name=tool_name,
        status=STATUS_FAILED,
        ok=False,
        error=error,
        summary=str(message),
        artifacts=artifacts,
        payload=result if result is not None else {},
        partial_failures=[],
    )


def normalize_envelope(tool_name: str, envelope: dict[str, Any]) -> dict[str, Any]:
    raw_meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
    raw_data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    raw_status = str(raw_meta.get("status") or "").strip().lower()
    raw_ok = bool(envelope.get("ok"))
    raw_error = envelope.get("error")

    derived_status = raw_status if raw_status in ALLOWED_STATUSES else STATUS_COMPLETED
    if derived_status == STATUS_COMPLETED:
        if raw_ok is False or raw_error:
            derived_status = STATUS_FAILED
        elif _normalize_partial_failures(raw_data.get("partial_failures")):
            derived_status = STATUS_PARTIAL

    summary = _coerce_summary(raw_data.get("summary"), "")
    artifacts = _extract_artifacts(raw_data.get("artifacts")) or _extract_artifacts(raw_data.get("payload"))
    payload = raw_data.get("payload", {})

    if derived_status == STATUS_FAILED:
        if isinstance(raw_error, dict):
            err_code = _normalize_code(str(raw_error.get("code")), default="UNKNOWN_ERROR")
            message = _extract_error_message(raw_error.get("message"))
            details = raw_error.get("details")
        else:
            message = _extract_error_message(raw_error)
            err_code = _infer_error_code(message, default_code="UNKNOWN_ERROR")
            details = raw_error
        return failed_envelope(
            tool_name=tool_name,
            code=err_code,
            message=message,
            details=details,
            result={"summary": summary, "artifacts": artifacts, "payload": payload},
        )

    if derived_status == STATUS_PARTIAL:
        partial_failures = _normalize_partial_failures(raw_data.get("partial_failures"))
        if not partial_failures and raw_error:
            partial_failures = _normalize_partial_failures(raw_error)
        return partial_envelope(
            tool_name=tool_name,
            result={"summary": summary, "artifacts": artifacts, "payload": payload},
            summary=summary,
            partial_failures=partial_failures,
        )

    return _build_envelope(
        tool_name=tool_name,
        status=STATUS_COMPLETED,
        ok=True,
        error=None,
        summary=summary or f"工具 {tool_name} 执行完成",
        artifacts=artifacts,
        payload=payload,
        partial_failures=[],
    )


def normalize_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    if _is_envelope(result):
        return normalize_envelope(tool_name, result)

    if isinstance(result, list):
        error_items: list[Any] = []
        success_items: list[Any] = []
        for item in result:
            if isinstance(item, dict) and _looks_error_dict(item):
                error_items.append(item)
            else:
                success_items.append(item)
        if error_items and success_items:
            failures = _normalize_partial_failures([item.get("error", item) for item in error_items])
            return partial_envelope(
                tool_name,
                result=result,
                summary=f"工具 {tool_name} 部分成功，成功 {len(success_items)} 项，失败 {len(error_items)} 项",
                partial_failures=failures,
            )
        if error_items and not success_items:
            message = _extract_error_message(error_items[0].get("error"))
            code = _infer_error_code(message, default_code="UNKNOWN_ERROR")
            return failed_envelope(
                tool_name,
                code=code,
                message=message,
                details={"errors": error_items},
                result=result,
            )
        return success_envelope(tool_name, result)

    if isinstance(result, dict):
        if _looks_partial_dict(result):
            partial_failures = _normalize_partial_failures(
                result.get("partial_failures") or result.get("error")
            )
            return partial_envelope(
                tool_name,
                result=result,
                summary=result.get("summary"),
                partial_failures=partial_failures,
            )

        if _looks_error_dict(result):
            raw_error = result.get("error", result.get("message"))
            message = _extract_error_message(raw_error)
            code = _infer_error_code(message, default_code="UNKNOWN_ERROR")
            return failed_envelope(
                tool_name,
                code=code,
                message=message,
                details=result,
                result=result,
            )

        return success_envelope(tool_name, result)

    return success_envelope(tool_name, result)


def normalize_tool_exception(
    tool_name: str,
    exc: Exception,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    message = str(exc) or "tool execution failed"
    exc_name = type(exc).__name__

    if exc_name == "TimeoutError":
        if timeout_seconds is not None:
            message = f"工具 {tool_name} 超时: 超过 {timeout_seconds:.1f}s 全局限制"
        return failed_envelope(
            tool_name,
            code="UPSTREAM_TIMEOUT",
            message=message,
            details={"exception": exc_name, "timeout_seconds": timeout_seconds},
        )

    if isinstance(exc, (ValueError, TypeError)):
        return failed_envelope(
            tool_name,
            code="INVALID_ARGUMENT",
            message=message,
            details={"exception": exc_name},
        )

    if isinstance(exc, PermissionError):
        return failed_envelope(
            tool_name,
            code="PERMISSION_DENIED",
            message=message,
            details={"exception": exc_name},
        )

    inferred = _infer_error_code(message, default_code="INTERNAL_ERROR")
    if inferred == "UNKNOWN_ERROR":
        inferred = "INTERNAL_ERROR"
    return failed_envelope(
        tool_name,
        code=inferred,
        message=message,
        details={"exception": exc_name},
    )
