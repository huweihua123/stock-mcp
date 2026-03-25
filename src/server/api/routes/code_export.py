# src/server/api/routes/code_export.py
"""HTTP data export endpoints for code-runtime workflows."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, status

from src.server.api.models.requests import (
    AlphaVantageJsonExportRequest,
    CodeExportResponse,
    TushareCsvExportRequest,
)
from src.server.core.dependencies import Container
from src.server.utils.logger import logger

router = APIRouter(prefix="/api/v1/code-export", tags=["Code Export"])


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return normalized.strip("._") or "dataset"


def _summary_for_no_data(provider: str, dataset: str) -> str:
    return f"{provider} {dataset} returned no data."


def _summary_for_error(provider: str, dataset: str) -> str:
    return f"{provider} {dataset} export failed."


def _virtual_export_path(filename: str) -> str:
    return f"/mnt/user-data/outputs/{filename}"


def _build_export_response(
    *,
    summary: str,
    filename: str,
    mime_type: str,
    provider: str,
    file_bytes: bytes | None = None,
    is_error: bool = False,
    no_data_reason: str | None = None,
    error: dict[str, Any] | None = None,
    extra_structured: dict[str, Any] | None = None,
) -> CodeExportResponse:
    content: list[dict[str, Any]] = [{"type": "text", "text": summary}]
    resources: list[dict[str, Any]] = []
    if file_bytes is not None:
        content.append(
            {
                "type": "file",
                "base64": base64.b64encode(file_bytes).decode("utf-8"),
                "mime_type": mime_type,
            }
        )
        resources.append(
            {
                "uri": _virtual_export_path(filename),
                "workspacePath": _virtual_export_path(filename),
                "name": filename,
                "description": summary,
                "mimeType": mime_type,
                "displayInReport": False,
            }
        )
    structured_content: dict[str, Any] = {
        "resources": resources,
        "provider": provider,
    }
    if no_data_reason:
        structured_content["noDataReason"] = no_data_reason
    if error:
        structured_content["error"] = error
    if extra_structured:
        structured_content.update(extra_structured)
    return CodeExportResponse(
        content=content,
        structuredContent=structured_content,
        isError=is_error,
    )


def _normalize_tushare_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in dict(kwargs or {}).items():
        if (
            isinstance(value, str)
            and ("date" in str(key).lower())
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip())
        ):
            normalized[key] = value.replace("-", "")
        else:
            normalized[key] = value
    return normalized


async def _export_tushare_csv_payload(api_name: str, kwargs: dict[str, Any]) -> CodeExportResponse:
    adapter = Container.tushare_adapter()
    client = adapter.tushare_conn.get_client()
    filename = f"{_slug(api_name)}.csv"
    if client is None:
        return _build_export_response(
            summary=_summary_for_error("tushare", api_name),
            filename=filename,
            mime_type="text/csv",
            provider="tushare",
            is_error=True,
            error={
                "code": "CLIENT_UNAVAILABLE",
                "message": "Tushare client unavailable",
            },
            extra_structured={"api_name": api_name},
        )

    api = getattr(client, api_name, None)
    if not callable(api):
        return _build_export_response(
            summary=_summary_for_error("tushare", api_name),
            filename=filename,
            mime_type="text/csv",
            provider="tushare",
            is_error=True,
            error={
                "code": "UNKNOWN_API",
                "message": f"Unknown Tushare api_name '{api_name}'",
            },
            extra_structured={"api_name": api_name},
        )

    normalized_kwargs = _normalize_tushare_kwargs(kwargs)
    frame = await adapter._run(api, **normalized_kwargs)
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return _build_export_response(
            summary=_summary_for_no_data("tushare", api_name),
            filename=filename,
            mime_type="text/csv",
            provider="tushare",
            no_data_reason="Query returned no data",
            extra_structured={"api_name": api_name, "kwargs": normalized_kwargs, "rows": 0},
        )

    csv_text = frame.to_csv(index=False)
    rows = int(len(frame))
    filename_hint = normalized_kwargs.get("ts_code") or normalized_kwargs.get("symbol") or api_name
    filename = f"{_slug(filename_hint)}_{_slug(api_name)}.csv"
    return _build_export_response(
        summary=f"Fetched {rows} rows from tushare {api_name}.",
        filename=filename,
        mime_type="text/csv",
        provider="tushare",
        file_bytes=csv_text.encode("utf-8"),
        extra_structured={
            "api_name": api_name,
            "kwargs": normalized_kwargs,
            "rows": rows,
            "columns": [str(column) for column in frame.columns.tolist()],
        },
    )


async def _export_alphavantage_json_payload(
    function: str,
    symbol: str,
    extra_params: dict[str, Any],
) -> CodeExportResponse:
    adapter = Container.alpha_vantage_adapter()
    filename = f"{_slug(symbol)}_{_slug(function)}.json"
    params = {
        "function": function,
        "symbol": symbol,
        **dict(extra_params or {}),
        "apikey": adapter.api_key,
    }
    payload = await adapter._fetch_json(params)
    if not payload:
        return _build_export_response(
            summary=_summary_for_no_data("alphavantage", function),
            filename=filename,
            mime_type="application/json",
            provider="alphavantage",
            no_data_reason="Query returned no data",
            extra_structured={"function": function, "symbol": symbol, "extra_params": dict(extra_params or {})},
        )
    if isinstance(payload, dict) and payload.get("Error Message"):
        return _build_export_response(
            summary=_summary_for_error("alphavantage", function),
            filename=filename,
            mime_type="application/json",
            provider="alphavantage",
            is_error=True,
            error={
                "code": "UPSTREAM_ERROR",
                "message": str(payload.get("Error Message")),
            },
            extra_structured={"function": function, "symbol": symbol, "extra_params": dict(extra_params or {})},
        )
    if isinstance(payload, dict) and payload.get("Information") and len(payload) <= 2:
        return _build_export_response(
            summary=_summary_for_error("alphavantage", function),
            filename=filename,
            mime_type="application/json",
            provider="alphavantage",
            is_error=True,
            error={
                "code": "UPSTREAM_NOTICE",
                "message": str(payload.get("Information")),
            },
            extra_structured={"function": function, "symbol": symbol, "extra_params": dict(extra_params or {})},
        )
    return _build_export_response(
        summary=f"Fetched Alpha Vantage {function} data for {symbol}.",
        filename=filename,
        mime_type="application/json",
        provider="alphavantage",
        file_bytes=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        extra_structured={
            "function": function,
            "symbol": symbol,
            "extra_params": dict(extra_params or {}),
        },
    )


@router.post(
    "/tushare/csv",
    response_model=CodeExportResponse,
    summary="Export raw Tushare tabular data for code runtime",
)
async def export_tushare_csv(request: TushareCsvExportRequest) -> CodeExportResponse:
    try:
        logger.info("API: export_tushare_csv called", api_name=request.api_name)
        return await _export_tushare_csv_payload(request.api_name, request.kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"API error in export_tushare_csv: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export tushare CSV: {exc}",
        ) from exc


@router.post(
    "/alphavantage/json",
    response_model=CodeExportResponse,
    summary="Export raw Alpha Vantage JSON data for code runtime",
)
async def export_alphavantage_json(request: AlphaVantageJsonExportRequest) -> CodeExportResponse:
    try:
        logger.info("API: export_alphavantage_json called", function=request.function, symbol=request.symbol)
        return await _export_alphavantage_json_payload(
            request.function,
            request.symbol,
            request.extra_params,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"API error in export_alphavantage_json: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export Alpha Vantage JSON: {exc}",
        ) from exc


__all__ = [
    "router",
    "_export_tushare_csv_payload",
    "_export_alphavantage_json_payload",
]
