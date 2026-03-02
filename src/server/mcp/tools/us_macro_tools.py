# src/server/mcp/tools/us_macro_tools.py
"""MCP tools for US macroeconomic analysis (competitor-aligned names)."""

from __future__ import annotations

from typing import Any, Dict

from fastmcp import Context, FastMCP

from src.server.core.use_cases import money_flow as money_flow_use_cases
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
)
from src.server.utils.logger import logger


def register_us_macro_tools(mcp: FastMCP):
    """Register US macro tools aligned with competitor naming."""

    @mcp.tool(tags={"us-macro", "macro"})
    async def get_us_economic_growth(
        quarters: int = 20,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get US real GDP trend and growth rates.

        Args:
            quarters: Number of recent quarters to return (default 20)
            ctx: FastMCP context
        """
        if ctx:
            await ctx.info(f"🇺🇸 宏观-经济增长: 最近{quarters}季")
        try:
            logger.info("MCP tool: get_us_economic_growth", quarters=quarters)
            result = await money_flow_use_cases.get_us_economic_growth(quarters)
            rows = result.get("data", []) if isinstance(result, dict) else []

            latest = rows[-1] if rows else {}
            q = latest.get("quarter", "N/A")
            yoy = latest.get("gdp_yoy")
            qoq = latest.get("gdp_qoq_annualized")
            summary = (
                f"美国经济增长(近{quarters}季): 最新{q} GDP同比"
                f"{_fmt_pct(yoy)}, 环比折年率{_fmt_pct(qoq)}"
            )

            artifact = create_artifact_envelope(
                component_type="us_gdp",
                name="US Real GDP Trend",
                content={"data": rows},
                description=summary,
                metadata={
                    "type": "us_gdp",
                    "title": "US Real GDP & Growth (YoY)",
                    "description": "Quarterly US Real GDP levels and Year-over-Year growth rates.",
                },
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
        except Exception as e:
            logger.error("get_us_economic_growth error", error=str(e))
            summary = f"获取美国经济增长数据失败: {e}"
            artifact = create_artifact_envelope(
                component_type="us_gdp",
                name="US Real GDP Trend",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"us-macro", "macro"})
    async def get_us_inflation_employment(
        months: int = 24,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get US inflation (CPI YoY) and unemployment trend.

        Args:
            months: Number of recent months to return (default 24)
            ctx: FastMCP context
        """
        if ctx:
            await ctx.info(f"🇺🇸 宏观-通胀就业: 最近{months}月")
        try:
            logger.info("MCP tool: get_us_inflation_employment", months=months)
            result = await money_flow_use_cases.get_us_inflation_employment(months)
            rows = result.get("data", []) if isinstance(result, dict) else []

            latest = rows[-1] if rows else {}
            m = latest.get("month", "N/A")
            cpi_yoy = latest.get("cpi_yoy")
            unrate = latest.get("unemployment_rate")
            summary = (
                f"美国通胀与就业(近{months}月): 最新{m} CPI同比"
                f"{_fmt_pct(cpi_yoy)}, 失业率{_fmt_pct(unrate)}"
            )

            artifact = create_artifact_envelope(
                component_type="us_inflation",
                name="Inflation vs Employment",
                content={"data": rows},
                description=summary,
                metadata={
                    "type": "us_inflation",
                    "title": "Inflation (CPI YoY) vs Unemployment",
                    "description": "Monthly comparison of US CPI YoY inflation and unemployment rate.",
                },
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
        except Exception as e:
            logger.error("get_us_inflation_employment error", error=str(e))
            summary = f"获取美国通胀与就业数据失败: {e}"
            artifact = create_artifact_envelope(
                component_type="us_inflation",
                name="Inflation vs Employment",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"us-macro", "macro"})
    async def get_us_interest_rates(
        days: int = 180,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get US rates: 2Y/10Y Treasury and Fed Funds.

        Args:
            days: Number of recent days to return (default 180)
            ctx: FastMCP context
        """
        if ctx:
            await ctx.info(f"🇺🇸 宏观-利率曲线: 最近{days}天")
        try:
            logger.info("MCP tool: get_us_interest_rates", days=days)
            result = await money_flow_use_cases.get_us_interest_rates(days)
            rows = result.get("data", []) if isinstance(result, dict) else []

            latest = rows[-1] if rows else {}
            d = latest.get("date", "N/A")
            y2 = latest.get("us2y")
            y10 = latest.get("us10y")
            fed = latest.get("fed_funds")
            spread = latest.get("spread_10y_2y")
            summary = (
                f"美国利率(近{days}天): 最新{d} 2Y/10Y={_fmt_pct(y2)}/{_fmt_pct(y10)}, "
                f"Fed Funds={_fmt_pct(fed)}, 利差(10Y-2Y)={_fmt_pct(spread)}"
            )

            artifact = create_artifact_envelope(
                component_type="us_interest_rates",
                name="US Interest Rates",
                content={"data": rows},
                description=summary,
                metadata={
                    "type": "us_interest_rates",
                    "title": "US Yield Curve & Fed Funds Rate",
                    "description": "Daily tracking of 10Y/2Y Treasury yields and Fed Funds rate.",
                },
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
        except Exception as e:
            logger.error("get_us_interest_rates error", error=str(e))
            summary = f"获取美国利率数据失败: {e}"
            artifact = create_artifact_envelope(
                component_type="us_interest_rates",
                name="US Interest Rates",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "N/A"
