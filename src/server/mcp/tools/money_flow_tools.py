# src/server/mcp/tools/money_flow_tools.py
"""MCP tools for money flow analysis.
Provides stock money flow and north bound (HSGT) flow data.
Returns structured data (JSON) for frontend visualization.

使用新版 Artifact 结构:
- variant 标准化
- 参照竞品格式
"""

from datetime import datetime, timedelta
import inspect
from typing import Any, Dict

from fastmcp import FastMCP, Context

from src.server.core.use_cases import money_flow as money_flow_use_cases
from src.server.core.dependencies import Container
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_artifact_list_response,
    ResourceVariant,
    create_chart_artifact,
    create_mcp_error_result,
    create_mcp_tool_result,
    create_table_artifact,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols import to_ts_code


def register_money_flow_tools(mcp: FastMCP):
    """Register money flow analysis tools."""

    async def _resolve_ts_code(raw_symbol: str) -> str:
        resolved = await Container.market_gateway().resolve_ticker(raw_symbol)
        return to_ts_code(resolved)

    def _unit_label(unit: str) -> str:
        return {
            "cny": "元",
            "10k_cny": "万元",
            "100m_cny": "亿元",
            "unknown": "原始单位",
        }.get(unit, unit or "原始单位")

    def _to_cny(val: Any, unit: str) -> float | None:
        if not isinstance(val, (int, float)):
            return None
        if unit == "cny":
            return float(val)
        if unit == "10k_cny":
            return float(val) * 1e4
        if unit == "100m_cny":
            return float(val) * 1e8
        return None

    def _fmt_amount(val: Any, unit: str = "unknown") -> str:
        cny = _to_cny(val, unit)
        if cny is not None:
            if abs(cny) >= 1e8:
                return f"{cny / 1e8:.2f}亿"
            if abs(cny) >= 1e4:
                return f"{cny / 1e4:.2f}万"
            return f"{cny:.0f}元"
        if isinstance(val, (int, float)):
            return f"{float(val):.2f}{_unit_label(unit)}"
        return "N/A"

    def _build_no_data_response(
        *,
        summary: str,
        artifact: Dict[str, Any],
        reason: str,
        scope: Dict[str, Any],
        suggested_reroute: str,
    ) -> Dict[str, Any]:
        scope_payload: Dict[str, Any] = dict(scope) if isinstance(scope, dict) else {}
        scope_payload.setdefault("retry_same_query_allowed", False)
        guard_parts = [
            str(scope_payload.get("tool") or ""),
            str(
                scope_payload.get("sector_id")
                or scope_payload.get("resolved_sector_id")
                or scope_payload.get("resolved_sector")
                or scope_payload.get("sector_name")
                or scope_payload.get("symbol")
                or ""
            ),
            str(scope_payload.get("days") or scope_payload.get("sample_size") or ""),
        ]
        scope_payload.setdefault("loop_guard_key", "|".join(guard_parts))

        reroute_text = str(suggested_reroute or "").strip()
        loop_guard_prefix = "不要重复同参调用同一工具；"
        if loop_guard_prefix not in reroute_text:
            reroute_text = f"{loop_guard_prefix}{reroute_text}" if reroute_text else loop_guard_prefix

        return create_mcp_tool_result(
            summary,
            resources=[],
            no_data_reason=str(reason or "").strip() or "query returned no data",
            is_error=False,
            error=None,
        ).model_copy(
            update={
                "structuredContent": {
                    "resources": [],
                    "noDataReason": str(reason or "").strip() or "query returned no data",
                    "scope": scope_payload,
                    "retriable": False,
                    "retry_same_query_allowed": False,
                    "suggested_reroute": reroute_text,
                }
            }
        )

    def _is_no_data_exception(exc: Exception | str) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        markers = (
            "no sector index found",
            "no sector daily data",
            "no data",
            "not found",
            "未找到",
            "无数据",
            "暂无数据",
            "no adapter supports get_sector_valuation_metrics",
            "no adapter supports get_sector_money_flow_history",
        )
        return any(marker in text for marker in markers)

    def _candidate_names(rows: Any) -> list[str]:
        if not isinstance(rows, list):
            return []
        names: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                name = str(
                    row.get("canonical_name")
                    or row.get("name")
                    or row.get("sector_name")
                    or ""
                ).strip()
            else:
                name = str(row or "").strip()
            if name:
                names.append(name)
        dedup: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name not in seen:
                dedup.append(name)
                seen.add(name)
        return dedup

    async def _pre_resolve_sector(query_text: str, intent: str) -> Dict[str, Any] | None:
        resolver = getattr(money_flow_use_cases, "resolve_sector", None)
        if not callable(resolver) or not inspect.iscoroutinefunction(resolver):
            return None
        try:
            result = await resolver(query_text=query_text, intent=intent)
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    @mcp.tool(tags={"money-flow"})
    async def get_money_flow(
        symbol: str, days: int = 20, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取个股资金流向数据

        分析主力资金和散户资金的流入流出情况，帮助判断资金动向。

        Args:
            symbol: 股票代码. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA
            days: 获取最近 N 天数据 (默认 20 天)
            ctx: FastMCP Context for logging

        Returns:
            ArtifactEnvelope containing money flow data
        """
        if ctx:
            await ctx.info(
                f"🔧 获取资金流向: {symbol}", extra={"symbol": symbol, "days": days}
            )

        try:
            logger.info("MCP tool called: get_money_flow", symbol=symbol, days=days)
            result = await money_flow_use_cases.get_money_flow(symbol, days)
            result["variant"] = "money_flow"
            ts_code = result.get("ts_code") or await _resolve_ts_code(symbol)

            # 构建摘要信息给 LLM（精简版）
            summary = result.get("summary", {})
            total_main = summary.get("total_main_net", 0)
            total_retail = summary.get("total_retail_net", 0)
            total_net = total_main + total_retail
            trend = summary.get("trend", "未知")
            amount_unit = (
                result.get("amount_unit")
                or summary.get("amount_unit")
                or "unknown"
            )

            summary_text = (
                f"{symbol}主力资金流（{days}日）:\n"
                f"- 大/超大单净流入(主力口径): {_fmt_amount(total_main, amount_unit)}\n"
                f"- 整体净流{'入' if total_net >= 0 else '出'}: {_fmt_amount(total_net, amount_unit)}\n"
                f"- 金额单位口径: {_unit_label(amount_unit)}\n"
                f"- 趋势: {trend}"
            )

            if ctx:
                await ctx.info(
                    f"✅ 资金流向获取完成: {symbol}",
                    extra={"total_main_net": total_main, "trend": trend},
                )

            # 对齐竞品格式: content = {ts_code, records:[{trade_date, main_net_inflow}]}
            records = []
            if isinstance(result.get("records"), list):
                records = result.get("records", [])
            else:
                data = result.get("data", {})
                dates = data.get("dates", [])
                main = data.get("main_net_inflow", [])
                if dates and main and len(dates) == len(main):
                    for d, v in zip(dates, main):
                        trade_date = d.replace("-", "") if isinstance(d, str) else d
                        records.append({"trade_date": trade_date, "main_net_inflow": v})

            content = {
                "ts_code": ts_code,
                "records": records,
                "amount_unit": amount_unit,
            }

            # 包装为 ArtifactEnvelope (竞品格式)
            artifact = create_artifact_envelope(
                variant="money_flow",
                name=f"Money Flow: {ts_code}",
                content=content,
                description=(
                    f"Main Force (Institutional) Capital Flow: {ts_code} (Last {days} days). "
                    "Visualizes daily net inflow/outflow trends of Large & Extra-Large orders. "
                    f"Overall Trend: {trend}."
                ),
                metadata={
                    "variant": "money_flow",
                    "ts_code": ts_code,
                    "days": days,
                    "amount_unit": amount_unit,
                },
                visible_to_llm=False,
                display_in_report=True,
            )
            if not records:
                no_data_reason = (
                    str(summary.get("trend") or "").strip()
                    or "money flow records are empty"
                )
                no_data_summary = (
                    f"{symbol} 资金流向暂无有效记录: {no_data_reason}。"
                    "建议缩短时间窗或改用行业/新闻工具补证。"
                )
                return _build_no_data_response(
                    summary=no_data_summary,
                    artifact=artifact,
                    reason=no_data_reason,
                    scope={
                        "tool": "get_money_flow",
                        "symbol": symbol,
                        "ts_code": ts_code,
                        "days": days,
                        "source": str(result.get("source") or ""),
                    },
                    suggested_reroute=(
                        "Try shorter days or reroute to get_sector_money_flow_history/"
                        "search_sentiment_news for evidence."
                    ),
                )

            return create_artifact_response(summary=summary_text, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="money_flow", name=f"{symbol} 资金流向"
            )
        except Exception as e:
            logger.error(f"Get money flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 获取资金流向失败: {symbol}", extra={"error": str(e)}
                )
            if not _is_no_data_exception(e):
                return create_mcp_error_result(str(e))
            ts_code = to_ts_code(symbol)
            empty = {"ts_code": ts_code, "records": []}
            artifact = create_artifact_envelope(
                variant="money_flow",
                name=f"Money Flow: {ts_code}",
                content=empty,
                description="No money flow data",
                metadata={"variant": "money_flow", "ts_code": ts_code, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return _build_no_data_response(
                summary="No money flow data",
                artifact=artifact,
                reason="tool exception produced empty money flow artifact",
                scope={
                    "tool": "get_money_flow",
                    "symbol": symbol,
                    "ts_code": ts_code,
                    "days": days,
                },
                suggested_reroute=(
                    "Retry with a different date window or reroute to sector/news evidence."
                ),
            )

    @mcp.tool(tags={"money-flow"})
    async def get_north_bound_flow(
        days: int = 30, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取北向资金(沪深港通)流向数据

        追踪外资通过沪股通、深股通流入A股市场的资金情况。
        北向资金被视为"聪明钱"，其流向对市场有重要参考价值。

        Args:
            days: 获取最近 N 天数据 (默认 30 天)
            ctx: FastMCP Context for logging

        Returns:
            ArtifactEnvelope containing north bound flow data
        """
        if ctx:
            await ctx.info(f"🔧 获取北向资金流向", extra={"days": days})

        try:
            logger.info("MCP tool called: get_north_bound_flow", days=days)
            result = await money_flow_use_cases.get_north_bound_flow(days)
            result["variant"] = "north_bound_flow"

            # 构建摘要
            # moneyflow_hsgt 返回单位为万元，转换为亿元展示
            summary = result.get("summary", {})
            total_net = summary.get("total_net", 0)
            total_net_yi = (
                float(total_net) / 10000 if isinstance(total_net, (int, float)) else None
            )
            series = (result.get("data") or {}).get("total") or []
            dates = (result.get("data") or {}).get("dates") or []
            latest_total = float(series[-1]) if series else None
            prev_total = float(series[-2]) if len(series) >= 2 else None
            latest_date = str(dates[-1]) if dates else "N/A"

            latest_total_yi = latest_total / 10000 if latest_total is not None else None
            day_delta_text = (
                f"(较前一日{(latest_total - prev_total) / 10000:+.2f}亿)"
                if latest_total is not None and prev_total is not None
                else ""
            )
            if total_net_yi is None:
                flow_signal = "未知"
            elif total_net_yi >= 0:
                flow_signal = "外资阶段性净流入"
            else:
                flow_signal = "外资阶段性净流出"

            summary_text = (
                f"北向资金({days}日): 最新{latest_date}单日净流入"
                f"{f'{latest_total_yi:+.2f}亿' if latest_total_yi is not None else 'N/A'}"
                f"{day_delta_text}, 累计净流入"
                f"{f'{total_net_yi:+.2f}亿' if total_net_yi is not None else 'N/A'}; "
                f"资金信号={flow_signal}"
            )
            total_net_display = (
                f"{total_net_yi:+.2f}亿" if total_net_yi is not None else "N/A"
            )

            if ctx:
                await ctx.info(
                    f"✅ 北向资金流向获取完成", extra={"total_net": total_net_display}
                )

            artifact = create_artifact_envelope(
                variant="north_bound_flow",
                name="北向资金流向",
                content=result,
                description=summary_text,
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)

        except Exception as e:
            logger.error(f"Get north bound flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(f"❌ 获取北向资金流向失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_chip_distribution(
        symbol: str | None = None,
        ts_code: str | None = None,
        period_days: int = 120,
        price_bins: int = 50,
        ctx: Context = None,
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
            ctx: FastMCP Context for logging

        Returns:
            {
                "symbol": "600519.SH",
                "variant": "chip_distribution",
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
        if not symbol and not ts_code:
            return create_mcp_error_result("symbol or ts_code is required")
        raw_symbol = ts_code or symbol
        if ctx:
            await ctx.info(
                f"🔧 获取筹码分布: {raw_symbol}",
                extra={"symbol": raw_symbol, "period_days": period_days},
            )

        try:
            logger.info(
                "MCP tool called: get_chip_distribution",
                symbol=raw_symbol,
                period_days=period_days,
                price_bins=price_bins,
            )
            result = await money_flow_use_cases.get_chip_distribution_detail(
                raw_symbol, period_days, price_bins
            )
            result["variant"] = "chip_distribution"

            # Normalize to competitor format
            ts_code = result.get("ts_code") or result.get("symbol") or raw_symbol
            fallback_exchange = (
                raw_symbol.split(":", 1)[0] if ":" in raw_symbol else None
            )
            ts_code = to_ts_code(ts_code, fallback_exchange=fallback_exchange)

            # 构建摘要
            summary = result.get("summary", {})
            profit_ratio = summary.get("profit_ratio", 0)
            avg_cost = summary.get("avg_cost", 0)
            concentration = summary.get("concentration_90", 0)
            main_peak = summary.get("main_peak_price", 0)

            summary_text = (
                f"{ts_code}筹码分布/成本结构快照:\n"
                f"- 获利比例: {profit_ratio*100:.1f}%\n"
                f"- 平均成本: {avg_cost:.2f}元\n"
                f"- 主峰价格/成本区间: {main_peak:.2f} / {concentration:.2f}"
            )

            if ctx:
                await ctx.info(
                    f"✅ 筹码分布获取完成: {ts_code}",
                    extra={"profit_ratio": profit_ratio, "avg_cost": avg_cost},
                )

            artifact = create_artifact_envelope(
                variant="chip_distribution",
                name=f"Chip Distribution: {ts_code}",
                content={
                    "ts_code": ts_code,
                    "trade_date": result.get("trade_date"),
                    "chip_trade_date": result.get("chip_trade_date"),
                    "price_trade_date": result.get("price_trade_date"),
                    "current_price": result.get("current_price"),
                    "avg_cost": result.get("avg_cost") or summary.get("avg_cost"),
                    "support_price": result.get("support_price"),
                    "resistance_price": result.get("resistance_price"),
                    "profit_ratio": result.get("profit_ratio")
                    or summary.get("profit_ratio"),
                    "loss_ratio": result.get("loss_ratio"),
                    "flat_ratio": result.get("flat_ratio"),
                    "distribution": result.get("distribution") or [],
                    "data": result.get("data"),
                },
                description=(
                    f"Chip Distribution (Cost Structure) Snapshot for {ts_code}. "
                    f"Chip Date: {result.get('chip_trade_date')}, "
                    f"Price Reference Date: {result.get('price_trade_date')}. "
                    f"Avg Cost: {avg_cost:.2f}. "
                    f"Key Levels: Support at {result.get('support_price')}, "
                    f"Resistance at {result.get('resistance_price')}. "
                    f"Profit Ratio: {profit_ratio*100:.1f}%."
                ),
                metadata={
                    "variant": "chip_distribution",
                    "ts_code": ts_code,
                    "trade_date": result.get("trade_date"),
                    "chip_trade_date": result.get("chip_trade_date"),
                    "price_trade_date": result.get("price_trade_date"),
                },
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="chip_distribution", name=f"{symbol} 筹码分布"
            )
        except Exception as e:
            logger.error(f"Get chip distribution failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 获取筹码分布失败: {symbol}", extra={"error": str(e)}
                )
            return {
                "error": str(e),
                "symbol": symbol,
                "variant": "chip_distribution",
            }

    @mcp.tool(tags={"money-flow"})
    async def get_money_supply(months: int = 60, ctx: Context = None) -> Dict[str, Any]:
        """获取中国货币流动性 (M1/M2)."""
        if ctx:
            await ctx.info("🔧 获取货币供应量", extra={"months": months})

        try:
            logger.info("MCP tool called: get_money_supply", months=months)
            result = await money_flow_use_cases.get_money_supply(months)
            result["variant"] = "money_supply"

            content = result.get("data", []) or []
            content = sorted(content, key=lambda x: str(x.get("month", "")))
            if months > 0:
                content = content[-months:]

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            def _fmt_pct(v: float | None) -> str:
                return f"{v:.1f}%" if v is not None else "N/A"

            latest = content[-1] if content else {}
            prev = content[-2] if len(content) >= 2 else {}
            latest_month = str(latest.get("month") or "N/A")

            m1_yoy = _to_float(latest.get("m1_yoy"))
            m2_yoy = _to_float(latest.get("m2_yoy"))
            prev_m1_yoy = _to_float(prev.get("m1_yoy"))
            prev_m2_yoy = _to_float(prev.get("m2_yoy"))

            spread = _to_float(latest.get("m1_m2_spread"))
            if spread is None and m1_yoy is not None and m2_yoy is not None:
                spread = m1_yoy - m2_yoy
            prev_spread = _to_float(prev.get("m1_m2_spread"))
            if (
                prev_spread is None
                and prev_m1_yoy is not None
                and prev_m2_yoy is not None
            ):
                prev_spread = prev_m1_yoy - prev_m2_yoy

            spread_delta_text = (
                f"(较上月{(spread - prev_spread):+.1f}pct)"
                if spread is not None and prev_spread is not None
                else ""
            )
            if spread is None:
                liquidity_signal = "未知"
            elif spread >= 0:
                liquidity_signal = "企业活期改善"
            else:
                liquidity_signal = "资金偏沉淀"

            summary_text = (
                f"中国货币供应(近{months}月): 最新{latest_month}M1同比{_fmt_pct(m1_yoy)}, "
                f"M2同比{_fmt_pct(m2_yoy)}, M1-M2剪刀差{_fmt_pct(spread)}{spread_delta_text}; "
                f"流动性信号={liquidity_signal}"
            )
            artifact = create_artifact_envelope(
                variant="money_supply",
                name="Money Supply Data",
                content=content,
                description=summary_text,
                metadata={"variant": "money_supply"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get money supply failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取货币供应量失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_inflation_data(
        months: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取中国月度通胀指标 (CPI/PPI)."""
        if ctx:
            await ctx.info("🔧 获取通胀指标", extra={"months": months})

        try:
            logger.info("MCP tool called: get_inflation_data", months=months)
            result = await money_flow_use_cases.get_inflation_data(months)
            result["variant"] = "inflation_data"

            data = result.get("data", {})
            cpi = data.get("CPI", []) if isinstance(data, dict) else []
            ppi = data.get("PPI", []) if isinstance(data, dict) else []
            cpi = sorted(cpi, key=lambda x: str(x.get("month", "")))
            ppi = sorted(ppi, key=lambda x: str(x.get("month", "")))
            if months > 0:
                cpi = cpi[-months:]
                ppi = ppi[-months:]

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            def _fmt_pct(v: float | None) -> str:
                return f"{v:.1f}%" if v is not None else "N/A"

            cpi_latest = cpi[-1] if cpi else {}
            cpi_prev = cpi[-2] if len(cpi) >= 2 else {}
            ppi_latest = ppi[-1] if ppi else {}
            ppi_prev = ppi[-2] if len(ppi) >= 2 else {}
            latest_month = str(cpi_latest.get("month") or ppi_latest.get("month") or "N/A")

            cpi_yoy = _to_float(cpi_latest.get("nt_yoy"))
            cpi_yoy_prev = _to_float(cpi_prev.get("nt_yoy"))
            cpi_mom = _to_float(cpi_latest.get("nt_mom"))
            ppi_yoy = _to_float(ppi_latest.get("ppi_yoy"))
            ppi_yoy_prev = _to_float(ppi_prev.get("ppi_yoy"))
            spread = (
                cpi_yoy - ppi_yoy
                if cpi_yoy is not None and ppi_yoy is not None
                else None
            )

            cpi_delta_text = (
                f"(较上月{(cpi_yoy - cpi_yoy_prev):+.1f}pct)"
                if cpi_yoy is not None and cpi_yoy_prev is not None
                else ""
            )
            ppi_delta_text = (
                f"(较上月{(ppi_yoy - ppi_yoy_prev):+.1f}pct)"
                if ppi_yoy is not None and ppi_yoy_prev is not None
                else ""
            )

            if cpi_yoy is None and ppi_yoy is None:
                inflation_signal = "未知"
            elif (
                cpi_yoy is not None
                and ppi_yoy is not None
                and cpi_yoy > 0
                and ppi_yoy < 0
            ):
                inflation_signal = "消费偏温和、上游偏弱"
            elif cpi_yoy is not None and cpi_yoy >= 2:
                inflation_signal = "通胀压力抬升"
            elif ppi_yoy is not None and ppi_yoy <= -2:
                inflation_signal = "工业品价格偏弱"
            else:
                inflation_signal = "价格总体平稳"

            summary_text = (
                f"中国通胀(近{months}月): 最新{latest_month}CPI同比{_fmt_pct(cpi_yoy)}"
                f"{cpi_delta_text}, 环比{_fmt_pct(cpi_mom)}; PPI同比{_fmt_pct(ppi_yoy)}"
                f"{ppi_delta_text}, CPI-PPI剪刀差{_fmt_pct(spread)}; "
                f"价格信号={inflation_signal}"
            )
            artifact = create_artifact_envelope(
                variant="inflation_data",
                name="Inflation Data",
                content={"cpi": cpi, "ppi": ppi},
                description=summary_text,
                metadata={"variant": "inflation_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get inflation data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取通胀指标失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_pmi_data(months: int = 60, ctx: Context = None) -> Dict[str, Any]:
        """获取中国官方 PMI 数据."""
        if ctx:
            await ctx.info("🔧 获取 PMI", extra={"months": months})

        try:
            logger.info("MCP tool called: get_pmi_data", months=months)
            result = await money_flow_use_cases.get_pmi_data(months)
            result["variant"] = "pmi_data"

            content = result.get("data", []) or []

            def _month_of(row: Dict[str, Any]) -> str:
                # Tushare cn_pmi often uses uppercase MONTH.
                m = row.get("month")
                if m is None:
                    m = row.get("MONTH")
                return str(m or "")

            content = sorted(content, key=_month_of)
            if months > 0:
                content = content[-months:]

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            latest = content[-1] if content else {}
            prev = content[-2] if len(content) >= 2 else {}
            latest_month = _month_of(latest) or "N/A"

            def _pick(row: Dict[str, Any], *keys: str) -> Any:
                for k in keys:
                    if k in row and row.get(k) is not None:
                        return row.get(k)
                return None

            # Field compatibility:
            # - Generic schema: pmi / pmi_non_mfg / pmi_composite
            # - Tushare cn_pmi schema: PMI010000 / PMI020200 / PMI030000
            mfg = _to_float(_pick(latest, "pmi", "PMI010000"))
            mfg_prev = _to_float(_pick(prev, "pmi", "PMI010000"))
            non_mfg = _to_float(
                _pick(
                    latest,
                    "pmi_non_mfg",
                    "pmi_nonmanufacturing",
                    "PMI020200",
                    "PMI020000",
                )
            )
            non_mfg_prev = _to_float(
                _pick(
                    prev,
                    "pmi_non_mfg",
                    "pmi_nonmanufacturing",
                    "PMI020200",
                    "PMI020000",
                )
            )
            composite = _to_float(_pick(latest, "pmi_composite", "PMI030000"))
            composite_prev = _to_float(_pick(prev, "pmi_composite", "PMI030000"))

            def _fmt_val(v: float | None) -> str:
                return f"{v:.1f}" if v is not None else "N/A"

            def _fmt_delta(curr: float | None, base: float | None) -> str:
                if curr is None or base is None:
                    return ""
                return f",环比{(curr - base):+.1f}"

            mfg_state = (
                "扩张"
                if isinstance(mfg, (int, float)) and mfg >= 50
                else ("收缩" if isinstance(mfg, (int, float)) else "未知")
            )
            non_mfg_state = (
                "扩张"
                if isinstance(non_mfg, (int, float)) and non_mfg >= 50
                else ("收缩" if isinstance(non_mfg, (int, float)) else "未知")
            )
            if isinstance(mfg, (int, float)) and isinstance(non_mfg, (int, float)):
                macro_state = (
                    "偏强"
                    if mfg >= 50 and non_mfg >= 50
                    else ("偏弱" if mfg < 50 and non_mfg < 50 else "分化")
                )
            elif isinstance(composite, (int, float)):
                macro_state = "偏强" if composite >= 50 else "偏弱"
            else:
                macro_state = "未知"
            summary_text = (
                f"中国PMI(近{months}月): 最新{latest_month}制造业{_fmt_val(mfg)}"
                f"({mfg_state}{_fmt_delta(mfg, mfg_prev)}), 非制造业{_fmt_val(non_mfg)}"
                f"({non_mfg_state}{_fmt_delta(non_mfg, non_mfg_prev)}), 综合{_fmt_val(composite)}"
                f"{_fmt_delta(composite, composite_prev)}; 景气判断={macro_state}"
            )
            artifact = create_artifact_envelope(
                variant="pmi_data",
                name="PMI Data",
                content=content,
                description=summary_text,
                metadata={"variant": "pmi_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get PMI data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取 PMI 数据失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_gdp_data(quarters: int = 20, ctx: Context = None) -> Dict[str, Any]:
        """获取中国季度 GDP 增长数据."""
        if ctx:
            await ctx.info("🔧 获取 GDP", extra={"quarters": quarters})

        try:
            logger.info("MCP tool called: get_gdp_data", quarters=quarters)
            result = await money_flow_use_cases.get_gdp_data(quarters)
            result["variant"] = "gdp_data"

            content = result.get("data", []) or []
            content = sorted(content, key=lambda x: str(x.get("quarter", "")))
            if quarters > 0:
                content = content[-quarters:]

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            latest = content[-1] if content else {}
            prev = content[-2] if len(content) >= 2 else {}
            latest_quarter = str(latest.get("quarter") or "N/A")

            gdp_yoy = _to_float(latest.get("gdp_yoy"))
            gdp_yoy_prev = _to_float(prev.get("gdp_yoy"))
            pi_yoy = _to_float(latest.get("pi_yoy"))
            si_yoy = _to_float(latest.get("si_yoy"))
            ti_yoy = _to_float(latest.get("ti_yoy"))

            def _fmt_pct(v: float | None) -> str:
                return f"{v:.1f}%" if v is not None else "N/A"

            gdp_delta_text = (
                f"(较上季{(gdp_yoy - gdp_yoy_prev):+.1f}pct)"
                if gdp_yoy is not None and gdp_yoy_prev is not None
                else ""
            )

            sector_candidates = []
            if pi_yoy is not None:
                sector_candidates.append(("一产", pi_yoy))
            if si_yoy is not None:
                sector_candidates.append(("二产", si_yoy))
            if ti_yoy is not None:
                sector_candidates.append(("三产", ti_yoy))
            lead_sector = (
                f"{max(sector_candidates, key=lambda x: x[1])[0]}领先"
                if sector_candidates
                else "结构未知"
            )

            summary_text = (
                f"中国GDP(近{quarters}季): 最新{latest_quarter}同比{_fmt_pct(gdp_yoy)}"
                f"{gdp_delta_text}; 三产同比一产{_fmt_pct(pi_yoy)}/二产{_fmt_pct(si_yoy)}"
                f"/三产{_fmt_pct(ti_yoy)}({lead_sector})"
            )
            artifact = create_artifact_envelope(
                variant="gdp_data",
                name="GDP Data",
                content=content,
                description=summary_text,
                metadata={"variant": "gdp_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get GDP data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取 GDP 数据失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_social_financing(
        months: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取中国社会融资总量."""
        if ctx:
            await ctx.info("🔧 获取社会融资总量", extra={"months": months})

        try:
            logger.info("MCP tool called: get_social_financing", months=months)
            # Keep enough history to derive stock-yoy from stk_endval when upstream
            # does not provide explicit stk_yoy.
            fetch_months = max(months, 24)
            result = await money_flow_use_cases.get_social_financing(fetch_months)
            result["variant"] = "social_financing"

            raw_data = result.get("data", []) or []

            def _month_of(row: Dict[str, Any]) -> str:
                m = row.get("month")
                if m is None:
                    m = row.get("MONTH")
                return str(m or "")

            # Normalize rows first so downstream logic has stable keys.
            normalized = [
                {
                    "month": _month_of(r),
                    "inc_month": r.get("inc_month"),
                    "inc_cumval": r.get("inc_cumval"),
                    "stk_endval": r.get("stk_endval"),
                    "stk_yoy": r.get("stk_yoy"),
                }
                for r in raw_data
            ]
            normalized = sorted(normalized, key=lambda x: str(x.get("month", "")), reverse=True)
            content = normalized[:months] if months and months > 0 else normalized

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            def _derive_stk_yoy(row: Dict[str, Any]) -> float | None:
                direct = _to_float(row.get("stk_yoy"))
                if direct is not None:
                    return direct

                month = str(row.get("month") or "")
                if len(month) != 6 or not month.isdigit():
                    return None

                prev_year_month = f"{int(month[:4]) - 1:04d}{month[4:]}"
                base_row = next(
                    (r for r in normalized if str(r.get("month") or "") == prev_year_month),
                    None,
                )
                if not base_row:
                    return None

                curr = _to_float(row.get("stk_endval"))
                base = _to_float(base_row.get("stk_endval"))
                if curr is None or base is None or base == 0:
                    return None
                return (curr / base - 1.0) * 100.0

            latest = content[0] if content else {}
            prev = content[1] if len(content) >= 2 else {}
            latest_month = str(latest.get("month") or "N/A")

            inc_month = _to_float(latest.get("inc_month"))
            prev_inc_month = _to_float(prev.get("inc_month"))
            stk_yoy = _derive_stk_yoy(latest)
            prev_stk_yoy = _derive_stk_yoy(prev)

            def _fmt_num(v: float | None) -> str:
                if v is None:
                    return "N/A"
                return f"{v:,.1f}"

            inc_delta_text = (
                f"(较上月{(inc_month - prev_inc_month):+,.1f})"
                if inc_month is not None and prev_inc_month is not None
                else ""
            )
            stk_yoy_delta_text = (
                f"(较上月{(stk_yoy - prev_stk_yoy):+.1f}pct)"
                if stk_yoy is not None and prev_stk_yoy is not None
                else ""
            )

            if stk_yoy is None:
                credit_signal = "未知"
            elif stk_yoy >= 9:
                credit_signal = "信用扩张偏强"
            elif stk_yoy <= 8:
                credit_signal = "信用扩张偏弱"
            else:
                credit_signal = "信用扩张中性"

            summary_text = (
                f"中国社融(近{months}月): 最新{latest_month}新增社融{_fmt_num(inc_month)}"
                f"{inc_delta_text}, 存量同比{f'{stk_yoy:.1f}%' if stk_yoy is not None else 'N/A'}"
                f"{stk_yoy_delta_text}; 信用信号={credit_signal}"
            )
            artifact = create_artifact_envelope(
                variant="social_financing",
                name="Social Financing Data",
                content=content,
                description=summary_text,
                metadata={"variant": "social_financing"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get social financing failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取社会融资失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_interest_rates(
        shibor_days: int = 252, lpr_months: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取中国利率数据 (SHIBOR + LPR)."""
        if ctx:
            await ctx.info(
                "🔧 获取利率数据",
                extra={"shibor_days": shibor_days, "lpr_months": lpr_months},
            )

        try:
            logger.info(
                "MCP tool called: get_interest_rates",
                shibor_days=shibor_days,
                lpr_months=lpr_months,
            )
            result = await money_flow_use_cases.get_interest_rates(
                shibor_days, lpr_months
            )
            result["variant"] = "interest_rates"

            shibor = result.get("data", {}).get("shibor", []) or []
            lpr = result.get("data", {}).get("lpr", []) or []

            shibor = sorted(shibor, key=lambda x: str(x.get("date", "")), reverse=True)
            lpr = sorted(
                lpr,
                key=lambda x: str(x.get("date") or x.get("month", "")),
                reverse=True,
            )

            if shibor_days and shibor_days > 0:
                shibor = shibor[:shibor_days]
            if lpr_months and lpr_months > 0:
                lpr = lpr[:lpr_months]

            shibor_content = [
                {
                    "date": r.get("date"),
                    "on": r.get("on"),
                    "1w": r.get("1w"),
                    "2w": r.get("2w"),
                    "1m": r.get("1m"),
                    "3m": r.get("3m"),
                    "6m": r.get("6m"),
                    "9m": r.get("9m"),
                    "1y": r.get("1y"),
                }
                for r in shibor
            ]
            lpr_content = [
                {
                    "date": r.get("date") or r.get("month"),
                    "lpr_1y": r.get("1y"),
                    "lpr_5y": r.get("5y"),
                }
                for r in lpr
            ]

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            shibor_latest = shibor_content[0] if shibor_content else {}
            shibor_prev = shibor_content[1] if len(shibor_content) >= 2 else {}
            lpr_latest = lpr_content[0] if lpr_content else {}
            lpr_prev = lpr_content[1] if len(lpr_content) >= 2 else {}

            latest_date = str(
                shibor_latest.get("date") or lpr_latest.get("date") or "N/A"
            )
            shibor_1w = _to_float(shibor_latest.get("1w"))
            shibor_1y = _to_float(shibor_latest.get("1y"))
            prev_shibor_1w = _to_float(shibor_prev.get("1w"))

            lpr_1y = _to_float(lpr_latest.get("lpr_1y"))
            lpr_5y = _to_float(lpr_latest.get("lpr_5y"))
            prev_lpr_1y = _to_float(lpr_prev.get("lpr_1y"))
            prev_lpr_5y = _to_float(lpr_prev.get("lpr_5y"))

            curve_spread = (
                shibor_1y - shibor_1w
                if shibor_1y is not None and shibor_1w is not None
                else None
            )
            lpr_spread = (
                lpr_5y - lpr_1y if lpr_1y is not None and lpr_5y is not None else None
            )
            lpr_1y_delta = (
                lpr_1y - prev_lpr_1y
                if lpr_1y is not None and prev_lpr_1y is not None
                else None
            )
            lpr_5y_delta = (
                lpr_5y - prev_lpr_5y
                if lpr_5y is not None and prev_lpr_5y is not None
                else None
            )

            if lpr_1y_delta is None and lpr_5y_delta is None:
                policy_signal = "未知"
            elif (
                (lpr_1y_delta is None or lpr_1y_delta == 0)
                and (lpr_5y_delta is None or lpr_5y_delta == 0)
            ):
                policy_signal = "政策利率按兵不动"
            elif (
                (lpr_1y_delta is not None and lpr_1y_delta < 0)
                or (lpr_5y_delta is not None and lpr_5y_delta < 0)
            ):
                policy_signal = "政策利率偏宽松"
            else:
                policy_signal = "政策利率偏收紧"

            def _fmt_pct(v: float | None) -> str:
                return f"{v:.2f}%" if v is not None else "N/A"

            summary_text = (
                f"中国利率(Shibor+LPR): 最新{latest_date}Shibor1W/1Y="
                f"{_fmt_pct(shibor_1w)}/{_fmt_pct(shibor_1y)}, 期限利差{_fmt_pct(curve_spread)}"
                f"{f'(1W较前值{(shibor_1w - prev_shibor_1w):+.2f}pct)' if shibor_1w is not None and prev_shibor_1w is not None else ''}; "
                f"LPR1Y/5Y={_fmt_pct(lpr_1y)}/{_fmt_pct(lpr_5y)}, 挂钩利差{_fmt_pct(lpr_spread)}; "
                f"利率信号={policy_signal}"
            )

            artifacts = [
                create_artifact_envelope(
                    variant="interest_rates_shibor",
                    name="SHIBOR Interest Rates",
                    content=shibor_content,
                    description="China Interbank Offered Rate (SHIBOR): Term Structure (Overnight to 1 Year) - Last 1 Year",
                    metadata={"variant": "interest_rates_shibor"},
                ),
                create_artifact_envelope(
                    variant="interest_rates_lpr",
                    name="LPR Interest Rates",
                    content=lpr_content,
                    description="China Loan Prime Rate (LPR, shibor_lpr): 1-Year and 5-Year Benchmark Lending Rates - Last 5 Years",
                    metadata={"variant": "interest_rates_lpr"},
                ),
            ]

            return create_artifact_list_response(
                summary=summary_text, artifacts=artifacts
            )
        except Exception as e:
            logger.error(f"Get interest rates failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取利率失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_market_liquidity(
        days: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取 A 股市场流动性指标."""
        if ctx:
            await ctx.info("🔧 获取市场流动性", extra={"days": days})

        try:
            logger.info("MCP tool called: get_market_liquidity", days=days)
            result = await money_flow_use_cases.get_market_liquidity(days)
            result["variant"] = "market_liquidity"

            data = result.get("data", {})
            north_flow = data.get("north_flow", [])
            margin = data.get("margin", [])
            latest_north = north_flow[-1] if north_flow else {}
            latest_margin = margin[-1] if margin else {}
            north_value = latest_north.get("north_money")
            margin_value = latest_margin.get("rzrqye")
            latest_date = (
                latest_north.get("trade_date")
                or latest_margin.get("trade_date")
                or "N/A"
            )
            summary_text = (
                f"A股流动性({days}日): 最新{latest_date}北向净流入="
                f"{north_value if north_value is not None else 'N/A'}, "
                f"融资融券余额={margin_value if margin_value is not None else 'N/A'}, "
                f"样本={len(north_flow)}/{len(margin)}日"
            )

            north_series = []
            if north_flow:
                north_series = [
                    {
                        "name": "北向净流入",
                        "type": "line",
                        "data": [
                            {"x": r.get("trade_date"), "y": r.get("north_money")}
                            for r in north_flow
                        ],
                    }
                ]

            margin_series = []
            if margin:
                margin_series = [
                    {
                        "name": "融资融券余额",
                        "type": "line",
                        "data": [
                            {"x": r.get("trade_date"), "y": r.get("rzrqye")}
                            for r in margin
                        ],
                    }
                ]

            artifacts = [
                create_chart_artifact(
                    title="北向资金趋势",
                    chart_type="line",
                    unit="",
                    x_label="日期",
                    y_label="净流入",
                    series=north_series,
                    description="北向资金趋势",
                    metadata={"variant": "chart", "dataset": "north_flow"},
                    name="Northbound Flow",
                ),
                create_chart_artifact(
                    title="融资融券余额",
                    chart_type="line",
                    unit="",
                    x_label="日期",
                    y_label="余额",
                    series=margin_series,
                    description="融资融券余额趋势",
                    metadata={"variant": "chart", "dataset": "margin"},
                    name="Margin Balance",
                ),
            ]

            return create_artifact_list_response(
                summary=summary_text, artifacts=artifacts
            )
        except Exception as e:
            logger.error(f"Get market liquidity failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取市场流动性失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_market_money_flow(
        trade_date: str | None = None,
        top_n: int = 20,
        include_outflow: bool = True,
        days: int | None = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """获取板块资金流向统计（含净流入TopN与数据新鲜度门禁）.

        Args:
            trade_date: 交易日，格式 `YYYY-MM-DD`（可选）
            top_n: Top N 板块数量
            include_outflow: 是否返回净流出榜单
            days: 可选回溯别名参数；当 trade_date 为空时，按当前时间回溯 N 天生成 trade_date
        """
        if ctx:
            await ctx.info(
                "🔧 获取板块资金流向",
                extra={
                    "trade_date": trade_date,
                    "top_n": top_n,
                    "include_outflow": include_outflow,
                    "days": days,
                },
            )

        try:
            effective_trade_date = trade_date
            if effective_trade_date is None and isinstance(days, int) and days > 0:
                lookback_days = max(1, min(int(days), 3650))
                effective_trade_date = (
                    datetime.now() - timedelta(days=lookback_days)
                ).strftime("%Y-%m-%d")
                if ctx:
                    await ctx.warning(
                        "⚠️ 参数兼容: 未提供 trade_date，已按 days 回溯生成交易日",
                        extra={"days": lookback_days, "trade_date": effective_trade_date},
                    )

            logger.info(
                "MCP tool called: get_market_money_flow",
                trade_date=effective_trade_date,
                days=days,
            )
            result = await money_flow_use_cases.get_market_money_flow(
                trade_date=effective_trade_date,
                top_n=top_n,
                include_outflow=include_outflow,
            )
            result["variant"] = "market_money_flow"

            requested_trade_date = result.get("requested_trade_date") or effective_trade_date
            as_of_trade_date = result.get("as_of_trade_date")
            data_freshness = result.get("data_freshness", "unknown")
            top_n_effective = int(result.get("top_n") or top_n or 20)
            overview = result.get("market_overview", {})
            total_net = overview.get("total_net_amount", 0.0) or 0.0
            inflow = overview.get("inflow_count", 0) or 0
            outflow = overview.get("outflow_count", 0) or 0
            top_inflow = result.get("top_inflow", []) or []
            top_outflow = result.get("top_outflow", []) or []
            gate_allowed = bool(result.get("trend_conclusion_allowed", False))
            blocked_reason = result.get("blocked_reason")

            freshness_suffix = ""
            if requested_trade_date and as_of_trade_date and requested_trade_date != as_of_trade_date:
                freshness_suffix = (
                    f"（请求{requested_trade_date}，实际返回{as_of_trade_date}）"
                )
            elif as_of_trade_date:
                freshness_suffix = f"（数据日期{as_of_trade_date}）"

            gate_text = (
                "趋势结论门禁=PASS"
                if gate_allowed
                else f"趋势结论门禁=BLOCKED({blocked_reason or 'insufficient_data'})"
            )
            summary_text = (
                f"板块资金流向Top{top_n_effective}（{requested_trade_date or '最新'}）{freshness_suffix}: "
                f"净流入{inflow}个、净流出{outflow}个，全市场净流入 {total_net:.2f}; "
                f"data_freshness={data_freshness}; {gate_text}"
            )

            inflow_rows = [
                {
                    "rank": r.get("rank"),
                    "sector_name": r.get("sector_name"),
                    "net_amount": r.get("net_amount"),
                    "pct_chg": r.get("pct_chg"),
                }
                for r in top_inflow
            ]
            outflow_rows = [
                {
                    "rank": r.get("rank"),
                    "sector_name": r.get("sector_name"),
                    "net_amount": r.get("net_amount"),
                    "pct_chg": r.get("pct_chg"),
                }
                for r in top_outflow
            ]

            artifacts = [
                create_chart_artifact(
                    title="板块资金流向",
                    chart_type="bar",
                    unit="",
                    x_label="板块",
                    y_label="净流入",
                    series=[
                        {
                            "name": "净流入",
                            "type": "bar",
                            "data": [
                                {
                                    "x": r.get("sector_name"),
                                    "y": r.get("net_amount"),
                                }
                                for r in inflow_rows
                            ],
                        }
                    ],
                    description="板块净流入 TopN",
                    metadata={
                        "type": "chart",
                        "dataset": "market_money_flow_top_inflow",
                        "requested_trade_date": requested_trade_date,
                        "as_of_trade_date": as_of_trade_date,
                        "data_freshness": data_freshness,
                        "trend_conclusion_allowed": gate_allowed,
                        "blocked_reason": blocked_reason,
                    },
                    name="Market Money Flow",
                ),
                create_table_artifact(
                    title=f"板块资金净流入 Top{top_n_effective}",
                    columns=[
                        {"key": "rank", "label": "排名"},
                        {"key": "sector_name", "label": "板块"},
                        {"key": "net_amount", "label": "净流入"},
                        {"key": "pct_chg", "label": "涨跌幅(%)"},
                    ],
                    rows=inflow_rows,
                    tag="market_money_flow_top_inflow",
                    description="Market sector capital inflow ranking",
                ),
            ]
            if include_outflow:
                artifacts.append(
                    create_table_artifact(
                        title=f"板块资金净流出 Top{top_n_effective}",
                        columns=[
                            {"key": "rank", "label": "排名"},
                            {"key": "sector_name", "label": "板块"},
                            {"key": "net_amount", "label": "净流出"},
                            {"key": "pct_chg", "label": "涨跌幅(%)"},
                        ],
                        rows=outflow_rows,
                        tag="market_money_flow_top_outflow",
                        description="Market sector capital outflow ranking",
                    )
                )

            artifacts.append(
                create_artifact_envelope(
                    variant="market_money_flow_gate",
                    name="Market Money Flow Gate",
                    content={
                        "requested_trade_date": requested_trade_date,
                        "as_of_trade_date": as_of_trade_date,
                        "data_freshness": data_freshness,
                        "trend_conclusion_allowed": gate_allowed,
                        "blocked_reason": blocked_reason,
                        "top_n": top_n_effective,
                        "inflow_count": inflow,
                        "outflow_count": outflow,
                    },
                    description=(
                        "Trend conclusion gate derived from market money flow freshness "
                        "and rank coverage."
                    ),
                    metadata={"variant": "market_money_flow_gate"},
                    visible_to_llm=True,
                    display_in_report=True,
                )
            )

            return create_artifact_list_response(
                summary=summary_text, artifacts=artifacts
            )
        except Exception as e:
            logger.error(f"Get market money flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取板块资金流向失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def resolve_sector(
        query_text: str,
        intent: str = "trend",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """解析板块查询词，返回稳定 sector_id（resolved/ambiguous/not_found）."""
        if ctx:
            await ctx.info(
                "🔧 解析板块名称",
                extra={"query_text": query_text, "intent": intent},
            )

        try:
            logger.info(
                "MCP tool called: resolve_sector",
                query_text=query_text,
                intent=intent,
            )
            result = await money_flow_use_cases.resolve_sector(
                query_text=query_text,
                intent=intent,
            )
            status = result.get("status", "not_found")
            canonical_name = result.get("canonical_name", "")
            sector_id = result.get("sector_id", "")
            candidates = result.get("candidates", []) or []
            provider_sector_id = result.get("provider_sector_id", "")
            canonical_sector_id = result.get("canonical_sector_id", "")
            canonical_source = result.get("canonical_source", "")

            if status == "resolved":
                summary_text = (
                    f"板块解析成功: {query_text} -> {canonical_name}({sector_id})"
                )
            elif status == "ambiguous":
                candidate_names = _candidate_names(candidates)
                summary_text = (
                    f'板块名称"{query_text}"不明确，候选：'
                    f"{'、'.join([x for x in candidate_names if x])}。"
                )
            else:
                summary_text = f'未找到板块: "{query_text}"'

            artifact = create_artifact_envelope(
                variant="sector_resolve",
                name=f"Sector Resolve: {query_text}",
                content={
                    "query_text": query_text,
                    "intent": intent,
                    "status": status,
                    "sector_id": sector_id,
                    "canonical_name": canonical_name,
                    "provider_sector_id": provider_sector_id,
                    "canonical_sector_id": canonical_sector_id,
                    "canonical_source": canonical_source,
                    "candidates": candidates,
                    "source": result.get("source", ""),
                },
                description=summary_text,
                metadata={
                    "variant": "sector_resolve",
                    "query_text": query_text,
                    "intent": intent,
                    "status": status,
                },
                visible_to_llm=True,
                display_in_report=False,
            )
            if status == "ambiguous":
                response = _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=f"ambiguous sector query: {query_text}",
                    scope={
                        "tool": "resolve_sector",
                        "query_text": query_text,
                        "intent": intent,
                        "status": status,
                        "candidate_count": len(candidates),
                        "retry_same_query_allowed": False,
                    },
                    suggested_reroute=(
                        "从 candidates 选择一个精确板块后，继续调用下游工具；不要重复同参 resolve。"
                    ),
                )
                # Keep explicit decision hint for planner/LLM while using no_data semantics.
                response["decision_required"] = True
                response["resolution_status"] = "ambiguous"
                return response

            if status == "not_found":
                no_data_reason = str(result.get("reason") or "").strip()
                if not no_data_reason:
                    no_data_reason = "sector query not found"
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=no_data_reason,
                    scope={
                        "tool": "resolve_sector",
                        "query_text": query_text,
                        "intent": intent,
                        "status": status,
                        "candidate_count": len(candidates),
                    },
                    suggested_reroute=(
                        "Use a precise sector name from candidates and retry."
                        if status == "ambiguous"
                        else "Try resolve_sector_scope or alternate keywords."
                    ),
                )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Resolve sector failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 板块解析失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_sector_trend(
        sector_name: str = "",
        days: int = 10,
        sector_id: str = "",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """获取板块近 N 天游势与累计涨跌幅."""
        if ctx:
            await ctx.info(
                "🔧 获取板块走势",
                extra={"sector_name": sector_name, "sector_id": sector_id, "days": days},
            )

        try:
            logger.info(
                "MCP tool called: get_sector_trend",
                sector_name=sector_name,
                sector_id=sector_id,
                days=days,
            )
            query_name = str(sector_name or "").strip()
            query_sector_id = str(sector_id or "").strip()
            if query_name and not query_sector_id:
                pre = await _pre_resolve_sector(query_name, intent="trend")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    query_sector_id = str((pre or {}).get("sector_id") or "").strip()
                    query_name = str((pre or {}).get("canonical_name") or query_name).strip() or query_name
                elif pre_status == "ambiguous":
                    candidate_rows = (pre or {}).get("candidates") or []
                    candidate_names = _candidate_names(candidate_rows)
                    candidates_str = "、".join(candidate_names)
                    summary_text = (
                        f'板块名称"{query_name}"不明确，候选：{candidates_str}。'
                        "请先选择一个精确板块名后再查询走势。"
                    )
                    artifact = create_artifact_envelope(
                        variant="sector_trend",
                        name=f"Sector Trend: {query_name}",
                        content={
                            "sector_name": query_name,
                            "index_code": "",
                            "days": days,
                            "candidates": candidate_names,
                            "total_pct_chg": 0,
                            "trend": [],
                        },
                        description=summary_text,
                        metadata={"variant": "sector_trend", "sector_name": query_name},
                        visible_to_llm=True,
                    )
                    return _build_no_data_response(
                        summary=summary_text,
                        artifact=artifact,
                        reason=f"ambiguous sector query: {query_name}",
                        scope={
                            "tool": "get_sector_trend",
                            "sector_name": query_name,
                            "sector_id": "",
                            "days": days,
                            "candidate_count": len(candidate_names),
                        },
                        suggested_reroute=(
                            "从候选里选择一个精确 sector_name，或先调用 resolve_sector 后再查走势。"
                        ),
                    )
            elif query_sector_id and not query_name:
                pre = await _pre_resolve_sector(query_sector_id, intent="trend")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    resolved_name = str((pre or {}).get("canonical_name") or "").strip()
                    resolved_id = str((pre or {}).get("sector_id") or "").strip()
                    if resolved_name:
                        query_name = resolved_name
                    if resolved_id:
                        query_sector_id = resolved_id
            result = await money_flow_use_cases.get_sector_trend(
                sector_name=query_name,
                sector_id=query_sector_id or None,
                days=days,
            )
            query_label = query_name or query_sector_id or "unknown"

            # ── 候选列表：板块名称模糊匹配返回多个候选时 ──────────────────
            if result.get("candidates"):
                candidates: list = result["candidates"]
                candidates_str = "、".join(candidates)
                summary_text = (
                    f'板块名称"{query_label}"不明确，找到以下候选板块：{candidates_str}。'
                    f"请用精确名称重新查询，例如直接使用上述某个名称。"
                )
                artifact = create_artifact_envelope(
                    variant="sector_trend",
                    name=f"Sector Trend: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "days": days,
                        "candidates": candidates,
                        "total_pct_chg": 0,
                        "trend": [],
                    },
                    description=summary_text,
                    metadata={"variant": "sector_trend", "sector_name": query_label},
                    visible_to_llm=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=f"ambiguous sector query: {query_label}",
                    scope={
                        "tool": "get_sector_trend",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "candidate_count": len(candidates),
                    },
                    suggested_reroute=(
                        "Use one exact candidate sector_name and rerun get_sector_trend."
                    ),
                )

            if result.get("error"):
                no_data_reason = str(result.get("error") or "").strip()
                summary_text = f"板块走势数据不可用: {no_data_reason}"
                artifact = create_artifact_envelope(
                    variant="sector_trend",
                    name=f"Sector Trend: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "days": days,
                        "error": no_data_reason,
                        "trend": [],
                    },
                    description=summary_text,
                    metadata={"variant": "sector_trend", "sector_name": query_label},
                    visible_to_llm=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=no_data_reason,
                    scope={
                        "tool": "get_sector_trend",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                    },
                    suggested_reroute=(
                        "Try resolve_sector first, then rerun with exact sector_id/name."
                    ),
                )

            index_name = result.get("sector_name") or query_label
            index_code = result.get("index_code", "")
            total_pct = result.get("total_pct_chg", 0)
            name_with_code = f"{index_name}({index_code})" if index_code else index_name
            summary_text = f"{name_with_code}板块走势（最近{days}天). 累计涨跌幅: {total_pct:+.2f}%."

            artifact = create_artifact_envelope(
                variant="sector_trend",
                name=f"Sector Trend: {index_name}",
                content={
                    "sector_name": index_name,
                    "index_code": index_code,
                    "days": days,
                    "total_pct_chg": total_pct,
                    "trend": result.get("trend", []),
                },
                description=summary_text,
                metadata={
                    "variant": "sector_trend",
                    "sector_name": index_name,
                    "index_code": index_code,
                },
            )
            trend_rows = result.get("trend", [])
            if not isinstance(trend_rows, list) or len(trend_rows) == 0:
                return _build_no_data_response(
                    summary=f"{name_with_code} 板块走势暂无可用数据。",
                    artifact=artifact,
                    reason="sector trend records are empty",
                    scope={
                        "tool": "get_sector_trend",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "resolved_sector": index_name,
                        "index_code": index_code,
                        "days": days,
                    },
                    suggested_reroute=(
                        "Reduce days or reroute to get_sector_money_flow_history/"
                        "get_sector_valuation_metrics."
                    ),
                )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get sector trend failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取板块走势失败", extra={"error": str(e)})
            if _is_no_data_exception(e):
                query_label = sector_name or sector_id or "unknown"
                summary_text = f"{query_label} 板块走势暂无可用数据: {e}"
                artifact = create_artifact_envelope(
                    variant="sector_trend",
                    name=f"Sector Trend: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "days": days,
                        "error": str(e),
                        "trend": [],
                    },
                    description=summary_text,
                    metadata={"variant": "sector_trend", "sector_name": query_label},
                    visible_to_llm=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=str(e),
                    scope={
                        "tool": "get_sector_trend",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                    },
                    suggested_reroute=(
                        "Resolve sector first or switch to neighboring sectors/news evidence."
                    ),
                )
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_ggt_daily(days: int = 60, ctx: Context = None) -> Dict[str, Any]:
        """获取港股通每日成交统计."""
        if ctx:
            await ctx.info("🔧 获取港股通每日成交统计", extra={"days": days})

        try:
            logger.info("MCP tool called: get_ggt_daily", days=days)
            result = await money_flow_use_cases.get_ggt_daily(days)

            rows = result.get("data", [])
            columns = [
                {"key": "trade_date", "label": "日期"},
                {"key": "buy_amount", "label": "买入(亿)", "align": "right"},
                {"key": "sell_amount", "label": "卖出(亿)", "align": "right"},
                {"key": "net_amount", "label": "净买入(亿)", "align": "right"},
                {"key": "buy_volume", "label": "买入笔数(万)", "align": "right"},
                {"key": "sell_volume", "label": "卖出笔数(万)", "align": "right"},
            ]

            latest_row = rows[-1] if rows else {}
            latest_date = latest_row.get("trade_date", "N/A")
            buy_amount = latest_row.get("buy_amount")
            sell_amount = latest_row.get("sell_amount")
            net_amount = latest_row.get("net_amount")
            summary_text = (
                f"港股通成交({len(rows)}日): 最新{latest_date}净买入"
                f"{f'{net_amount:+.2f}亿' if isinstance(net_amount, (int, float)) else 'N/A'}, "
                f"买入{f'{buy_amount:.2f}亿' if isinstance(buy_amount, (int, float)) else 'N/A'}/"
                f"卖出{f'{sell_amount:.2f}亿' if isinstance(sell_amount, (int, float)) else 'N/A'}"
            )

            artifact = create_artifact_envelope(
                variant="table",
                name="HK Stock Connect Daily",
                content={
                    "title": "港股通每日成交统计",
                    "tag": "ggt_daily",
                    "columns": columns,
                    "rows": rows,
                },
                description="Hong Kong Stock Connect daily trading statistics",
                metadata={"variant": "table", "dataset": "ggt_daily"},
                visible_to_llm=False,
                display_in_report=True,
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get ggt daily failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取港股通成交统计失败", extra={"error": str(e)})
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"money-flow"})
    async def get_sector_money_flow_history(
        sector_name: str = "",
        days: int = 20,
        sector_id: str = "",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """获取板块资金流向历史数据

        查询指定行业板块近 N 个交易日的行情走势及主力/散户资金
        净流入情况，帮助判断行业板块的资金动向和热度变化。

        Args:
            sector_name: 板块名称 (如 "白酒", "半导体", "新能源",
                "光伏", "医药", "银行")
            days: 获取最近 N 个交易日数据 (默认 20)
            ctx: FastMCP Context for logging

        Returns:
            ArtifactEnvelope containing sector money flow history
        """
        if ctx:
            await ctx.info(
                "🔧 获取板块资金流向",
                extra={"sector_name": sector_name, "sector_id": sector_id, "days": days},
            )

        try:
            logger.info(
                "MCP tool called: get_sector_money_flow_history",
                sector_name=sector_name,
                sector_id=sector_id,
                days=days,
            )
            query_name = str(sector_name or "").strip()
            query_sector_id = str(sector_id or "").strip()
            if query_name and not query_sector_id:
                pre = await _pre_resolve_sector(query_name, intent="flow")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    query_sector_id = str((pre or {}).get("sector_id") or "").strip()
                    query_name = str((pre or {}).get("canonical_name") or query_name).strip() or query_name
                elif pre_status == "ambiguous":
                    candidate_rows = (pre or {}).get("candidates") or []
                    candidate_names = _candidate_names(candidate_rows)
                    candidates_str = "、".join(candidate_names)
                    summary_text = (
                        f'板块名称"{query_name}"不明确，候选：{candidates_str}。'
                        "请先选择一个精确板块名后再查询资金流。"
                    )
                    empty_artifact = create_artifact_envelope(
                        variant="sector_flow",
                        name=f"Sector Money Flow: {query_name}",
                        content={
                            "sector_name": query_name,
                            "index_code": "",
                            "has_money_flow": False,
                            "records": [],
                            "candidates": candidate_names,
                        },
                        description=summary_text,
                        metadata={"variant": "sector_flow", "sector_name": query_name},
                        visible_to_llm=True,
                    )
                    return _build_no_data_response(
                        summary=summary_text,
                        artifact=empty_artifact,
                        reason=f"ambiguous sector query: {query_name}",
                        scope={
                            "tool": "get_sector_money_flow_history",
                            "sector_name": query_name,
                            "sector_id": "",
                            "days": days,
                            "candidate_count": len(candidate_names),
                        },
                        suggested_reroute=(
                            "从候选里选择一个精确 sector_name，或先调用 resolve_sector 后再查资金流。"
                        ),
                    )
            elif query_sector_id and not query_name:
                pre = await _pre_resolve_sector(query_sector_id, intent="flow")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    resolved_name = str((pre or {}).get("canonical_name") or "").strip()
                    resolved_id = str((pre or {}).get("sector_id") or "").strip()
                    if resolved_name:
                        query_name = resolved_name
                    if resolved_id:
                        query_sector_id = resolved_id
            result = await money_flow_use_cases.get_sector_money_flow_history(
                sector_name=query_name,
                days=days,
                sector_id=query_sector_id or None,
            )
            query_label = query_name or query_sector_id or "unknown"

            # ── 候选列表：板块名称模糊匹配返回多个候选时 ──────────────────
            if result.get("candidates"):
                candidates: list = result["candidates"]
                candidates_str = "、".join(candidates)
                summary_text = (
                    f'板块名称"{query_label}"不明确，找到以下候选板块：{candidates_str}。'
                    f"请用精确名称重新查询，例如直接使用上述某个名称。"
                )
                empty_artifact = create_artifact_envelope(
                    variant="sector_flow",
                    name=f"Sector Money Flow: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "has_money_flow": False,
                        "records": [],
                        "candidates": candidates,
                    },
                    description=summary_text,
                    metadata={"variant": "sector_flow", "sector_name": query_label},
                    visible_to_llm=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=empty_artifact,
                    reason=f"ambiguous sector query: {query_label}",
                    scope={
                        "tool": "get_sector_money_flow_history",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "candidate_count": len(candidates),
                    },
                    suggested_reroute=(
                        "Use one exact candidate sector_name and rerun get_sector_money_flow_history."
                    ),
                )

            if result.get("error"):
                err_msg = result["error"]
                # 如果 error 响应中同时携带了候选列表，拼入提示
                candidates = result.get("candidates") or []
                if candidates:
                    candidates_str = "、".join(candidates)
                    summary_text = (
                        f'板块名称"{query_label}"不明确，找到以下候选板块：{candidates_str}。'
                        f"请用精确名称重新查询，例如直接使用上述某个名称。"
                    )
                else:
                    summary_text = f"未找到板块数据: {err_msg}"
                empty_artifact = create_artifact_envelope(
                    variant="sector_flow",
                    name=f"Sector Money Flow: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "has_money_flow": False,
                        "records": [],
                        "error": err_msg,
                        "candidates": candidates,
                    },
                    description=summary_text,
                    metadata={"variant": "sector_flow", "sector_name": query_label},
                    visible_to_llm=bool(candidates),
                    display_in_report=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=empty_artifact,
                    reason=str(err_msg),
                    scope={
                        "tool": "get_sector_money_flow_history",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "candidate_count": len(candidates),
                    },
                    suggested_reroute=(
                        "Resolve sector first and rerun with exact sector_id/name."
                    ),
                )

            index_name = result.get("sector_name", query_label)
            index_code = result.get("index_code", "")
            records = result.get("records", [])
            summary_info = result.get("summary", {})
            has_flow = result.get("has_money_flow", False)
            amount_unit = (
                result.get("amount_unit")
                or summary_info.get("amount_unit")
                or "unknown"
            )

            # Build summary text
            total_pct = summary_info.get("total_pct_chg", 0)
            trend = summary_info.get("trend", "")
            total_main = summary_info.get("total_main_net")
            flow_source = summary_info.get("flow_source")

            name_with_code = f"{index_name}({index_code})" if index_code else index_name
            lines = [f"{name_with_code} 板块资金流向 (近{len(records)}日):", f"- 区间涨跌幅: {total_pct:.2f}%"]
            if has_flow and total_main is not None:
                lines.append(
                    f"- 主力累计净流入: {_fmt_amount(total_main, amount_unit)}"
                )
                lines.append(f"- 金额单位口径: {_unit_label(amount_unit)}")
                if flow_source:
                    lines.append(f"- 资金流口径: {flow_source}")
            lines.append(f"- 趋势: {trend}")
            summary_text = "\n".join(lines)

            # Build artifact
            artifact = create_artifact_envelope(
                variant="sector_flow",
                name=f"Sector Money Flow: {index_name}",
                content={
                    "sector_name": index_name,
                    "index_code": index_code,
                    "has_money_flow": has_flow,
                    "records": records,
                    "amount_unit": amount_unit,
                },
                description=summary_text,
                metadata={
                    "variant": "sector_flow",
                    "sector_name": index_name,
                    "index_code": index_code,
                    "days": len(records),
                    "amount_unit": amount_unit,
                },
                visible_to_llm=False,
                display_in_report=True,
            )
            if not records:
                return _build_no_data_response(
                    summary=f"{name_with_code} 板块资金流暂无可用记录。",
                    artifact=artifact,
                    reason="sector money flow records are empty",
                    scope={
                        "tool": "get_sector_money_flow_history",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "resolved_sector": index_name,
                        "index_code": index_code,
                        "days": days,
                        "has_money_flow": bool(has_flow),
                    },
                    suggested_reroute=(
                        "Reduce days or reroute to get_sector_trend/get_sector_valuation_metrics."
                    ),
                )

            if ctx:
                await ctx.info(
                    f"✅ 板块资金流向获取完成: {index_name}",
                    extra={"days": len(records), "trend": trend},
                )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(
                f"Get sector money flow history failed: {e}",
                exc_info=True,
            )
            if ctx:
                await ctx.error(
                    f"❌ 获取板块资金流向失败: {sector_name or sector_id or 'unknown'}",
                    extra={"error": str(e)},
                )
            return {
                "error": str(e),
                "variant": "sector_flow",
            }

    @mcp.tool(tags={"money-flow"})
    async def get_sector_valuation_metrics(
        sector_name: str = "",
        days: int = 250,
        sample_size: int = 60,
        sector_id: str = "",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """获取板块估值指标与历史分位（PE/PB）.

        基于板块成分股聚合计算板块层面的 PE(TTM)/PB，并给出历史分位，
        用于判断板块估值安全边际（低估/合理/高估）。
        """
        if ctx:
            await ctx.info(
                "🔧 获取板块估值",
                extra={
                    "sector_name": sector_name,
                    "sector_id": sector_id,
                    "days": days,
                    "sample_size": sample_size,
                },
            )

        try:
            logger.info(
                "MCP tool called: get_sector_valuation_metrics",
                sector_name=sector_name,
                sector_id=sector_id,
                days=days,
                sample_size=sample_size,
            )
            query_name = str(sector_name or "").strip()
            query_sector_id = str(sector_id or "").strip()
            if query_name and not query_sector_id:
                pre = await _pre_resolve_sector(query_name, intent="valuation")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    query_sector_id = str((pre or {}).get("sector_id") or "").strip()
                    query_name = str((pre or {}).get("canonical_name") or query_name).strip() or query_name
                elif pre_status == "ambiguous":
                    candidate_rows = (pre or {}).get("candidates") or []
                    candidate_names = _candidate_names(candidate_rows)
                    summary_text = (
                        f'板块名称"{query_name}"不明确，候选：'
                        f"{'、'.join(candidate_names)}。请使用精确板块名称重试。"
                    )
                    artifact = create_artifact_envelope(
                        variant="sector_valuation_metrics",
                        name=f"Sector Valuation: {query_name}",
                        content={
                            "sector_name": query_name,
                            "index_code": "",
                            "candidates": candidate_names,
                            "history": [],
                        },
                        description=summary_text,
                        metadata={
                            "variant": "sector_valuation_metrics",
                            "sector_name": query_name,
                        },
                        visible_to_llm=True,
                        display_in_report=True,
                    )
                    return _build_no_data_response(
                        summary=summary_text,
                        artifact=artifact,
                        reason=f"ambiguous sector query: {query_name}",
                        scope={
                            "tool": "get_sector_valuation_metrics",
                            "sector_name": query_name,
                            "sector_id": "",
                            "days": days,
                            "sample_size": sample_size,
                            "candidate_count": len(candidate_names),
                        },
                        suggested_reroute=(
                            "从候选里选择一个精确 sector_name，或先调用 resolve_sector 后再查估值。"
                        ),
                    )
            elif query_sector_id and not query_name:
                pre = await _pre_resolve_sector(query_sector_id, intent="valuation")
                pre_status = str((pre or {}).get("status") or "").lower()
                if pre_status == "resolved":
                    resolved_name = str((pre or {}).get("canonical_name") or "").strip()
                    resolved_id = str((pre or {}).get("sector_id") or "").strip()
                    if resolved_name:
                        query_name = resolved_name
                    if resolved_id:
                        query_sector_id = resolved_id
            result = await money_flow_use_cases.get_sector_valuation_metrics(
                sector_name=query_name,
                days=days,
                sample_size=sample_size,
                sector_id=query_sector_id or None,
            )
            query_label = query_name or query_sector_id or "unknown"

            if result.get("candidates"):
                candidates = result.get("candidates", [])
                summary_text = (
                    f'板块名称"{query_label}"不明确，候选：'
                    f"{'、'.join(candidates)}。请使用精确板块名称重试。"
                )
                artifact = create_artifact_envelope(
                    variant="sector_valuation_metrics",
                    name=f"Sector Valuation: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "candidates": candidates,
                        "history": [],
                    },
                    description=summary_text,
                    metadata={
                        "variant": "sector_valuation_metrics",
                        "sector_name": query_label,
                    },
                    visible_to_llm=True,
                    display_in_report=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=f"ambiguous sector query: {query_label}",
                    scope={
                        "tool": "get_sector_valuation_metrics",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "sample_size": sample_size,
                        "candidate_count": len(candidates),
                    },
                    suggested_reroute=(
                        "Use one exact candidate sector_name and rerun get_sector_valuation_metrics."
                    ),
                )

            if result.get("error"):
                summary_text = f"板块估值数据不可用: {result.get('error')}"
                artifact = create_artifact_envelope(
                    variant="sector_valuation_metrics",
                    name=f"Sector Valuation: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": result.get("index_code", ""),
                        "history": [],
                        "error": result.get("error"),
                    },
                    description=summary_text,
                    metadata={
                        "variant": "sector_valuation_metrics",
                        "sector_name": query_label,
                    },
                    visible_to_llm=True,
                    display_in_report=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=str(result.get("error") or "valuation data unavailable"),
                    scope={
                        "tool": "get_sector_valuation_metrics",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "sample_size": sample_size,
                    },
                    suggested_reroute=(
                        "Resolve sector first and rerun with exact sector_id/name."
                    ),
                )

            sector = result.get("sector_name", query_label)
            index_code = result.get("index_code", "")
            current = result.get("current", {}) or {}
            summary = result.get("summary", {}) or {}
            history = result.get("history", []) or []

            pe = current.get("pe_ttm")
            pb = current.get("pb")
            pe_pct = summary.get("pe_ttm_percentile")
            pb_pct = summary.get("pb_percentile")
            level = summary.get("valuation_level", "未知")
            latest_cov = summary.get("coverage_latest")
            used = result.get("member_count_used")
            total = result.get("member_count_total")
            with_data = result.get("member_count_with_data")

            def _fmt_num(v: Any, nd: int = 2) -> str:
                if isinstance(v, (int, float)):
                    return f"{v:.{nd}f}"
                return "N/A"

            def _fmt_pct(v: Any) -> str:
                if isinstance(v, (int, float)):
                    return f"{v:.1f}%"
                return "N/A"

            summary_text = (
                f"{sector}({index_code}) 板块估值(近{len(history)}日): "
                f"PE(TTM)={_fmt_num(pe)}(分位{_fmt_pct(pe_pct)}), "
                f"PB={_fmt_num(pb)}(分位{_fmt_pct(pb_pct)}), "
                f"估值判断={level}; 覆盖={latest_cov}/{used} "
                f"(样本成分{with_data}/{total})"
            )

            artifact = create_artifact_envelope(
                variant="sector_valuation_metrics",
                name=f"Sector Valuation: {sector}",
                content={
                    "sector_name": sector,
                    "index_code": index_code,
                    "current": current,
                    "summary": summary,
                    "history": history,
                    "member_count_total": total,
                    "member_count_used": used,
                    "member_count_with_data": with_data,
                },
                description=summary_text,
                metadata={
                    "variant": "sector_valuation_metrics",
                    "sector_name": sector,
                    "index_code": index_code,
                    "days": len(history),
                },
                visible_to_llm=True,
                display_in_report=True,
            )
            if not history:
                return _build_no_data_response(
                    summary=f"{sector}({index_code}) 板块估值历史数据为空。",
                    artifact=artifact,
                    reason="sector valuation history is empty",
                    scope={
                        "tool": "get_sector_valuation_metrics",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "resolved_sector": sector,
                        "index_code": index_code,
                        "days": days,
                        "sample_size": sample_size,
                    },
                    suggested_reroute=(
                        "Reduce days/sample_size or reroute to sector trend/news evidence."
                    ),
                )

            if ctx:
                await ctx.info(
                    f"✅ 板块估值获取完成: {sector}",
                    extra={"valuation_level": level, "days": len(history)},
                )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get sector valuation metrics failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 获取板块估值失败: {sector_name or sector_id or 'unknown'}",
                    extra={"error": str(e)},
                )
            if _is_no_data_exception(e):
                query_label = sector_name or sector_id or "unknown"
                summary_text = f"{query_label} 板块估值数据暂无可用结果: {e}"
                artifact = create_artifact_envelope(
                    variant="sector_valuation_metrics",
                    name=f"Sector Valuation: {query_label}",
                    content={
                        "sector_name": query_label,
                        "index_code": "",
                        "history": [],
                        "error": str(e),
                    },
                    description=summary_text,
                    metadata={
                        "variant": "sector_valuation_metrics",
                        "sector_name": query_label,
                    },
                    visible_to_llm=True,
                    display_in_report=True,
                )
                return _build_no_data_response(
                    summary=summary_text,
                    artifact=artifact,
                    reason=str(e),
                    scope={
                        "tool": "get_sector_valuation_metrics",
                        "sector_name": sector_name,
                        "sector_id": sector_id,
                        "days": days,
                        "sample_size": sample_size,
                    },
                    suggested_reroute=(
                        "尝试缩短窗口/样本规模，或改路由到 get_sector_trend/"
                        "get_sector_money_flow_history/news 进行补证。"
                    ),
                )
            return {
                "error": str(e),
                "variant": "sector_valuation_metrics",
            }
