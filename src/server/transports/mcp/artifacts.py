from __future__ import annotations

import json
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from mcp.types import CallToolResult, TextContent

_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "stock-mcp-outputs"


class ResourceVariant(str, Enum):
    TABLE = "table"
    FINANCIAL_CHART = "financial_chart"
    TECHNICAL_INDICATORS = "technical_indicators"
    CHIP_DISTRIBUTION = "chip_distribution"
    MONEY_FLOW = "money_flow"
    REAL_TIME_QUOTE = "real_time_quote"
    MARKET_LIQUIDITY = "market_liquidity"
    SECTOR_FLOW = "sector_flow"
    NORTH_BOUND_FLOW = "north_bound_flow"
    INFLATION_DATA = "inflation_data"
    MONEY_SUPPLY = "money_supply"
    PMI_DATA = "pmi_data"
    GDP_DATA = "gdp_data"
    SOCIAL_FINANCING = "social_financing"
    MACRO_INDICATOR = "macro_indicator"
    NEWS_CITATIONS = "news_citations"
    RESEARCH_REPORTS = "research_reports"
    SECTOR_UNIVERSE = "sector_universe"
    PEER_BENCHMARK = "peer_benchmark"
    VALUE_CHAIN = "value_chain"
    SECTOR_EVIDENCE_PACK = "sector_evidence_pack"
    US_TECHNICAL_CHART = "us_technical_chart"
    EARNINGS_TABLE = "earnings_table"
    CASH_FLOW_CHART = "cash_flow_chart"
    US_VALUATION = "us_valuation"
    INSTITUTIONAL_HOLDINGS = "institutional_holdings"
    US_VOLUME_ANALYSIS = "us_volume_analysis"
    US_SECTOR_ETF = "us_sector_etf"
    US_NEWS_SENTIMENT = "us_news_sentiment"
    OTHER = "other"


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "artifact"))
    slug = slug.strip("-")
    return slug or "artifact"


def _normalize_variant(variant: Union[str, ResourceVariant]) -> str:
    if isinstance(variant, ResourceVariant):
        return variant.value
    return str(variant or "other").strip().lower().replace("-", "_") or "other"


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _virtual_output_path(filename: str) -> str:
    return f"/mnt/user-data/outputs/{filename}"


def _descriptor_from_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "uri": envelope["uri"],
        "workspacePath": envelope.get("workspacePath") or envelope["uri"],
        "name": envelope["name"],
        "description": envelope.get("description") or "",
        "mimeType": envelope.get("mimeType") or "application/json",
        "displayInReport": bool(envelope.get("displayInReport", True)),
    }


def write_json_artifact(
    output_dir: str | Path | None = None,
    *,
    name: str,
    payload: Any,
) -> tuple[str, bytes]:
    slug = _slugify(name)
    filename = f"{slug}-{uuid4().hex[:8]}.json"
    output_root = Path(output_dir or _OUTPUT_ROOT)
    output_root.mkdir(parents=True, exist_ok=True)
    raw_bytes = _json_bytes(payload)
    host_path = output_root / filename
    host_path.write_bytes(raw_bytes)
    return filename, raw_bytes


def create_resource_descriptor(
    *,
    uri: str,
    name: str,
    description: str = "",
    mime_type: str = "application/json",
    display_in_report: bool = True,
) -> Dict[str, Any]:
    return {
        "uri": uri,
        "workspacePath": uri,
        "name": name,
        "description": description,
        "mimeType": mime_type,
        "displayInReport": display_in_report,
    }


def create_artifact_envelope(
    variant: Union[str, ResourceVariant],
    name: str,
    content: Any,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    visible_to_llm: bool = False,
    display_in_report: bool = True,
    source_tool: Optional[str] = None,
    schema_version: str = "v1",
) -> Dict[str, Any]:
    _ = visible_to_llm, source_tool
    normalized_variant = _normalize_variant(variant)
    wrapped_payload = {
        "type": normalized_variant,
        "schema_version": str(schema_version or "v1"),
        "data": content,
    }
    if metadata:
        ignored_meta_keys = {"schema_id", "schemaid", "render", "variant", "type"}
        meta = {
            key: value
            for key, value in dict(metadata).items()
            if str(key).strip().lower() not in ignored_meta_keys
        }
        if meta:
            wrapped_payload["meta"] = meta
    filename, _raw_bytes = write_json_artifact(
        name=name,
        payload=wrapped_payload,
    )
    return create_resource_descriptor(
        uri=_virtual_output_path(filename),
        name=name,
        description=description,
        mime_type="application/json",
        display_in_report=display_in_report,
    )


def create_file_artifact_envelope(
    name: str,
    workspace_path: Optional[str] = None,
    *,
    description: str = "",
    artifact_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    visible_to_llm: bool = False,
    display_in_report: bool = False,
    source_tool: Optional[str] = None,
    schema_version: str = "v1",
) -> Dict[str, Any]:
    _ = artifact_url, visible_to_llm, source_tool, schema_version, metadata
    path = Path(str(workspace_path or ""))
    filename = path.name or f"{_slugify(name)}-{uuid4().hex[:8]}"
    return create_resource_descriptor(
        uri=str(workspace_path or _virtual_output_path(filename)),
        name=name,
        description=description,
        mime_type=mime_type or "application/octet-stream",
        display_in_report=display_in_report,
    )


