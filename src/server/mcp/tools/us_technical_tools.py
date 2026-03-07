# src/server/mcp/tools/us_technical_tools.py
"""MCP tools for US stock technical analysis.

Active Tools (4):
  - get_us_technical_indicators:  美股技术指标 (MA/RSI/MACD/布林带/ATR)
  - get_us_volume_analysis:       量价分析 (RVol / OBV趋势)
  - get_us_price_history:         K线数据 (OHLCV, 可指定interval)
  - us_technical_analysis_summary: 一键综合技术分析摘要 (对齐竞品)
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastmcp import FastMCP, Context

from src.server.core.use_cases import technical as technical_use_cases
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    ComponentType,
    create_artifact_envelope,
    create_artifact_response,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


def register_us_technical_tools(mcp: FastMCP):
    """Register US technical analysis tools."""

    # ------------------------------------------------------------------
    # get_us_technical_indicators  (主力工具，对齐竞品 get_us_technical_indicators)
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-technical", "indicators"})
    async def get_us_technical_indicators(
        symbol: str,
        days: int = 60,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get technical indicators for a US stock.

        Calculates MA(5/20/60), RSI(14), MACD, Bollinger Bands, ATR
        over the specified look-back window using daily OHLCV data.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:NVDA
            days: Look-back window in calendar days (default 60, min 30)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with indicator series and LLM summary
        """
        if ctx:
            await ctx.info(f"📉 计算美股技术指标: {symbol} ({days}d)")
        try:
            logger.info(
                "MCP tool: get_us_technical_indicators", symbol=symbol, days=days
            )
            # Reuse existing TechnicalService via calculate_technical_indicators
            period = f"{max(days, 30)}d"
            result = await technical_use_cases.calculate_technical_indicators(
                symbol, period=period, interval="1d"
            )

            if isinstance(result, dict) and result.get("error"):
                raise ValueError(result["error"])

            # Build concise LLM summary
            rows = result.get("rows") or []
            latest = rows[-1] if rows else {}
            rsi = latest.get("rsi14") or latest.get("rsi")
            close = latest.get("close")
            ma20 = latest.get("ma20") or latest.get("sma20")
            macd_val = latest.get("macd") or (
                (result.get("indicators") or {}).get("macd", {}) or {}
            ).get("macd_line", [None])
            if isinstance(macd_val, list):
                macd_val = macd_val[-1] if macd_val else None

            rsi_label = (
                "超买" if rsi and rsi > 70 else "超卖" if rsi and rsi < 30 else "中性"
            )
            trend = (
                "价格在MA20之上"
                if (close and ma20 and close > ma20)
                else "价格在MA20之下"
            )

            summary = (
                f"{symbol} 技术面: RSI={f'{rsi:.1f}' if rsi else 'N/A'}({rsi_label}), "
                f"{trend}, MACD={f'{macd_val:.3f}' if macd_val else 'N/A'}"
            )

            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} 技术指标",
                content=result,
                description=summary,
                metadata={"ticker": symbol, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ComponentType.US_TECHNICAL_CHART, f"{symbol} 技术指标"
            )
        except Exception as e:
            logger.error(
                "get_us_technical_indicators error", symbol=symbol, error=str(e)
            )
            summary = f"获取 {symbol} 技术指标失败: {e}"
            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} 技术指标",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # get_us_volume_analysis
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-technical", "volume"})
    async def get_us_volume_analysis(
        symbol: str,
        days: int = 30,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Analyze volume metrics for a US stock.

        Returns average volume (20d), current relative volume (RVol),
        OBV trend direction, and daily volume bar chart data.

        High RVol (>2x) with price breakout = strong signal.
        OBV divergence (price up, OBV down) = distribution warning.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:SPY, NASDAQ:QQQ
            days: Analysis window in calendar days (default 30)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with volume metrics and LLM summary
        """
        if ctx:
            await ctx.info(f"📊 量价分析: {symbol} ({days}d)")
        try:
            logger.info("MCP tool: get_us_volume_analysis", symbol=symbol, days=days)
            data = await technical_use_cases.get_us_volume_analysis(
                symbol, days=days
            )

            avg_vol = data.get("avg_volume_20d", 0)
            rvol = data.get("rvol")
            obv_trend = data.get("obv_trend", "unknown")
            cur_vol = data.get("current_volume", 0)

            rvol_label = (
                "异常放量"
                if rvol and rvol > 2
                else (
                    "温和放量"
                    if rvol and rvol > 1.2
                    else "缩量" if rvol and rvol < 0.7 else "正常"
                )
            )
            summary = (
                f"{symbol} 成交量分析: 当前量{_fmt_vol(cur_vol)} "
                f"(RVol={f'{rvol:.2f}x' if rvol else 'N/A'} {rvol_label}), "
                f"20日均量{_fmt_vol(avg_vol)}, OBV趋势{obv_trend}"
            )

            artifact = create_artifact_envelope(
                component_type=ComponentType.US_VOLUME_ANALYSIS,
                name=f"{symbol} 量价分析",
                content=data,
                description=summary,
                metadata={"ticker": symbol, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ComponentType.US_VOLUME_ANALYSIS, f"{symbol} 量价分析"
            )
        except Exception as e:
            logger.error("get_us_volume_analysis error", symbol=symbol, error=str(e))
            summary = f"获取 {symbol} 量价分析失败: {e}"
            artifact = create_artifact_envelope(
                component_type=ComponentType.US_VOLUME_ANALYSIS,
                name=f"{symbol} 量价分析",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # get_us_price_history (K线数据)
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-technical", "kline"})
    async def get_us_price_history(
        symbol: str,
        days: int = 60,
        interval: str = "1d",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get OHLCV price history (K-line data) for a US stock.

        Returns open, high, low, close, volume bars for each period.
        Use interval='1wk' for weekly bars, '1mo' for monthly.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:QQQ
            days: Number of calendar days to look back (default 60)
            interval: Bar interval: 1d (daily), 1wk (weekly), 1mo (monthly)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with OHLCV bars and LLM summary
        """
        if ctx:
            await ctx.info(f"📈 获取K线数据: {symbol} ({days}d/{interval})")
        try:
            logger.info(
                "MCP tool: get_us_price_history",
                symbol=symbol,
                days=days,
                interval=interval,
            )
            data = await technical_use_cases.get_us_price_history(
                symbol, days=days, interval=interval
            )

            bars = data.get("bars", [])
            if bars:
                first_close = bars[0].get("close", 0)
                last_close = bars[-1].get("close", 0)
                chg_pct = (
                    ((last_close - first_close) / first_close * 100)
                    if first_close
                    else 0
                )
                high = max(b["high"] for b in bars)
                low = min(b["low"] for b in bars)
                summary = (
                    f"{symbol} {days}d K线: 最新收盘{last_close:.2f}, "
                    f"区间涨跌{chg_pct:+.2f}%, 高{high:.2f}/低{low:.2f}, "
                    f"共{len(bars)}根{interval}K线"
                )
            else:
                summary = f"{symbol} K线数据为空"

            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} K线数据 ({interval})",
                content=data,
                description=summary,
                metadata={"ticker": symbol, "interval": interval, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ComponentType.US_TECHNICAL_CHART, f"{symbol} K线"
            )
        except Exception as e:
            logger.error("get_us_price_history error", symbol=symbol, error=str(e))
            summary = f"获取 {symbol} K线数据失败: {e}"
            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} K线",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

    # ------------------------------------------------------------------
    # us_technical_analysis_summary  (综合技术分析，对齐竞品同名工具)
    # ------------------------------------------------------------------
    @mcp.tool(tags={"us-technical", "summary"})
    async def us_technical_analysis_summary(
        symbol: str,
        days: int = 60,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Comprehensive US stock technical analysis summary.

        Combines price history, volume analysis, and technical indicators
        into a single structured report. Identifies trend, momentum,
        support/resistance levels, and key signals.

        Use this as the primary entry point for US stock technical analysis.

        Args:
            symbol: US stock ticker. Format: EXCHANGE:SYMBOL
                Examples: NASDAQ:AAPL, NYSE:TSLA, NASDAQ:NVDA, NYSE:SPY
            days: Analysis window in calendar days (default 60)
            ctx: FastMCP Context

        Returns:
            ArtifactResponse with comprehensive technical analysis
        """
        if ctx:
            await ctx.info(f"🔬 美股技术综合分析: {symbol} ({days}d)")
        try:
            import asyncio

            logger.info(
                "MCP tool: us_technical_analysis_summary", symbol=symbol, days=days
            )

            # Fetch price history and volume analysis in parallel
            price_task = technical_use_cases.get_us_price_history(
                symbol, days=days, interval="1d"
            )
            vol_task = technical_use_cases.get_us_volume_analysis(
                symbol, days=min(days, 30)
            )
            ind_task = technical_use_cases.calculate_technical_indicators(
                symbol, period=f"{max(days, 30)}d", interval="1d"
            )

            price_data, vol_data, ind_data = await asyncio.gather(
                price_task, vol_task, ind_task, return_exceptions=True
            )

            # Gracefully handle partial failures
            errors = []
            for name, result in [
                ("price", price_data),
                ("volume", vol_data),
                ("indicators", ind_data),
            ]:
                if isinstance(result, Exception):
                    errors.append(f"{name}: {result}")

            bars = (
                price_data.get("bars", [])
                if not isinstance(price_data, Exception)
                else []
            )
            latest_bar = bars[-1] if bars else {}
            close = latest_bar.get("close")

            # --- Volume signals ---
            rvol = vol_data.get("rvol") if not isinstance(vol_data, Exception) else None
            obv_trend = (
                vol_data.get("obv_trend", "N/A")
                if not isinstance(vol_data, Exception)
                else "N/A"
            )

            # --- Indicator signals ---
            ind_rows = (
                (ind_data.get("rows") or [])
                if not isinstance(ind_data, Exception)
                else []
            )
            latest_ind = ind_rows[-1] if ind_rows else {}
            rsi = latest_ind.get("rsi14") or latest_ind.get("rsi")
            ma20 = latest_ind.get("ma20") or latest_ind.get("sma20")
            ma60 = latest_ind.get("ma60") or latest_ind.get("sma60")

            # --- Trend classification ---
            signals: List[str] = []
            if close and ma20:
                signals.append(
                    "价格 > MA20 (短期上升趋势)"
                    if close > ma20
                    else "价格 < MA20 (短期下降趋势)"
                )
            if close and ma60:
                signals.append(
                    "价格 > MA60 (中期上升趋势)"
                    if close > ma60
                    else "价格 < MA60 (中期下降趋势)"
                )
            if rsi:
                if rsi > 70:
                    signals.append(f"RSI={rsi:.1f} 超买区间")
                elif rsi < 30:
                    signals.append(f"RSI={rsi:.1f} 超卖区间")
                else:
                    signals.append(f"RSI={rsi:.1f} 中性")
            if rvol and rvol > 1.5:
                signals.append(f"RVol={rvol:.2f}x 放量")
            if obv_trend == "up":
                signals.append("OBV向上 (资金流入)")
            elif obv_trend == "down":
                signals.append("OBV向下 (资金流出)")

            summary = (
                f"{symbol} 技术综合: 收盘{f'{close:.2f}' if close else 'N/A'}; "
                + ", ".join(signals[:4])
            )
            if errors:
                summary += f" [部分数据获取失败: {'; '.join(errors)}]"

            content = {
                "ticker": symbol,
                "days": days,
                "latest_price": close,
                "signals": signals,
                "price_bars": (
                    bars[-20:] if bars else []
                ),  # Last 20 bars for frontend chart
                "volume": {
                    "rvol": rvol,
                    "obv_trend": obv_trend,
                    "avg_volume_20d": (
                        vol_data.get("avg_volume_20d")
                        if not isinstance(vol_data, Exception)
                        else None
                    ),
                },
                "indicators": {
                    "rsi": rsi,
                    "ma20": ma20,
                    "ma60": ma60,
                },
                "errors": errors,
            }

            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} 技术综合分析",
                content=content,
                description=summary,
                metadata={"ticker": symbol, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, ComponentType.US_TECHNICAL_CHART, f"{symbol} 技术分析"
            )
        except Exception as e:
            logger.error(
                "us_technical_analysis_summary error", symbol=symbol, error=str(e)
            )
            summary = f"技术综合分析失败 ({symbol}): {e}"
            artifact = create_artifact_envelope(
                component_type=ComponentType.US_TECHNICAL_CHART,
                name=f"{symbol} 技术分析",
                content={"error": str(e)},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_vol(val) -> str:
    if val is None:
        return "N/A"
    v = float(val)
    if v >= 1e9:
        return f"{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K"
    return str(int(v))
