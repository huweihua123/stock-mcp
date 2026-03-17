"""Strict tool result envelope normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

ENVELOPE_SCHEMA_VERSION = "1.0.0"
RESULT_OK = "ok"
RESULT_NO_DATA = "no_data"
RESULT_ERROR = "error"
ALLOWED_RESULT_STATUSES = {RESULT_OK, RESULT_NO_DATA, RESULT_ERROR}
_MAINBZ_TOOL = "get_mainbz_info"

ALLOWED_ERROR_CODES = {
    "INVALID_ARGUMENT",
    "INVALID_ROUTE",
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
    "INVALID_ROUTE": ("routing", False),
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


def _normalize_result_status(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    status = raw.strip().lower()
    if status in ALLOWED_RESULT_STATUSES:
        return status
    if status in {"completed", "success", "ok"}:
        return RESULT_OK
    if status in {"partial", "no_data", "nodata"}:
        return RESULT_NO_DATA
    if status in {"failed", "error", "failure", "timeout"}:
        return RESULT_ERROR
    return None


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
    result_status = _normalize_result_status(result.get("result_status"))
    return (
        "error" in result
        or status in {"error", "failed", "failure"}
        or result_status == RESULT_ERROR
    )


def _looks_no_data_dict(result: dict[str, Any]) -> bool:
    result_status = _normalize_result_status(result.get("result_status"))
    status = str(result.get("status") or "").strip().lower()
    if result_status == RESULT_NO_DATA:
        return True
    if status == "no_data":
        return True
    if result.get("rows") == [] or result.get("chunks") == []:
        no_data_reason = str(result.get("no_data_reason") or "").strip()
        if no_data_reason:
            return True
    raw_error = result.get("error")
    if isinstance(raw_error, dict):
        if _normalize_code(str(raw_error.get("code")), default="UNKNOWN_ERROR") == "NO_DATA":
            return True
    if isinstance(raw_error, str):
        if _infer_error_code(raw_error, default_code="UNKNOWN_ERROR") == "NO_DATA":
            return True
    partial_failures = result.get("partial_failures")
    if isinstance(partial_failures, list):
        for item in partial_failures:
            if not isinstance(item, dict):
                continue
            if _normalize_code(str(item.get("code")), default="UNKNOWN_ERROR") == "NO_DATA":
                return True
    if "no data" in str(result.get("summary") or "").lower():
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

    if "invalid route" in msg or "wrong route" in msg or "us-only" in msg or "仅支持美股" in msg:
        return "INVALID_ROUTE"
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


def _append_invalid_arg_hint(tool_name: str, message: str) -> str:
    text = str(message or "").strip()
    hint = ""

    if tool_name == "get_market_money_flow":
        hint = (
            "参数提示: 支持 trade_date(YYYY-MM-DD, 可选)、top_n、include_outflow。"
            "days 为回溯别名参数，仅在未传 trade_date 时生效。"
        )
    elif tool_name in {"build_sector_universe", "build_sector_evidence_pack"}:
        hint = (
            "参数提示: symbols 可省略，或传入字符串数组；"
            "若不传将自动按 sector_name 构建样本池。"
        )
    elif tool_name == "quality_gate_sector_report":
        hint = "参数提示: evidence_pack 可省略；若提供需为对象结构。"

    if hint and hint not in text:
        return f"{text}\n\n{hint}" if text else hint
    return text


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


def _extract_mainbz_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    rows = raw.get("rows")
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    payload = raw.get("payload")
    if isinstance(payload, dict):
        nested_rows = payload.get("rows")
        if isinstance(nested_rows, list):
            return [item for item in nested_rows if isinstance(item, dict)]
    return []


def _build_mainbz_no_data_artifact(ts_code: str, reason: str) -> dict[str, Any]:
    summary = f"{ts_code} 主营构成暂无数据 (NO_DATA): {reason}"
    return {
        "id": str(uuid4()),
        "name": f"Main Business Composition (No Data): {ts_code}",
        "component_type": "table",
        "content": {
            "ts_code": ts_code,
            "status": "no_data",
            "reason": reason,
            "source": "fina_mainbz",
            "rows": [],
            "coverage": {
                "row_count": 0,
                "dimensions_found": [],
                "expected_dimensions": ["P", "D", "I"],
                "latest_period": None,
            },
            "source_type_required_next": [
                "structured_financial",
                "filings_or_web_news",
            ],
        },
        "description": summary,
        "metadata": {
            "type": "main_business",
            "source": "fina_mainbz",
            "result_status": "no_data",
        },
        "timestamp": _now_iso(),
        "visible_to_llm": True,
        "display_in_report": True,
    }


def _patch_mainbz_no_data(tool_name: str, result: Any) -> Any:
    if tool_name != _MAINBZ_TOOL or not isinstance(result, dict):
        return result

    rows = _extract_mainbz_rows(result)
    artifacts = _extract_artifacts(result)
    if rows or artifacts:
        return result

    ts_code = str(
        result.get("ts_code")
        or result.get("symbol")
        or result.get("ticker")
        or "unknown"
    )
    no_data_reason = (
        str(result.get("no_data_reason") or "").strip()
        or "fina_mainbz returned empty rows"
    )
    summary = (
        str(result.get("summary") or "").strip()
        or f"{ts_code} 主营构成暂无数据 (NO_DATA): {no_data_reason}. "
        "建议补证：改用财务/股东结构化工具，并用公告或网页检索补主营结构叙事。"
    )
    patched = dict(result)
    patched["summary"] = summary
    patched["artifact"] = _build_mainbz_no_data_artifact(ts_code, no_data_reason)
    patched["result_status"] = RESULT_NO_DATA
    patched["no_data_reason"] = no_data_reason
    patched.setdefault(
        "reroute_if_blocked",
        "If main business mix is required, route to filings/web search evidence.",
    )
    return patched


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


def _has_effective_payload(payload: Any) -> bool:
    if payload in (None, "", {}, []):
        return False
    if isinstance(payload, str):
        return bool(payload.strip())
    if isinstance(payload, (int, float, bool)):
        return True
    if isinstance(payload, list):
        return any(_has_effective_payload(item) for item in payload)
    if isinstance(payload, tuple):
        return any(_has_effective_payload(item) for item in payload)
    if isinstance(payload, dict):
        ignore = {
            "status",
            "result_status",
            "summary",
            "error",
            "message",
            "reason",
            "scope",
            "suggested_reroute",
            "retriable",
            "no_data_reason",
            "upstream_status",
        }
        for key, value in payload.items():
            if key in ignore:
                continue
            if _has_effective_payload(value):
                return True
        return False
    return True


def _summary_with_status(result_status: str, summary: str) -> str:
    body = _coerce_summary(summary, "")
    if body.lower().startswith("result_status="):
        return body
    if not body:
        body = "工具执行完成" if result_status == RESULT_OK else "工具返回无有效数据"
    return f"result_status={result_status} | {body}"


def _resolve_suggested_reroute(raw: Any, *, default_hint: str) -> str:
    if isinstance(raw, dict):
        for key in ("suggested_reroute", "reroute_if_blocked", "next_tool_hint"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return default_hint


def _extract_primary_error(raw_error: Any) -> tuple[str, str, Any]:
    if isinstance(raw_error, dict):
        explicit_code = _normalize_code(str(raw_error.get("code")), default="UNKNOWN_ERROR")
        message = _extract_error_message(raw_error.get("message") or raw_error)
        details = raw_error.get("details")
        return explicit_code, message, details
    message = _extract_error_message(raw_error)
    code = _infer_error_code(message, default_code="UNKNOWN_ERROR")
    return code, message, raw_error


def _build_envelope(
    *,
    tool_name: str,
    result_status: str,
    ok: bool,
    error: dict[str, Any] | None,
    summary: str,
    artifacts: list[dict[str, Any]],
    payload: Any,
    reason: str | None = None,
    scope: Any = None,
    retriable: bool | None = None,
    suggested_reroute: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "result_status": result_status,
        "summary": _summary_with_status(result_status, summary),
        "artifacts": artifacts,
        "payload": _sanitize_payload(payload),
    }
    if result_status in {RESULT_NO_DATA, RESULT_ERROR}:
        data["reason"] = str(reason or "")
        data["scope"] = scope if scope is not None else {}
        data["retriable"] = bool(retriable) if retriable is not None else False
        data["suggested_reroute"] = (
            suggested_reroute
            or "Switch source type and reroute to an alternative evidence capability."
        )
    return {
        "ok": ok,
        "error": error,
        "data": data,
        "meta": {
            "tool_name": tool_name,
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "timestamp": _now_iso(),
        },
    }


def success_envelope(tool_name: str, result: Any, *, summary: str | None = None) -> dict[str, Any]:
    resolved_summary = ""
    if isinstance(result, dict):
        resolved_summary = _coerce_summary(summary or result.get("summary"), "")
    elif isinstance(result, str):
        resolved_summary = _coerce_summary(summary or result, "")
    else:
        resolved_summary = _coerce_summary(summary, "")
    if not resolved_summary:
        resolved_summary = f"工具 {tool_name} 执行完成"
    artifacts = _extract_artifacts(result)
    payload = _sanitize_payload(result)
    if not artifacts and not _has_effective_payload(payload):
        return no_data_envelope(
            tool_name,
            result,
            reason="tool returned empty payload/artifacts",
            scope={"tool_name": tool_name},
        )
    return _build_envelope(
        tool_name=tool_name,
        result_status=RESULT_OK,
        ok=True,
        error=None,
        summary=resolved_summary,
        artifacts=artifacts,
        payload=payload,
    )


def no_data_envelope(
    tool_name: str,
    result: Any,
    *,
    reason: str | None = None,
    scope: Any = None,
    suggested_reroute: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    artifacts = _extract_artifacts(result)
    payload = _sanitize_payload(result)
    resolved_summary = _coerce_summary(summary, "")
    resolved_reason = _coerce_summary(reason, "")
    if not resolved_reason and isinstance(result, dict):
        resolved_reason = _coerce_summary(result.get("no_data_reason"), "")
    if not resolved_reason:
        resolved_reason = "query returned no data"
    if not resolved_summary:
        resolved_summary = f"工具 {tool_name} 无可用数据: {resolved_reason}"
    reroute_hint = _resolve_suggested_reroute(
        result if isinstance(result, dict) else {},
        default_hint=suggested_reroute or "Reroute to web/news/filings or adjust query scope.",
    )
    return _build_envelope(
        tool_name=tool_name,
        result_status=RESULT_NO_DATA,
        ok=False,
        error=None,
        summary=resolved_summary,
        artifacts=artifacts,
        payload=payload,
        reason=resolved_reason,
        scope=scope if scope is not None else {"tool_name": tool_name},
        retriable=False,
        suggested_reroute=reroute_hint,
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
    payload = _sanitize_payload(result if result is not None else {})
    reroute_hint = _resolve_suggested_reroute(
        result if isinstance(result, dict) else {},
        default_hint="Change route/source type before retrying.",
    )
    return _build_envelope(
        tool_name=tool_name,
        result_status=RESULT_ERROR,
        ok=False,
        error=error,
        summary=str(message),
        artifacts=artifacts,
        payload=payload,
        reason=str(message),
        scope=details if details not in (None, "", {}, []) else {"tool_name": tool_name},
        retriable=bool(error.get("retriable")),
        suggested_reroute=reroute_hint,
    )


def normalize_envelope(tool_name: str, envelope: dict[str, Any]) -> dict[str, Any]:
    raw_meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
    raw_data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    raw_error = envelope.get("error")
    summary = _coerce_summary(raw_data.get("summary"), "")
    artifacts = _extract_artifacts(raw_data.get("artifacts")) or _extract_artifacts(raw_data.get("payload"))
    payload = raw_data.get("payload", {})
    data_result_status = _normalize_result_status(raw_data.get("result_status"))
    legacy_status = _normalize_result_status(raw_meta.get("status"))

    derived = data_result_status or legacy_status
    if not derived:
        if raw_error:
            code, _, _ = _extract_primary_error(raw_error)
            derived = RESULT_NO_DATA if code == "NO_DATA" else RESULT_ERROR
        elif _looks_no_data_dict(raw_data):
            derived = RESULT_NO_DATA
        elif bool(envelope.get("ok")) is False:
            derived = RESULT_ERROR
        else:
            derived = RESULT_OK

    if tool_name == _MAINBZ_TOOL and derived == RESULT_OK and not artifacts:
        payload = _patch_mainbz_no_data(tool_name, payload)
        artifacts = _extract_artifacts(payload)
        if isinstance(payload, dict):
            summary = _coerce_summary(payload.get("summary"), summary)
            if _normalize_result_status(payload.get("result_status")) == RESULT_NO_DATA:
                derived = RESULT_NO_DATA

    if derived == RESULT_OK and not artifacts and not _has_effective_payload(payload):
        derived = RESULT_NO_DATA

    if derived == RESULT_ERROR:
        err_code, message, details = _extract_primary_error(raw_error)
        if err_code == "INVALID_ARGUMENT":
            message = _append_invalid_arg_hint(tool_name, message)
        return failed_envelope(
            tool_name=tool_name,
            code=err_code,
            message=message,
            details=details,
            result={"summary": summary, "artifacts": artifacts, "payload": payload},
        )

    if derived == RESULT_NO_DATA:
        reason = _coerce_summary(raw_data.get("reason"), "")
        if not reason and isinstance(payload, dict):
            reason = _coerce_summary(payload.get("no_data_reason"), "")
        if not reason and raw_error:
            _, msg, _ = _extract_primary_error(raw_error)
            reason = msg
        return no_data_envelope(
            tool_name=tool_name,
            result={"summary": summary, "artifacts": artifacts, "payload": payload},
            reason=reason or "query returned no data",
            scope=raw_data.get("scope") if isinstance(raw_data.get("scope"), dict) else {"tool_name": tool_name},
            suggested_reroute=_coerce_summary(raw_data.get("suggested_reroute"), ""),
            summary=summary,
        )

    return success_envelope(
        tool_name=tool_name,
        result={"summary": summary, "artifacts": artifacts, "payload": payload},
        summary=summary or f"工具 {tool_name} 执行完成",
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
        if error_items and not success_items:
            code, message, details = _extract_primary_error(error_items[0].get("error"))
            if code == "NO_DATA":
                return no_data_envelope(
                    tool_name,
                    result=result,
                    reason=message,
                    scope={"errors": error_items},
                )
            return failed_envelope(
                tool_name,
                code=code,
                message=message,
                details=details if details not in (None, "", {}, []) else {"errors": error_items},
                result=result,
            )
        if error_items and success_items:
            return failed_envelope(
                tool_name,
                code="INTERNAL_ERROR",
                message=f"Mixed success/error payload detected: success={len(success_items)}, error={len(error_items)}",
                details={"errors": error_items, "success_count": len(success_items)},
                result=result,
            )
        return success_envelope(tool_name, result)

    if isinstance(result, dict):
        result = _patch_mainbz_no_data(tool_name, result)
        if _looks_no_data_dict(result):
            reason = _coerce_summary(result.get("no_data_reason"), "")
            if not reason:
                raw_error = result.get("error")
                if raw_error:
                    code, message, _ = _extract_primary_error(raw_error)
                    if code == "NO_DATA":
                        reason = message
            return no_data_envelope(
                tool_name,
                result=result,
                reason=reason or "query returned no data",
                scope={"tool_name": tool_name},
                summary=result.get("summary"),
            )

        if _looks_error_dict(result):
            raw_error = result.get("error", result.get("message"))
            code, message, details = _extract_primary_error(raw_error)
            if code == "NO_DATA":
                return no_data_envelope(
                    tool_name,
                    result=result,
                    reason=message,
                    scope=details if isinstance(details, dict) else {"tool_name": tool_name},
                    summary=result.get("summary"),
                )
            if code == "INVALID_ARGUMENT":
                message = _append_invalid_arg_hint(tool_name, message)
            return failed_envelope(
                tool_name,
                code=code,
                message=message,
                details=details if details not in (None, "", {}, []) else result,
                result=result,
            )

        if _normalize_result_status(result.get("result_status")) == RESULT_OK:
            return success_envelope(tool_name, result, summary=result.get("summary"))

        if not _extract_artifacts(result) and not _has_effective_payload(_sanitize_payload(result)):
            return no_data_envelope(
                tool_name,
                result=result,
                reason="tool returned empty payload/artifacts",
                scope={"tool_name": tool_name},
                summary=result.get("summary"),
            )

        return success_envelope(tool_name, result, summary=result.get("summary"))

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
        message = _append_invalid_arg_hint(tool_name, message)
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