def create_mcp_tool_result(
    summary: str,
    *,
    resources: List[Dict[str, Any]] | None = None,
    no_data_reason: str | None = None,
    error: Dict[str, Any] | None = None,
    is_error: bool = False,
) -> CallToolResult:
    content = [TextContent(type="text", text=str(summary or "").strip())]
    structured_resources: list[Dict[str, Any]] = []
    for resource in resources or []:
        structured_resources.append(_descriptor_from_envelope(resource))

    structured_content: Dict[str, Any] = {"resources": structured_resources}
    if no_data_reason:
        structured_content["noDataReason"] = str(no_data_reason).strip()
    if error:
        structured_content["error"] = dict(error)

    return CallToolResult(
        content=content,
        structuredContent=structured_content,
        isError=is_error,
    )


def create_mcp_error_result(
    summary: str,
    *,
    error_code: str = "INTERNAL_ERROR",
    details: Optional[Dict[str, Any]] = None,
) -> CallToolResult:
    error = {"code": error_code, "message": str(summary or "").strip()}
    if details:
        error["details"] = dict(details)
    return create_mcp_tool_result(
        summary=summary,
        resources=[],
        error=error,
        is_error=True,
    )


def create_artifact_response(
    summary: str,
    artifact: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> CallToolResult:
    _ = metadata
    return create_mcp_tool_result(
        summary=summary,
        resources=[artifact],
        is_error=False,
    )


def create_artifact_list_response(
    summary: str,
    artifacts: List[Dict[str, Any]],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> CallToolResult:
    _ = metadata
    return create_mcp_tool_result(
        summary=summary,
        resources=list(artifacts),
        is_error=False,
    )


def create_file_artifact_response(
    summary: str,
    *,
    name: str,
    workspace_path: str,
    description: str = "",
    mime_type: str = "application/json",
    metadata: Optional[Dict[str, Any]] = None,
    display_in_report: bool = True,
) -> CallToolResult:
    artifact = create_file_artifact_envelope(
        name=name,
        workspace_path=workspace_path,
        description=description,
        mime_type=mime_type,
        metadata=metadata,
        display_in_report=display_in_report,
    )
    return create_artifact_response(summary=summary, artifact=artifact)


def create_chart_artifact(
    title: str,
    chart_type: str,
    series: Optional[List[Dict[str, Any]]] = None,
    periods: Optional[List[Dict[str, Any]]] = None,
    subtitle: str = "",
    unit: str = "",
    x_label: str = "",
    y_label: str = "",
    extra_content: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    description: str = "",
    name: Optional[str] = None,
    display_in_report: bool = True,
) -> Dict[str, Any]:
    payload = {
        "chart_type": chart_type,
        "title": title,
        "subtitle": subtitle,
        "unit": unit,
        "x_label": x_label,
        "y_label": y_label,
    }
    if extra_content:
        payload.update(extra_content)
    if series is not None:
        payload["series"] = series
    if periods is not None:
        payload["periods"] = periods
    return create_artifact_envelope(
        variant="chart",
        name=name or title,
        content=payload,
        description=description,
        metadata=metadata,
        display_in_report=display_in_report,
        schema_version="v1",
    )


def create_table_artifact(
    title: str,
    columns: List[Dict[str, str]],
    rows: List[Dict[str, Any]],
    tag: str = "",
    description: str = "",
) -> Dict[str, Any]:
    return create_artifact_envelope(
        variant=ResourceVariant.TABLE,
        name=title,
        content={"title": title, "tag": tag, "columns": columns, "rows": rows},
        description=description,
        schema_version="v1",
    )


def create_symbol_error_response(
    error: Exception,
    variant: Union[str, ResourceVariant],
    name: str,
) -> CallToolResult:
    _ = variant, name
    message = str(error)
    details = None
    if hasattr(error, "to_dict"):
        details = error.to_dict()
        message = details.get("message") or message
    summary = f"符号解析失败: {message}"
    return create_mcp_error_result(
        summary,
        error_code="SYMBOL_RESOLUTION_ERROR",
        details=details or {"message": message},
    )


def create_market_liquidity_artifact(
    north_flow: List[Dict[str, Any]],
    margin: List[Dict[str, Any]],
    description: str = "",
) -> Dict[str, Any]:
    return create_artifact_envelope(
        variant=ResourceVariant.MARKET_LIQUIDITY,
        name="Market Liquidity Data",
        content={"north_flow": north_flow, "margin": margin},
        description=description or "A-share market liquidity indicators",
    )


def create_news_citations_artifact(
    sources: List[Dict[str, str]],
    query: str,
    total_count: int,
    displayed_count: int,
) -> Dict[str, Any]:
    return create_artifact_envelope(
        variant=ResourceVariant.NEWS_CITATIONS,
        name=f"Sources: {query[:50]}",
        content={
            "sources": sources,
            "query": query,
            "total_count": total_count,
            "displayed_count": displayed_count,
        },
        description=f"Source links for: {query}",
        display_in_report=False,
        schema_version="v1",
    )


__all__ = [
    "ResourceVariant",
    "create_artifact_envelope",
    "create_artifact_list_response",
    "create_artifact_response",
    "create_chart_artifact",
    "create_file_artifact_envelope",
    "create_file_artifact_response",
    "create_market_liquidity_artifact",
    "create_mcp_error_result",
    "create_mcp_tool_result",
    "create_news_citations_artifact",
    "create_resource_descriptor",
    "create_symbol_error_response",
    "create_table_artifact",
    "write_json_artifact",
]
