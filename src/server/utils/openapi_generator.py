"""
OpenAPI 文档生成器
从 FastMCP 工具定义生成 OpenAPI 3.0 规范
"""

import json
from typing import Any, Dict, List
from pathlib import Path

from fastmcp import FastMCP
from src.server.utils.logger import logger


class OpenAPIGenerator:
    """从 FastMCP 服务器生成 OpenAPI 文档"""

    def __init__(self, mcp: FastMCP, base_url: str = "http://localhost:9898"):
        self.mcp = mcp
        self.base_url = base_url
        self.tools = self._extract_tools_from_mcp()

    def _extract_tools_from_mcp(self) -> Dict[str, Any]:
        """从 FastMCP 实例提取工具定义"""
        tools = {}
        try:
            if hasattr(self.mcp, "_tool_manager"):
                tool_manager = self.mcp._tool_manager
                if hasattr(tool_manager, "_tools"):
                    for name, info in tool_manager._tools.items():
                        tools[name] = {
                            "name": name,
                            "description": info.get("description", ""),
                            "parameters": info.get("inputSchema", {}),
                        }
        except Exception as e:
            logger.warning(f"提取工具定义失败: {e}")
        return tools

    def generate_openapi_spec(self) -> Dict[str, Any]:
        """生成 OpenAPI 3.0 规范"""
        spec = {
            "openapi": "3.0.1",
            "info": {
                "title": "Stock MCP Tools API",
                "description": ("Stock MCP Tools API - 基于 FastMCP 的股票数据工具集"),
                "version": "1.0.0",
            },
            "servers": [
                {
                    "url": self.base_url,
                    "description": "Stock MCP Server",
                }
            ],
            "tags": self._generate_tags(),
            "paths": self._generate_paths(),
            "components": {"schemas": {}, "responses": {}, "securitySchemes": {}},
            "security": [],
        }
        return spec

    def _generate_tags(self) -> List[Dict[str, str]]:
        """生成标签定义"""
        return [
            {
                "name": "Asset Tools",
                "description": "资产搜索、价格查询、资产信息检索",
            },
            {
                "name": "Market Tools",
                "description": "市场数据查询",
            },
            {
                "name": "Fundamental Tools",
                "description": "财务数据和基本面分析",
            },
            {
                "name": "News Tools",
                "description": "新闻数据查询",
            },
            {
                "name": "Technical Tools",
                "description": "技术指标计算和交易信号",
            },
            {
                "name": "Filings Tools",
                "description": "SEC 和 A股公告查询",
            },
            {
                "name": "Research Tools",
                "description": "深度研究报告",
            },
        ]

    def _generate_paths(self) -> Dict[str, Any]:
        """生成路径定义 - 每个工具一个独立端点"""
        paths = {}
        tool_groups = {
            "get_kline_data": "Asset Tools",
            "get_stock_price_data": "Market Tools",
            "get_market_report": "Market Tools",
            "get_financial_reports": "Fundamental Tools",
            "get_dividend_info": "Fundamental Tools",
            "get_mainbz_info": "Fundamental Tools",
            "get_shareholder_info": "Fundamental Tools",
            "get_market_liquidity": "Market Tools",
            "get_market_money_flow": "Market Tools",
            "resolve_sector": "Market Tools",
            "get_sector_trend": "Market Tools",
            "get_sector_money_flow_history": "Market Tools",
            "get_sector_valuation_metrics": "Market Tools",
            "get_ggt_daily": "Market Tools",
            "get_money_supply": "Market Tools",
            "get_inflation_data": "Market Tools",
            "get_pmi_data": "Market Tools",
            "get_gdp_data": "Market Tools",
            "get_social_financing": "Market Tools",
            "get_interest_rates": "Market Tools",
            "get_us_economic_growth": "Market Tools",
            "get_us_inflation_employment": "Market Tools",
            "get_us_interest_rates": "Market Tools",
            "get_latest_news": "News Tools",
            "perform_deep_research": "Research Tools",
            "get_technical_indicators": "Technical Tools",
            "generate_trading_signal": "Technical Tools",
            "calculate_volatility": "Technical Tools",
            "fetch_periodic_sec_filings": "Filings Tools",
            "fetch_event_sec_filings": "Filings Tools",
            "fetch_ashare_filings": "Filings Tools",
            "resolve_sector_scope": "Research Tools",
            "build_sector_universe": "Research Tools",
            "build_peer_benchmark_table": "Research Tools",
            "build_sector_evidence_pack": "Research Tools",
            "build_sector_structure_snapshot": "Research Tools",
            "quality_gate_sector_report": "Research Tools",
        }

        for tool_name, group in tool_groups.items():
            tool_info = self.tools.get(tool_name, {})
            tool_desc = tool_info.get("description", f"{tool_name} 工具")

            endpoint = {
                "summary": tool_name,
                "deprecated": False,
                "description": tool_desc,
                "tags": [group],
                "parameters": [
                    {
                        "name": "Accept",
                        "in": "header",
                        "description": "",
                        "required": False,
                        "example": "application/json",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "Content-Type",
                        "in": "header",
                        "description": "",
                        "required": False,
                        "example": "application/json",
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": self._create_tool_request_schema(
                                tool_name, tool_info
                            ),
                            "example": self._create_tool_example(tool_name, tool_info),
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "成功响应",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {}}
                            }
                        },
                    }
                },
                "security": [],
            }

            tool_path = f"/{tool_name}"
            paths[tool_path] = {"post": endpoint}

        return paths

    def _create_tool_request_schema(
        self, tool_name: str, tool_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """为工具创建请求 schema"""
        params = tool_info.get("parameters", {})
        properties = params.get("properties", {})
        required = params.get("required", [])

        arguments_schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            arguments_schema["required"] = required

        return {
            "type": "object",
            "required": ["jsonrpc", "method", "params", "id"],
            "properties": {
                "jsonrpc": {
                    "type": "string",
                    "enum": ["2.0"],
                    "description": "JSON-RPC 版本",
                },
                "method": {
                    "type": "string",
                    "enum": ["tools/call"],
                    "description": "调用方法",
                },
                "params": {
                    "type": "object",
                    "required": ["name", "arguments"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": [tool_name],
                            "description": "工具名称",
                        },
                        "arguments": arguments_schema,
                    },
                },
                "id": {"type": "string", "description": "请求 ID"},
            },
        }

    def _create_tool_example(
        self, tool_name: str, tool_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """为工具创建请求示例"""
        params = tool_info.get("parameters", {})
        properties = params.get("properties", {})

        example_args = {}
        for prop_name, prop_schema in properties.items():
            prop_type = prop_schema.get("type", "string")
            example_val = prop_schema.get("example")

            if example_val is not None:
                example_args[prop_name] = example_val
            elif prop_type == "string":
                if "symbol" in prop_name.lower():
                    example_args[prop_name] = "NASDAQ:AAPL"
                elif "date" in prop_name.lower():
                    example_args[prop_name] = "2025-11-20"
                else:
                    example_args[prop_name] = f"example_{prop_name}"
            elif prop_type in ("integer", "number"):
                example_args[prop_name] = 100
            elif prop_type == "boolean":
                example_args[prop_name] = True
            elif prop_type == "array":
                example_args[prop_name] = []
            else:
                example_args[prop_name] = {}

        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": example_args},
            "id": f"apifox-test-{tool_name}",
        }

    def save_to_file(self, output_path: str | Path) -> None:
        """保存 OpenAPI 规范到文件"""
        spec = self.generate_openapi_spec()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)

        logger.info(f"✅ OpenAPI 规范已保存到: {output_path}")

    def print_spec(self) -> None:
        """打印 OpenAPI 规范"""
        spec = self.generate_openapi_spec()
        print(json.dumps(spec, indent=2, ensure_ascii=False))


def generate_openapi_from_mcp(
    mcp: FastMCP,
    output_path: str = "docs/openapi.json",
    base_url: str = "http://localhost:9898",
) -> None:
    """从 FastMCP 服务器生成 OpenAPI 文档"""
    generator = OpenAPIGenerator(mcp, base_url)
    generator.save_to_file(output_path)
