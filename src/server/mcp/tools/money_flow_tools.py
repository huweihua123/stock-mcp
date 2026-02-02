# src/server/mcp/tools/money_flow_tools.py
"""MCP tools for money flow analysis.
Provides stock money flow and north bound (HSGT) flow data.
Returns structured data (JSON) for frontend visualization.

使用新版 Artifact 结构:
- component_type 标准化
- 参照竞品格式
"""

from typing import Any, Dict

from fastmcp import FastMCP, Context

from src.server.core.use_cases import money_flow as money_flow_use_cases
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_artifact_list_response,
    ComponentType,
    create_chart_artifact,
)


def register_money_flow_tools(mcp: FastMCP):
    """Register money flow analysis tools."""

    def _normalize_ticker(symbol: str) -> str:
        if ":" in symbol:
            return symbol.upper()
        if "." in symbol:
            code, suffix = symbol.split(".", 1)
            suffix = suffix.upper()
            if suffix == "SH":
                return f"SSE:{code}"
            if suffix == "SZ":
                return f"SZSE:{code}"
            if suffix == "BJ":
                return f"BSE:{code}"
            return symbol
        s = symbol.upper().strip()
        if len(s) == 6 and s.isdigit():
            if s.startswith("6"):
                return f"SSE:{s}"
            if s.startswith(("0", "3")):
                return f"SZSE:{s}"
            if s.startswith("8"):
                return f"BSE:{s}"
        return s

    def _ts_code_from_symbol(symbol: str) -> str:
        if "." in symbol:
            return symbol.upper()
        if ":" in symbol:
            ex, code = symbol.split(":", 1)
            ex = ex.upper()
            if ex == "SSE":
                return f"{code}.SH"
            if ex == "SZSE":
                return f"{code}.SZ"
            if ex == "BSE":
                return f"{code}.BJ"
        return symbol.upper()

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
            symbol = _normalize_ticker(symbol)
            logger.info("MCP tool called: get_money_flow", symbol=symbol, days=days)
            result = await money_flow_use_cases.get_money_flow(symbol, days)
            result["component_type"] = "money_flow"
            ts_code = result.get("ts_code") or _ts_code_from_symbol(symbol)

            # 构建摘要信息给 LLM（精简版）
            summary = result.get("summary", {})
            total_main = summary.get("total_main_net", 0)
            total_retail = summary.get("total_retail_net", 0)
            total_net = total_main + total_retail
            trend = summary.get("trend", "未知")

            # 格式化金额显示
            def _fmt_amount(val: float) -> str:
                if abs(val) >= 1e8:
                    return f"{val/1e8:.2f}亿"
                if abs(val) >= 1e4:
                    return f"{val/1e4:.2f}万"
                return f"{val:.0f}元"

            summary_text = (
                f"{symbol}主力资金流（{days}日）:\n"
                f"- 大/超大单净流入(主力口径): {_fmt_amount(total_main)}\n"
                f"- 整体净流{'入' if total_net >= 0 else '出'}: {_fmt_amount(total_net)}\n"
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

            content = {"ts_code": ts_code, "records": records}

            # 包装为 ArtifactEnvelope (竞品格式)
            artifact = create_artifact_envelope(
                component_type="money_flow",
                name=f"Money Flow: {ts_code}",
                content=content,
                description=(
                    f"Main Force (Institutional) Capital Flow: {ts_code} (Last {days} days). "
                    "Visualizes daily net inflow/outflow trends of Large & Extra-Large orders. "
                    f"Overall Trend: {trend}."
                ),
                metadata={"type": "money_flow", "ts_code": ts_code, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            
            return create_artifact_response(
                summary=summary_text,
                artifact=artifact
            )

        except Exception as e:
            logger.error(f"Get money flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(
                    f"❌ 获取资金流向失败: {symbol}", extra={"error": str(e)}
                )
            ts_code = _ts_code_from_symbol(symbol)
            empty = {"ts_code": ts_code, "records": []}
            artifact = create_artifact_envelope(
                component_type="money_flow",
                name=f"Money Flow: {ts_code}",
                content=empty,
                description="No money flow data",
                metadata={"type": "money_flow", "ts_code": ts_code, "days": days},
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary="No money flow data", artifact=artifact)

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
            result["component_type"] = "north_bound_flow"

            # 构建摘要
            summary = result.get("summary", {})
            total_net = summary.get("total_net", 0)
            amount_str = f"{total_net:.2f}亿" if total_net else "暂无数据"

            summary_text = f"近{days}日北向资金简报:\n- 累计净流入: {amount_str}"

            if ctx:
                await ctx.info(
                    f"✅ 北向资金流向获取完成", extra={"total_net": amount_str}
                )

            artifact = create_artifact_envelope(
                component_type="market_liquidity",
                name="北向资金流向",
                content=result,
                description=summary_text,
            )
            
            return create_artifact_response(
                summary=summary_text,
                artifact=artifact
            )

        except Exception as e:
            logger.error(f"Get north bound flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error(f"❌ 获取北向资金流向失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "north_bound_flow"}

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
        if not symbol and not ts_code:
            return {"error": "symbol or ts_code is required"}
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
            result["component_type"] = "chip_distribution"

            # Normalize to competitor format
            ts_code = result.get("ts_code") or result.get("symbol") or raw_symbol
            if ":" in ts_code:
                ts_code = ts_code.replace("SSE:", "").replace("SZSE:", "").replace("BSE:", "")
                if ts_code.isdigit() and raw_symbol and ":" in raw_symbol:
                    ex = raw_symbol.split(":", 1)[0]
                    suffix = "SH" if ex == "SSE" else "SZ" if ex == "SZSE" else "BJ"
                    ts_code = f"{ts_code}.{suffix}"

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
                component_type="chip_distribution",
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
                    "profit_ratio": result.get("profit_ratio") or summary.get("profit_ratio"),
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
                    "type": "chip_distribution",
                    "ts_code": ts_code,
                    "trade_date": result.get("trade_date"),
                    "chip_trade_date": result.get("chip_trade_date"),
                    "price_trade_date": result.get("price_trade_date"),
                },
            )
            
            return create_artifact_response(
                summary=summary_text,
                artifact=artifact
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
                "component_type": "chip_distribution",
            }

    @mcp.tool(tags={"money-flow"})
    async def get_money_supply(ctx: Context = None) -> Dict[str, Any]:
        """获取中国货币流动性 (M1/M2)."""
        if ctx:
            await ctx.info("🔧 获取货币供应量")

        try:
            logger.info("MCP tool called: get_money_supply")
            result = await money_flow_use_cases.get_money_supply()
            result["component_type"] = "money_supply"

            summary_text = "中国货币流动性：M1/M2 增速与剪刀差（近 5 年）"

            content = result.get("data", [])
            artifact = create_artifact_envelope(
                component_type="money_supply",
                name="Money Supply Data",
                content=content,
                description=summary_text,
                metadata={"type": "money_supply"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get money supply failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取货币供应量失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "money_supply"}

    @mcp.tool(tags={"money-flow"})
    async def get_inflation_data(ctx: Context = None) -> Dict[str, Any]:
        """获取中国月度通胀指标 (CPI/PPI)."""
        if ctx:
            await ctx.info("🔧 获取通胀指标")

        try:
            logger.info("MCP tool called: get_inflation_data")
            result = await money_flow_use_cases.get_inflation_data()
            result["component_type"] = "inflation_data"

            summary_text = "中国月度通胀指标：CPI、PPI 及价差（近 5 年）"

            data = result.get("data", {})
            cpi = data.get("CPI", []) if isinstance(data, dict) else []
            ppi = data.get("PPI", []) if isinstance(data, dict) else []
            artifact = create_artifact_envelope(
                component_type="inflation_data",
                name="Inflation Data",
                content={"cpi": cpi, "ppi": ppi},
                description=summary_text,
                metadata={"type": "inflation_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get inflation data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取通胀指标失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "inflation_data"}

    @mcp.tool(tags={"money-flow"})
    async def get_pmi_data(ctx: Context = None) -> Dict[str, Any]:
        """获取中国官方 PMI 数据."""
        if ctx:
            await ctx.info("🔧 获取 PMI")

        try:
            logger.info("MCP tool called: get_pmi_data")
            result = await money_flow_use_cases.get_pmi_data()
            result["component_type"] = "pmi_data"

            summary_text = "中国官方 PMI：制造业/非制造业及分项（近 5 年）"

            content = result.get("data", [])
            artifact = create_artifact_envelope(
                component_type="pmi_data",
                name="PMI Data",
                content=content,
                description=summary_text,
                metadata={"type": "pmi_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get PMI data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取 PMI 数据失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "pmi_data"}

    @mcp.tool(tags={"money-flow"})
    async def get_gdp_data(ctx: Context = None) -> Dict[str, Any]:
        """获取中国季度 GDP 增长数据."""
        if ctx:
            await ctx.info("🔧 获取 GDP")

        try:
            logger.info("MCP tool called: get_gdp_data")
            result = await money_flow_use_cases.get_gdp_data()
            result["component_type"] = "gdp_data"

            summary_text = "中国季度 GDP 增长：总量与三产结构（近 5 年）"

            content = result.get("data", [])
            artifact = create_artifact_envelope(
                component_type="gdp_data",
                name="GDP Data",
                content=content,
                description=summary_text,
                metadata={"type": "gdp_data"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get GDP data failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取 GDP 数据失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "gdp_data"}

    @mcp.tool(tags={"money-flow"})
    async def get_social_financing(months: int = 60, ctx: Context = None) -> Dict[str, Any]:
        """获取中国社会融资总量."""
        if ctx:
            await ctx.info("🔧 获取社会融资总量", extra={"months": months})

        try:
            logger.info("MCP tool called: get_social_financing")
            result = await money_flow_use_cases.get_social_financing()
            result["component_type"] = "social_financing"

            summary_text = "China Total Social Financing (TSF): Aggregate Credit Demand, Monthly Increments & Stock Growth Trends - Last 5 Years"

            data = result.get("data", []) or []
            # Normalize & slice latest N months (desc)
            data = sorted(data, key=lambda x: str(x.get("month", "")), reverse=True)
            if months and months > 0:
                data = data[:months]
            content = [
                {
                    "month": r.get("month"),
                    "inc_month": r.get("inc_month"),
                    "inc_cumval": r.get("inc_cumval"),
                    "stk_endval": r.get("stk_endval"),
                    "stk_yoy": r.get("stk_yoy"),
                }
                for r in data
            ]
            artifact = create_artifact_envelope(
                component_type="social_financing",
                name="Social Financing Data",
                content=content,
                description=summary_text,
                metadata={"type": "social_financing"},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get social financing failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取社会融资失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "social_financing"}

    @mcp.tool(tags={"money-flow"})
    async def get_interest_rates(
        shibor_days: int = 252, lpr_months: int = 60, ctx: Context = None
    ) -> Dict[str, Any]:
        """获取中国利率数据 (SHIBOR + LPR)."""
        if ctx:
            await ctx.info("🔧 获取利率数据", extra={"shibor_days": shibor_days, "lpr_months": lpr_months})

        try:
            logger.info("MCP tool called: get_interest_rates")
            result = await money_flow_use_cases.get_interest_rates()
            result["component_type"] = "interest_rates"

            summary_text = "中国利率：SHIBOR 期限结构 + LPR"

            shibor = result.get("data", {}).get("shibor", []) or []
            lpr = result.get("data", {}).get("lpr", []) or []

            shibor = sorted(shibor, key=lambda x: str(x.get("date", "")), reverse=True)
            lpr = sorted(lpr, key=lambda x: str(x.get("date") or x.get("month", "")), reverse=True)

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

            artifacts = [
                create_artifact_envelope(
                    component_type="interest_rates_shibor",
                    name="SHIBOR Interest Rates",
                    content=shibor_content,
                    description="China Interbank Offered Rate (SHIBOR): Term Structure (Overnight to 1 Year) - Last 1 Year",
                    metadata={"type": "interest_rates_shibor"},
                ),
                create_artifact_envelope(
                    component_type="interest_rates_lpr",
                    name="LPR Interest Rates",
                    content=lpr_content,
                    description="China Loan Prime Rate (LPR): 1-Year and 5-Year Benchmark Lending Rates - Last 5 Years",
                    metadata={"type": "interest_rates_lpr"},
                ),
            ]

            return create_artifact_list_response(summary=summary_text, artifacts=artifacts)
        except Exception as e:
            logger.error(f"Get interest rates failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取利率失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "interest_rates"}

    @mcp.tool(tags={"money-flow"})
    async def get_market_liquidity(days: int = 60, ctx: Context = None) -> Dict[str, Any]:
        """获取 A 股市场流动性指标."""
        if ctx:
            await ctx.info("🔧 获取市场流动性", extra={"days": days})

        try:
            logger.info("MCP tool called: get_market_liquidity", days=days)
            result = await money_flow_use_cases.get_market_liquidity(days)
            result["component_type"] = "market_liquidity"

            summary_text = "A 股市场流动性指标"

            data = result.get("data", {})
            north_flow = data.get("north_flow", [])
            margin = data.get("margin", [])

            north_series = []
            if north_flow:
                north_series = [
                    {
                        "name": "北向净流入",
                        "type": "line",
                        "data": [{"x": r.get("trade_date"), "y": r.get("north_money")} for r in north_flow],
                    }
                ]

            margin_series = []
            if margin:
                margin_series = [
                    {
                        "name": "融资融券余额",
                        "type": "line",
                        "data": [{"x": r.get("trade_date"), "y": r.get("rzrqye")} for r in margin],
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
                    metadata={"type": "chart", "dataset": "north_flow"},
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
                    metadata={"type": "chart", "dataset": "margin"},
                    name="Margin Balance",
                ),
            ]

            return create_artifact_list_response(summary=summary_text, artifacts=artifacts)
        except Exception as e:
            logger.error(f"Get market liquidity failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取市场流动性失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "market_liquidity"}

    @mcp.tool(tags={"money-flow"})
    async def get_market_money_flow(trade_date: str = None, ctx: Context = None) -> Dict[str, Any]:
        """获取板块资金流向统计."""
        if ctx:
            await ctx.info("🔧 获取板块资金流向", extra={"trade_date": trade_date})

        try:
            logger.info("MCP tool called: get_market_money_flow", trade_date=trade_date)
            result = await money_flow_use_cases.get_market_money_flow(trade_date)
            result["component_type"] = "market_money_flow"

            summary_text = "板块资金流向：净流入/净流出板块数量及全市场净流入"

            data = result.get("data", [])
            total_net = 0.0
            inflow = 0
            outflow = 0
            for row in data:
                net = row.get("net_mf_amount") or row.get("net_amount") or 0
                total_net += net
                if net >= 0:
                    inflow += 1
                else:
                    outflow += 1

            summary_text = (
                f"板块资金流向（{trade_date or '最新'}）：净流入{inflow}个、净流出{outflow}个，"
                f"全市场净流入 {total_net:.2f}"
            )

            artifacts = [
                create_chart_artifact(
                    title="板块资金流向",
                    chart_type="bar",
                    unit="",
                    x_label="板块",
                    y_label="净流入",
                    series=[{
                        "name": "净流入",
                        "type": "bar",
                        "data": [{"x": r.get("name") or r.get("industry"), "y": r.get("net_mf_amount") or r.get("net_amount")} for r in data],
                    }],
                    description="板块资金流向分布",
                    metadata={"type": "chart", "dataset": "market_money_flow"},
                    name="Market Money Flow",
                )
            ]

            return create_artifact_list_response(summary=summary_text, artifacts=artifacts)
        except Exception as e:
            logger.error(f"Get market money flow failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取板块资金流向失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "market_money_flow"}

    @mcp.tool(tags={"money-flow"})
    async def get_sector_trend(sector_name: str, days: int = 10, ctx: Context = None) -> Dict[str, Any]:
        """获取板块近 N 天游势与累计涨跌幅."""
        if ctx:
            await ctx.info("🔧 获取板块走势", extra={"sector_name": sector_name, "days": days})

        try:
            logger.info("MCP tool called: get_sector_trend", sector_name=sector_name, days=days)
            result = await money_flow_use_cases.get_sector_trend(sector_name, days)

            total_pct = result.get("total_pct_chg", 0)
            summary_text = f"{sector_name}板块走势（最近{days}天). 累计涨跌幅: {total_pct:+.2f}%."

            artifact = create_artifact_envelope(
                component_type="sector_trend",
                name=f"Sector Trend: {sector_name}",
                content={
                    "sector_name": sector_name,
                    "days": days,
                    "total_pct_chg": total_pct,
                    "trend": result.get("trend", []),
                },
                description=summary_text,
                metadata={"type": "sector_trend", "sector_name": sector_name},
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get sector trend failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取板块走势失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "sector_trend"}

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

            summary_text = "港股通每日成交统计"

            artifact = create_artifact_envelope(
                component_type="table",
                name="HK Stock Connect Daily",
                content={
                    "title": "港股通每日成交统计",
                    "tag": "ggt_daily",
                    "columns": columns,
                    "rows": rows,
                },
                description="Hong Kong Stock Connect daily trading statistics",
                metadata={"type": "table", "dataset": "ggt_daily"},
                visible_to_llm=False,
                display_in_report=True,
            )

            return create_artifact_response(summary=summary_text, artifact=artifact)
        except Exception as e:
            logger.error(f"Get ggt daily failed: {e}", exc_info=True)
            if ctx:
                await ctx.error("❌ 获取港股通成交统计失败", extra={"error": str(e)})
            return {"error": str(e), "component_type": "ggt_daily"}
