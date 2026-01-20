# src/server/mcp/tools/money_flow_tools.py
"""MCP tools for money flow analysis.
Provides stock money flow and north bound (HSGT) flow data.
Returns structured data (JSON) for frontend visualization.
"""

from typing import Any, Dict

from fastmcp import FastMCP

from src.server.core.dependencies import Container
from src.server.utils.logger import logger


def register_money_flow_tools(mcp: FastMCP):
    """Register money flow analysis tools."""

    @mcp.tool(tags={"money-flow", "market-core"})
    async def get_money_flow(symbol: str, days: int = 20) -> Dict[str, Any]:
        """获取个股资金流向数据

        分析主力资金和散户资金的流入流出情况，帮助判断资金动向。

        Args:
            symbol: 股票代码. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
            days: 获取最近 N 天数据 (默认 20 天)

        Returns:
            {
                "symbol": "600519.SH",
                "component_type": "money_flow",
                "data": {
                    "dates": ["2026-01-15", ...],
                    "main_net_inflow": [10000000, -5000000, ...],
                    "retail_net_inflow": [-5000000, ...],
                    "total_net_inflow": [5000000, ...]
                },
                "summary": {
                    "total_main_net": 50000000,
                    "total_retail_net": -30000000,
                    "trend": "主力持续流入",
                    "period_days": 20
                }
            }
        """
        try:
            service = Container.money_flow_service()
            logger.info("MCP tool called: get_money_flow", symbol=symbol, days=days)

            result = await service.get_money_flow(symbol, days)
            return result

        except Exception as e:
            logger.error(f"Get money flow failed: {e}", exc_info=True)
            return {"error": str(e), "symbol": symbol, "component_type": "money_flow"}

    @mcp.tool(tags={"money-flow", "market-core"})
    async def get_north_bound_flow(days: int = 30) -> Dict[str, Any]:
        """获取北向资金(沪深港通)流向数据

        追踪外资通过沪股通、深股通流入A股市场的资金情况。
        北向资金被视为"聪明钱"，其流向对市场有重要参考价值。

        Args:
            days: 获取最近 N 天数据 (默认 30 天)

        Returns:
            {
                "component_type": "north_bound_flow",
                "data": {
                    "dates": ["2026-01-01", ...],
                    "hk_to_sh": [100, 200, ...],  # 沪股通净流入(亿)
                    "hk_to_sz": [50, 80, ...],   # 深股通净流入(亿)
                    "total": [150, 280, ...]     # 合计
                },
                "summary": {
                    "total_net": 5000,
                    "period_days": 30
                }
            }
        """
        try:
            service = Container.money_flow_service()
            logger.info("MCP tool called: get_north_bound_flow", days=days)

            result = await service.get_north_bound_flow(days)
            return result

        except Exception as e:
            logger.error(f"Get north bound flow failed: {e}", exc_info=True)
            return {"error": str(e), "component_type": "north_bound_flow"}

    @mcp.tool(tags={"chip-distribution", "market-core"})
    async def get_chip_distribution(
        symbol: str, period_days: int = 120, price_bins: int = 50
    ) -> Dict[str, Any]:
        """获取筹码分布数据

        基于历史成交量和价格数据，估算当前筹码分布情况。
        筹码分布可以帮助判断股票的支撑位、压力位和主力成本区间。

        Args:
            symbol: 股票代码. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
            period_days: 回溯天数 (默认 120 天，越长越准确)
            price_bins: 价格区间数量 (默认 50)

        Returns:
            {
                "symbol": "600519.SH",
                "component_type": "chip_distribution",
                "current_price": 1800.0,
                "data": {
                    "price_levels": [1700, 1710, 1720, ...],
                    "chip_percent": [0.05, 0.12, 0.08, ...],
                    "profit_chip": [0.05, 0.12, ...],
                    "loss_chip": [0.0, 0.0, 0.08, ...]
                },
                "summary": {
                    "profit_ratio": 0.65,
                    "avg_cost": 1650.5,
                    "concentration_90": 150.0,
                    "main_peak_price": 1720.0
                }
            }
        """
        try:
            service = Container.chip_service()
            logger.info(
                "MCP tool called: get_chip_distribution",
                symbol=symbol,
                period_days=period_days,
                price_bins=price_bins,
            )

            result = await service.get_chip_distribution(
                symbol, period_days, price_bins
            )
            return result

        except Exception as e:
            logger.error(f"Get chip distribution failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "symbol": symbol,
                "component_type": "chip_distribution",
            }

    @mcp.tool(tags={"macro-data", "market-core"})
    async def get_macro_data(indicators: str = "CPI,PPI,M2,GDP") -> Dict[str, Any]:
        """获取宏观经济数据

        获取影响股市的关键宏观经济指标，帮助判断宏观经济趋势。

        Args:
            indicators: 指标列表，逗号分隔 (支持: CPI, PPI, M2, GDP, SOCIAL_RETAIL)

        Returns:
            {
                "component_type": "macro_indicator",
                "data": {
                    "CPI": {"current": 0.1, "yoy": 0.1, "trend": [...]},
                    "M2": {"current": 3000000, "yoy": 9.7, "trend": [...]}
                },
                "summary": {
                    "economic_outlook": "稳中向好",
                    "update_time": "2026-01-15"
                }
            }
        """
        try:
            adapter_manager = Container.adapter_manager()
            indicator_list = [i.strip().upper() for i in indicators.split(",")]
            logger.info("MCP tool called: get_macro_data", indicators=indicator_list)

            result = await adapter_manager.get_macro_data(indicator_list)
            return result

        except Exception as e:
            logger.error(f"Get macro data failed: {e}", exc_info=True)
            return {"error": str(e), "component_type": "macro_indicator"}
