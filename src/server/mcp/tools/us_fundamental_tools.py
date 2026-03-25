# src/server/mcp/tools/us_fundamental_tools.py
"""MCP tools for US stock fundamental data.

Active Tools (4):
  - get_earnings_history:        EPS历史 (实际 vs 预期 + surprise%)
  - get_cash_flow_quality:       现金流质量 (OCF / FCF / FCF-净利润比)
  - get_us_valuation_metrics:    美股估值 (PE/PS/PB/EV_EBITDA)
  - get_us_institutional_holdings: 机构持仓 (前15大机构 + 变动)
"""

from typing import Any, Dict
import time

from fastmcp import FastMCP, Context

from src.server.core.use_cases import fundamental as fundamental_use_cases
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    ResourceVariant,
    create_artifact_envelope,
    create_artifact_response,
    create_table_artifact,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


def _is_rate_limited_error(err: Exception) -> bool:
    """Detect upstream rate-limit errors (HTTP 429 / Too Many Requests)."""
    msg = str(err).lower()
    signals = ("429", "too many requests", "rate limit", "rate limited")
    return any(s in msg for s in signals)


def register_us_fundamental_tools(mcp: FastMCP):
    """Register US fundamental analysis tools."""

    # ------------------------------------------------------------------
    # get_earnings_history
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-fundamental", "earnings"})
    async def get_earnings_history(
        symbol: str,
        quarters: int = 8,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get EPS earnings history for a US stock.

        Shows actual EPS vs analyst estimate and surprise % for the last N quarters.
        Useful for evaluating earnings quality and beat/miss trends.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:NVDA
            quarters: Number of past quarters to return (default 8)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with earnings table and LLM summary
        """
        if ctx:
            await ctx.info(f"📊 获取EPS历史: {symbol} ({quarters}季度)")
        try:
            t0 = time.perf_counter()
            logger.info(
                "MCP tool: get_earnings_history", symbol=symbol, quarters=quarters
            )
            if ":" not in symbol:
                logger.warning(
                    "get_earnings_history received unqualified symbol, resolver may probe exchanges and become slower",
                    symbol=symbol,
                )
            data = await fundamental_use_cases.get_earnings_history(
                symbol, quarters=quarters
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "MCP tool: get_earnings_history finished",
                symbol=symbol,
                quarters=quarters,
                elapsed_ms=elapsed_ms,
                rows=len(data.get("quarters", [])) if isinstance(data, dict) else 0,
            )
            rows = data.get("quarters", [])

            # Build summary for LLM
            beats = sum(
                1 for r in rows if r.get("surprise_pct") and r["surprise_pct"] > 0
            )
            misses = sum(
                1 for r in rows if r.get("surprise_pct") and r["surprise_pct"] < 0
            )
            avg_surprise = sum(
                r["surprise_pct"] for r in rows if r.get("surprise_pct") is not None
            ) / max(len([r for r in rows if r.get("surprise_pct") is not None]), 1)
            summary = (
                f"{symbol} 近{len(rows)}季度EPS: 超预期{beats}次, 低预期{misses}次, "
                f"平均surprise {avg_surprise:+.1f}%"
            )

            artifact = create_table_artifact(
                title=f"{symbol} EPS历史 (近{quarters}季度)",
                columns=[
                    {"key": "date", "label": "财报日期"},
                    {"key": "actual_eps", "label": "实际EPS"},
                    {"key": "estimated_eps", "label": "预期EPS"},
                    {"key": "surprise_pct", "label": "Surprise%"},
                ],
                rows=rows,
                tag="earnings_history",
                description=summary,
            )
            artifact["variant"] = ResourceVariant.EARNINGS_TABLE.value
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ResourceVariant.EARNINGS_TABLE, f"{symbol} EPS历史"
            )
        except Exception as e:
            logger.error("get_earnings_history error", symbol=symbol, error=str(e))
            is_rate_limited = _is_rate_limited_error(e)
            if is_rate_limited:
                summary = (
                    f"获取 {symbol} EPS历史失败: 数据源限流(429/Too Many Requests)。"
                    f"建议 30-120 秒后重试，或降低并发请求。原始错误: {e}"
                )
                content = {
                    "error": str(e),
                    "rate_limited": True,
                    "error_code": "RATE_LIMITED",
                    "retry_after_seconds": 60,
                }
            else:
                summary = f"获取 {symbol} EPS历史失败: {e}"
                content = {"error": str(e)}
            artifact = create_artifact_envelope(
                variant=ResourceVariant.EARNINGS_TABLE,
                name=f"{symbol} EPS历史",
                content=content,
                description=summary,
                metadata={"rate_limited": is_rate_limited},
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # get_cash_flow_quality
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-fundamental", "cashflow"})
    async def get_cash_flow_quality(
        symbol: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Analyze cash flow quality for a US stock.

        Returns operating cash flow, capex, free cash flow, and FCF/net-income
        ratio by year. High FCF ratio (>0.8) indicates strong earnings quality.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:MSFT, NYSE:BRK-B
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with cash flow table and LLM summary
        """
        if ctx:
            await ctx.info(f"💵 分析现金流质量: {symbol}")
        try:
            logger.info("MCP tool: get_cash_flow_quality", symbol=symbol)
            data = await fundamental_use_cases.get_cash_flow_quality(symbol)
            annual = data.get("annual", [])

            latest = annual[-1] if annual else {}
            fcf = latest.get("free_cf")
            fcf_ratio = latest.get("fcf_ratio")
            quality = (
                "优秀"
                if fcf_ratio and fcf_ratio > 0.8
                else "良好" if fcf_ratio and fcf_ratio > 0.5 else "一般"
            )

            summary = (
                f"{symbol} 最近年度: FCF={_fmt_billions(fcf)}, "
                f"FCF/净利润={f'{fcf_ratio:.0%}' if fcf_ratio else 'N/A'} ({quality})"
            )

            artifact = create_table_artifact(
                title=f"{symbol} 现金流质量",
                columns=[
                    {"key": "year", "label": "年度"},
                    {"key": "operating_cf", "label": "经营现金流"},
                    {"key": "capex", "label": "资本支出"},
                    {"key": "free_cf", "label": "自由现金流"},
                    {"key": "net_income", "label": "净利润"},
                    {"key": "fcf_ratio", "label": "FCF/净利润"},
                ],
                rows=annual,
                tag="cash_flow_quality",
                description=summary,
            )
            artifact["variant"] = ResourceVariant.CASH_FLOW_CHART.value
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ResourceVariant.CASH_FLOW_CHART, f"{symbol} 现金流"
            )
        except Exception as e:
            logger.error("get_cash_flow_quality error", symbol=symbol, error=str(e))
            summary = f"获取 {symbol} 现金流质量失败: {e}"
            artifact = create_artifact_envelope(
                variant=ResourceVariant.CASH_FLOW_CHART,
                name=f"{symbol} 现金流",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # get_us_valuation_metrics
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-fundamental", "valuation"})
    async def get_us_valuation_metrics(
        symbol: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get US stock valuation metrics.

        Returns PE (TTM & Forward), PS, PB, EV/EBITDA, PEG ratio,
        market cap, enterprise value, beta, and dividend yield.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NASDAQ:AMZN, NYSE:JPM
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with valuation snapshot and LLM summary
        """
        if ctx:
            await ctx.info(f"📈 获取估值指标: {symbol}")
        try:
            logger.info("MCP tool: get_us_valuation_metrics", symbol=symbol)
            data = await fundamental_use_cases.get_us_valuation_metrics(symbol)

            pe = data.get("pe_ttm")
            pb = data.get("pb")
            ev_ebitda = data.get("ev_ebitda")
            mcap = data.get("market_cap")
            name = data.get("name", symbol)

            summary = (
                f"{name} ({symbol}): PE(TTM)={f'{pe:.1f}x' if pe else 'N/A'}, "
                f"PB={f'{pb:.2f}x' if pb else 'N/A'}, "
                f"EV/EBITDA={f'{ev_ebitda:.1f}x' if ev_ebitda else 'N/A'}, "
                f"市值={_fmt_billions(mcap)}"
            )

            artifact = create_artifact_envelope(
                variant=ResourceVariant.US_VALUATION,
                name=f"{symbol} 估值指标",
                content=data,
                description=summary,
                metadata={"ticker": symbol, "sector": data.get("sector", "")},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ResourceVariant.US_VALUATION, f"{symbol} 估值"
            )
        except Exception as e:
            logger.error("get_us_valuation_metrics error", symbol=symbol, error=str(e))
            summary = f"获取 {symbol} 估值指标失败: {e}"
            artifact = create_artifact_envelope(
                variant=ResourceVariant.US_VALUATION,
                name=f"{symbol} 估值",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # get_us_institutional_holdings
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-fundamental", "institutional"})
    async def get_us_institutional_holdings(
        symbol: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get institutional holdings for a US stock.

        Returns top 15 institutional holders with shares held, percentage,
        and recent change direction. Useful for tracking smart money flows.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:META
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with institutional holdings table and LLM summary
        """
        if ctx:
            await ctx.info(f"🏦 获取机构持仓: {symbol}")
        try:
            logger.info("MCP tool: get_us_institutional_holdings", symbol=symbol)
            data = await fundamental_use_cases.get_us_institutional_holdings(symbol)
            holders = data.get("holders", [])

            top3 = [h["name"] for h in holders[:3] if h.get("name")]
            top3_str = ", ".join(top3) if top3 else "无数据"
            inflows = sum(
                1 for h in holders if h.get("change_pct") and h["change_pct"] > 0
            )
            outflows = sum(
                1 for h in holders if h.get("change_pct") and h["change_pct"] < 0
            )
            summary = (
                f"{symbol} 机构持仓: 共{len(holders)}家, "
                f"增仓{inflows}家/减仓{outflows}家, "
                f"前三: {top3_str}"
            )

            artifact = create_table_artifact(
                title=f"{symbol} 机构持仓 (前15)",
                columns=[
                    {"key": "name", "label": "机构名称"},
                    {"key": "pct_held", "label": "持仓比例%"},
                    {"key": "shares", "label": "持股数"},
                    {"key": "change_pct", "label": "变动%"},
                    {"key": "filing_date", "label": "申报日期"},
                ],
                rows=holders,
                tag="institutional_holdings",
                description=summary,
            )
            artifact["variant"] = ResourceVariant.INSTITUTIONAL_HOLDINGS.value
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ResourceVariant.INSTITUTIONAL_HOLDINGS, f"{symbol} 机构持仓"
            )
        except Exception as e:
            logger.error(
                "get_us_institutional_holdings error", symbol=symbol, error=str(e)
            )
            summary = f"获取 {symbol} 机构持仓失败: {e}"
            artifact = create_artifact_envelope(
                variant=ResourceVariant.INSTITUTIONAL_HOLDINGS,
                name=f"{symbol} 机构持仓",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_billions(val) -> str:
    """Format a large number as billions/millions string."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except Exception:
        return "N/A"
    if abs(v) >= 1e12:
        return f"${v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"
