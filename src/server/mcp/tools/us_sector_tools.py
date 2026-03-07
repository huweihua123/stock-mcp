# src/server/mcp/tools/us_sector_tools.py
"""MCP tools for US sector ETF analysis.

Active Tools (1):
  - get_us_sector_etf_analysis: 美股行业ETF走势分析 (对齐竞品 get_us_sector_etf_analysis)

Sector → ETF mapping (内置，无需外部配置):
  technology → XLK, financials → XLF, healthcare → XLV,
  energy → XLE, consumer discretionary → XLY, utilities → XLU,
  industrials → XLI, materials → XLB, real estate → XLRE,
  communication → XLC, semiconductor → SOXX, biotech → XBI,
  software → IGV, cloud → SKYY, cybersecurity → HACK, ai → AIQ
"""

from typing import Any, Dict

from fastmcp import FastMCP, Context

from src.server.core.use_cases import technical as technical_use_cases
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    ComponentType,
    create_artifact_envelope,
    create_artifact_response,
)


def register_us_sector_tools(mcp: FastMCP):
    """Register US sector ETF analysis tools."""

    @mcp.tool(tags={"us-sector", "etf"})
    async def get_us_sector_etf_analysis(
        sector_name: str,
        days: int = 30,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Analyze a US sector via its representative ETF.

        Automatically maps sector name (English or Chinese) to the most
        representative SPDR/iShares ETF and returns OHLCV bars, period
        return, and trend summary.

        Supported sectors (partial list):
          - technology / 科技 → XLK
          - financials / 金融 → XLF
          - healthcare / 医疗 → XLV
          - energy / 能源 → XLE
          - semiconductor / 半导体 → SOXX
          - biotech / 生物技术 → XBI
          - cloud / 云计算 → SKYY
          - ai / 人工智能 → AIQ
          - cybersecurity / 网络安全 → HACK
          - real estate / 房地产 → XLRE
          (fallback to SPY if sector not recognized)

        Args:
            sector_name: Sector name in English or Chinese (case-insensitive)
                Examples: "technology", "半导体", "healthcare", "AI"
            days: Look-back window in calendar days (default 30)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with ETF bars, period return, and LLM summary
        """
        if ctx:
            await ctx.info(f"🏭 美股板块ETF分析: {sector_name} ({days}d)")
        try:
            logger.info(
                "MCP tool: get_us_sector_etf_analysis",
                sector=sector_name,
                days=days,
            )
            data = await technical_use_cases.get_us_sector_etf_analysis(
                sector_name, days=days
            )

            etf = data.get("etf_ticker", "SPY")
            total_chg = data.get("total_change_pct", 0.0)
            trend_summary = data.get("trend_summary", "")

            # Weekly aggregated change for LLM
            direction = "上涨" if total_chg > 0 else "下跌"
            strength = (
                "强势"
                if abs(total_chg) > 5
                else "温和" if abs(total_chg) > 2 else "震荡"
            )
            summary = (
                f"美股{sector_name}板块 ({etf}): "
                f"{days}日{direction}{abs(total_chg):.2f}% ({strength}). "
                f"{trend_summary}"
            )

            artifact = create_artifact_envelope(
                component_type=ComponentType.US_SECTOR_ETF,
                name=f"{sector_name} 板块ETF ({etf})",
                content=data,
                description=summary,
                metadata={
                    "sector": sector_name,
                    "etf_ticker": etf,
                    "days": days,
                    "total_change_pct": total_chg,
                },
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except Exception as e:
            logger.error(
                "get_us_sector_etf_analysis error",
                sector=sector_name,
                error=str(e),
            )
            summary = f"获取{sector_name}板块ETF数据失败: {e}"
            artifact = create_artifact_envelope(
                component_type=ComponentType.US_SECTOR_ETF,
                name=f"{sector_name} 板块ETF",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
