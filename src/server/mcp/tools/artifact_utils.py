# src/server/mcp/tools/artifact_utils.py
"""
Artifact 工具函数

MCP 工具返回结构化数据，前端渲染可视化组件
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union, TypedDict
from uuid import uuid4


class ComponentType(str, Enum):
    """组件类型"""

    # 核心
    PLAN_TASK = "plan_task"
    TOOL_CALL = "tool_call"
    # 表格
    TABLE = "table"
    # 股票数据
    FINANCIAL_CHART = "financial_chart"
    TECHNICAL_INDICATORS = "technical_indicators"
    CHIP_DISTRIBUTION = "chip_distribution"
    MONEY_FLOW = "money_flow"
    REAL_TIME_QUOTE = "real_time_quote"
    # 市场数据
    MARKET_LIQUIDITY = "market_liquidity"
    SECTOR_FLOW = "sector_flow"
    NORTH_BOUND_FLOW = "north_bound_flow"
    # 宏观数据
    INFLATION_DATA = "inflation_data"
    MONEY_SUPPLY = "money_supply"
    PMI_DATA = "pmi_data"
    GDP_DATA = "gdp_data"
    SOCIAL_FINANCING = "social_financing"
    MACRO_INDICATOR = "macro_indicator"
    # 新闻
    NEWS_CITATIONS = "news_citations"
    RESEARCH_REPORTS = "research_reports"
    # 美股专用
    US_TECHNICAL_CHART = "us_technical_chart"
    EARNINGS_TABLE = "earnings_table"
    CASH_FLOW_CHART = "cash_flow_chart"
    US_VALUATION = "us_valuation"
    INSTITUTIONAL_HOLDINGS = "institutional_holdings"
    US_VOLUME_ANALYSIS = "us_volume_analysis"
    US_SECTOR_ETF = "us_sector_etf"
    US_NEWS_SENTIMENT = "us_news_sentiment"
    # 其他
    OTHER = "other"


class ArtifactResponse(TypedDict):
    """MCP 工具的标准返回结构 (Data Layering)"""

    summary: str
    """给 LLM 看的摘要 (必须是字符串)"""

    artifact: Dict[str, Any]
    """给前端/Agent 内部使用的全量数据"""


class ArtifactListResponse(TypedDict):
    """MCP 工具的多产物返回结构"""

    summary: str
    """给 LLM 看的摘要 (必须是字符串)"""

    artifacts: List[Dict[str, Any]]
    """多个 Artifact"""


def create_artifact_envelope(
    component_type: Union[str, ComponentType],
    name: str,
    content: Any,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    visible_to_llm: bool = False,
    display_in_report: bool = True,
) -> Dict[str, Any]:
    """
    创建 Artifact 信封

    注意：此函数只创建 artifact 部分。
    如果需要返回给 Agent，请使用 create_artifact_response 组合 summary。

    Args:
        component_type: 组件类型
        name: 显示标题
        content: 数据内容
        description: 摘要描述
        metadata: 元信息
        visible_to_llm: 兼容字段(已废弃)；LLM 可见内容应通过 summary 控制
        display_in_report: 是否在报告中展示

    Returns:
        Artifact 字典
    """
    if isinstance(component_type, ComponentType):
        comp_type = component_type.value
    else:
        comp_type = str(component_type).lower().replace("-", "_")

    return {
        "id": str(uuid4()),
        "name": name,
        "content": content,
        "component_type": comp_type,
        "description": description,
        "metadata": metadata or {},
        "timestamp": datetime.utcnow().isoformat(),
        "visible_to_llm": visible_to_llm,
        "display_in_report": display_in_report,
    }


def create_artifact_response(
    summary: str, artifact: Dict[str, Any]
) -> ArtifactResponse:
    """
    创建标准 Artifact 返回结构

    Args:
        summary: 给 LLM 看的文本摘要
        artifact: create_artifact_envelope 创建的字典
    """
    return {"summary": summary, "artifact": artifact}


def create_artifact_list_response(
    summary: str, artifacts: List[Dict[str, Any]]
) -> ArtifactListResponse:
    """创建多产物返回结构."""
    return {
        "summary": summary,
        "artifacts": artifacts,
    }


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
    """统一图表 Artifact 结构."""
    content = {
        "chart_type": chart_type,
        "title": title,
        "subtitle": subtitle,
        "unit": unit,
        "x_label": x_label,
        "y_label": y_label,
    }
    if extra_content:
        content.update(extra_content)
    if series is not None:
        content["series"] = series
    if periods is not None:
        content["periods"] = periods

    return create_artifact_envelope(
        component_type="chart",
        name=name or title,
        content=content,
        description=description,
        metadata=metadata or {"type": "chart"},
        visible_to_llm=False,
        display_in_report=display_in_report,
    )


def create_table_artifact(
    title: str,
    columns: List[Dict[str, str]],
    rows: List[Dict[str, Any]],
    tag: str = "",
    description: str = "",
) -> Dict[str, Any]:
    """创建表格 Artifact"""
    return create_artifact_envelope(
        component_type=ComponentType.TABLE,
        name=title,
        content={"title": title, "tag": tag, "columns": columns, "rows": rows},
        description=description,
        metadata={"dataset": tag},
    )


def create_symbol_error_response(
    error: Exception,
    component_type: Union[str, ComponentType],
    name: str,
) -> Dict[str, Any]:
    """Create a consistent response for symbol resolution errors."""
    message = str(error)
    details = None
    if hasattr(error, "to_dict"):
        details = error.to_dict()
        message = details.get("message") or message
    summary = f"符号解析失败: {message}"
    content = {"error": details or {"message": message}}
    artifact = create_artifact_envelope(
        component_type=component_type,
        name=name,
        content=content,
        description=summary,
        visible_to_llm=True,
        display_in_report=True,
    )
    return create_artifact_response(summary=summary, artifact=artifact)


def create_market_liquidity_artifact(
    north_flow: List[Dict[str, Any]],
    margin: List[Dict[str, Any]],
    description: str = "",
) -> Dict[str, Any]:
    """创建市场流动性 Artifact"""
    return create_artifact_envelope(
        component_type=ComponentType.MARKET_LIQUIDITY,
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
    """创建新闻来源 Artifact"""
    return create_artifact_envelope(
        component_type=ComponentType.NEWS_CITATIONS,
        name=f"Sources: {query[:50]}",
        content=sources,
        description=f"Source links for: {query}",
        metadata={
            "query": query,
            "total_count": total_count,
            "displayed_count": displayed_count,
        },
        display_in_report=False,
    )
