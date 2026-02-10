# src/server/mcp/tools/fundamental_tools.py
"""MCP tools for fundamental (financial) data.
Implements `get_financial_reports`.
Returns structured data (JSON).
"""

from typing import Any, Dict

from fastmcp import FastMCP, Context

from src.server.core.use_cases import fundamental as fundamental_use_cases
from src.server.core.dependencies import Container
from src.server.utils.logger import logger
from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_artifact_list_response,
    create_chart_artifact,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError
from src.server.domain.symbols import to_ts_code


def register_fundamental_tools(mcp: FastMCP):
    async def _resolve_ts_code(raw_symbol: str) -> str:
        resolved = await Container.market_gateway().resolve_ticker(raw_symbol)
        return to_ts_code(resolved)

    async def _get_financial_reports_impl(symbol: str, ctx: Context = None) -> Dict[str, Any]:
        """Implementation of get_financial_reports logic."""
        if ctx:
            await ctx.info(f"🔧 获取财务报告: {symbol}", extra={"symbol": symbol})

        try:
            logger.info("MCP tool called: get_financial_reports", symbol=symbol)

            financials = await fundamental_use_cases.get_financials(symbol)
            income = financials.get("income_statement") or []
            ts_code = financials.get("ts_code") or await _resolve_ts_code(symbol)

            def _first_value(row: Dict[str, Any], keys: list[str]) -> Any:
                for key in keys:
                    if key in row and row.get(key) not in (None, ""):
                        return row.get(key)
                return None

            def _parse_number(value: Any) -> float | None:
                if value is None:
                    return None
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    cleaned = value.replace(",", "").strip()
                    if cleaned == "":
                        return None
                    try:
                        return float(cleaned)
                    except ValueError:
                        return None
                return None

            def _normalize_end_date(value: Any) -> str | None:
                if value is None:
                    return None
                if not isinstance(value, str):
                    value = str(value)
                cleaned = (
                    value.strip()
                    .replace("-", "")
                    .replace("/", "")
                    .replace(".", "")
                )
                return cleaned if len(cleaned) >= 8 and cleaned[:8].isdigit() else None

            def _normalize_income_row(row: Dict[str, Any]) -> Dict[str, Any]:
                end_date = _normalize_end_date(
                    _first_value(
                        row,
                        [
                            "end_date",
                            "报告期",
                            "报告日期",
                            "报告日",
                            "报表日期",
                            "日期",
                            "截止日期",
                        ],
                    )
                )
                revenue = _first_value(row, ["revenue", "营业收入", "营业总收入", "主营业务收入"])
                net_income = _first_value(
                    row,
                    ["n_income_attr_p", "归母净利润", "归属于母公司所有者的净利润", "净利润", "n_income"],
                )
                return {
                    "end_date": end_date,
                    "revenue": _parse_number(revenue),
                    "n_income_attr_p": _parse_number(net_income),
                }

            normalized_income = [_normalize_income_row(row) for row in income]
            income_sorted = sorted(
                [row for row in normalized_income if row.get("end_date")],
                key=lambda r: r.get("end_date"),
            )

            def _get_quarter(end_date: str) -> str | None:
                if not end_date or len(end_date) < 8:
                    return None
                mmdd = end_date[4:8]
                if mmdd == "0331":
                    return "Q1"
                if mmdd == "0630":
                    return "Q2"
                if mmdd == "0930":
                    return "Q3"
                if mmdd == "1231":
                    return "Q4"
                return None

            def _build_quarter_bars(rows: list[dict], value_key: str) -> list[dict]:
                yearly: dict[str, dict[str, float]] = {}
                for row in rows:
                    end_date = row.get("end_date")
                    quarter = _get_quarter(end_date)
                    if not quarter:
                        continue
                    year = end_date[:4]
                    value = row.get(value_key)
                    if value is None:
                        continue
                    yearly.setdefault(year, {})[quarter] = float(value)

                # Convert YTD to single-quarter by differencing
                for year, bars in yearly.items():
                    q1 = bars.get("Q1")
                    q2 = bars.get("Q2")
                    q3 = bars.get("Q3")
                    q4 = bars.get("Q4")
                    if q2 is not None and q1 is not None:
                        bars["Q2"] = q2 - q1
                    if q3 is not None and q2 is not None:
                        bars["Q3"] = q3 - (q1 or 0) - (bars.get("Q2") or 0)
                    if q4 is not None:
                        q1v = q1 or 0
                        q2v = bars.get("Q2") or (q2 - q1 if q2 is not None and q1 is not None else 0)
                        q3v = bars.get("Q3") or 0
                        bars["Q4"] = q4 - q1v - q2v - q3v
                data = []
                for year in sorted(yearly.keys()):
                    bars = yearly[year]
                    data.append({
                        "category": year,
                        "bars": {
                            "Q1": bars.get("Q1"),
                            "Q2": bars.get("Q2"),
                            "Q3": bars.get("Q3"),
                            "Q4": bars.get("Q4"),
                        },
                    })
                # compute YoY based on annual total
                prev_total = None
                for item in data:
                    bars = item["bars"]
                    total = sum([v for v in bars.values() if isinstance(v, (int, float))])
                    if prev_total and prev_total != 0:
                        item["line"] = (total / prev_total) - 1
                    else:
                        item["line"] = None
                    prev_total = total
                return data

            revenue_data = _build_quarter_bars(income_sorted, "revenue")
            net_income_data = _build_quarter_bars(
                [
                    {**row, "n_income_attr_p": row.get("n_income_attr_p") or row.get("n_income")}
                    for row in income_sorted
                ],
                "n_income_attr_p",
            )

            artifacts = [
                create_artifact_envelope(
                    component_type="financial_chart",
                    name=f"Revenue Chart: {ts_code}",
                    content={
                        "title": "营业收入",
                        "ts_code": ts_code,
                        "currency": "CNY",
                        "data": revenue_data,
                    },
                    description=f"Revenue quarterly breakdown chart for {ts_code}",
                    metadata={"type": "financial_chart", "ts_code": ts_code, "chart_type": "revenue"},
                ),
                create_artifact_envelope(
                    component_type="financial_chart",
                    name=f"Net Income Chart: {ts_code}",
                    content={
                        "title": "归母净利润",
                        "ts_code": ts_code,
                        "currency": "CNY",
                        "data": net_income_data,
                    },
                    description=f"Net income quarterly breakdown chart for {ts_code}",
                    metadata={"type": "financial_chart", "ts_code": ts_code, "chart_type": "net_income"},
                ),
            ]

            summary_text = f"{ts_code} 财务图表：季度营收分解 + 季度净利润分解"
            
            if ctx:
                await ctx.info(
                    f"✅ 财务报告获取完成: {ts_code}",
                    extra={"ts_code": ts_code}
                )

            return create_artifact_list_response(
                summary=summary_text,
                artifacts=artifacts
            )
            
        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="financial_chart", name=f"{symbol} 财务报告"
            )
        except Exception as e:
            logger.error(f"Get financial report failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取财务报告失败: {symbol}",
                    extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_financial_reports(symbol: str, ctx: Context = None) -> Dict[str, Any]:
        """Get financial reports for the given ticker.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA

        Returns:
            ArtifactEnvelope containing financial analysis
        """
        return await _get_financial_reports_impl(symbol, ctx)

    @mcp.tool(tags={"fundamental"})
    async def get_mainbz_info(
        symbol: str | None = None,
        ts_code: str | None = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get main business composition for the given ticker."""
        if not symbol and not ts_code:
            return {"error": "symbol or ts_code is required"}

        raw_symbol = ts_code or symbol
        if ctx:
            await ctx.info(f"🔧 获取主营业务构成: {raw_symbol}", extra={"symbol": raw_symbol})

        try:
            logger.info("MCP tool called: get_mainbz_info", symbol=raw_symbol)

            data = await fundamental_use_cases.get_mainbz_info(raw_symbol)
            rows = data.get("rows", [])
            ts_code = data.get("ts_code") or await _resolve_ts_code(raw_symbol)

            type_map = {
                "P": ("分产品", "产品"),
                "D": ("分地区", "地区"),
                "I": ("分行业", "行业"),
            }

            artifacts = []
            for dim, (title_suffix, subtitle) in type_map.items():
                dim_rows = [r for r in rows if r.get("type") == dim]
                if not dim_rows:
                    continue

                periods_map = {}
                for r in dim_rows:
                    end_date = r.get("end_date")
                    if not end_date:
                        continue
                    periods_map.setdefault(end_date, []).append(
                        {"name": r.get("bz_item"), "value": r.get("bz_sales")}
                    )

                periods = [{"date": d, "data": v} for d, v in sorted(periods_map.items(), key=lambda x: x[0], reverse=True)]
                default_date = periods[0]["date"] if periods else ""

                artifacts.append(
                    create_artifact_envelope(
                        component_type="pie_chart",
                        name=f"Main Business Revenue Composition ({title_suffix}): {ts_code}",
                        content={
                            "title": f"主营业务营收构成 - {title_suffix}",
                            "subtitle": subtitle,
                            "ts_code": ts_code,
                            "currency": "CNY",
                            "period_label": "报告期",
                            "default_date": default_date,
                            "periods": periods,
                        },
                        description=f"Main business revenue composition ({title_suffix}) for {ts_code}",
                        metadata={
                            "type": "pie_chart",
                            "ts_code": ts_code,
                            "dimension": dim,
                            "source": "fina_mainbz",
                            "metric": "bz_sales",
                        },
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            summary_text = f"{ts_code} 主营业务收入构成（分产品/分地区/分行业）"

            if ctx:
                await ctx.info(
                    f"✅ 主营业务构成获取完成: {ts_code}",
                    extra={"rows": len(rows)}
                )

            return create_artifact_list_response(
                summary=summary_text,
                artifacts=artifacts
            )

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="main_business", name=f"{symbol} 主营业务构成"
            )
        except Exception as e:
            logger.error(f"Get main business info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取主营业务构成失败: {symbol}",
                    extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_shareholder_info(
        symbol: str | None = None,
        ts_code: str | None = None,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get shareholder info for the given ticker."""
        if not symbol and not ts_code:
            return {"error": "symbol or ts_code is required"}
        raw_symbol = ts_code or symbol
        if ctx:
            await ctx.info(f"🔧 获取股东信息: {raw_symbol}", extra={"symbol": raw_symbol})

        try:
            logger.info("MCP tool called: get_shareholder_info", symbol=raw_symbol)

            data = await fundamental_use_cases.get_shareholder_info(raw_symbol)
            holder_data = data.get("data", {})
            ts_code = data.get("ts_code") or await _resolve_ts_code(raw_symbol)

            def _latest_by_date(rows: list[dict], date_key: str) -> tuple[str, list[dict]]:
                if not rows:
                    return "", []
                # normalize date to sortable string
                def _date_val(r):
                    v = r.get(date_key) or ""
                    return str(v)
                latest = max(rows, key=_date_val).get(date_key) or ""
                latest_rows = [r for r in rows if r.get(date_key) == latest]
                return str(latest), latest_rows

            def _fmt_pct(val: Any) -> str | None:
                if val is None or val == "":
                    return None
                try:
                    num = float(val)
                    return f"{num:.1f}%"
                except Exception:
                    s = str(val)
                    return s if s.endswith("%") else s

            def _parse_date(val: Any) -> str | None:
                if val is None:
                    return None
                s = str(val)
                if len(s) == 8 and s.isdigit():
                    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                return s

            artifacts = []

            top10 = holder_data.get("top10_holders", [])
            if top10:
                latest_date, rows = _latest_by_date(top10, "end_date")
                mapped = []
                for r in rows:
                    mapped.append({
                        "holder_name": r.get("holder_name"),
                        "hold_amount": r.get("hold_amount"),
                        "hold_ratio": _fmt_pct(r.get("hold_ratio")),
                        "end_date": _parse_date(r.get("end_date")),
                        "ann_date": _parse_date(r.get("ann_date")),
                    })
                artifacts.append(
                    create_artifact_envelope(
                        component_type="table",
                        name=f"前十大股东: {ts_code}",
                        content={
                            "title": "前十大股东",
                            "tag": ts_code,
                            "date": _parse_date(latest_date),
                            "columns": [
                                {"key": "holder_name", "label": "股东名称"},
                                {"key": "hold_amount", "label": "持股数量", "align": "right", "value_meta": {"type": "compact"}},
                                {"key": "hold_ratio", "label": "持股比例", "align": "right"},
                                {"key": "end_date", "label": "报告期"},
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Top 10 shareholders for {ts_code}",
                        metadata={"type": "table", "ts_code": ts_code, "dataset": "top10_holders"},
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            float10 = holder_data.get("top10_floatholders", [])
            if float10:
                latest_date, rows = _latest_by_date(float10, "end_date")
                mapped = []
                for r in rows:
                    mapped.append({
                        "holder_name": r.get("holder_name"),
                        "hold_amount": r.get("hold_amount"),
                        "hold_float_ratio": _fmt_pct(r.get("hold_float_ratio") or r.get("hold_ratio")),
                        "end_date": _parse_date(r.get("end_date")),
                        "ann_date": _parse_date(r.get("ann_date")),
                    })
                artifacts.append(
                    create_artifact_envelope(
                        component_type="table",
                        name=f"前十大流通股东: {ts_code}",
                        content={
                            "title": "前十大流通股东",
                            "tag": ts_code,
                            "date": _parse_date(latest_date),
                            "columns": [
                                {"key": "holder_name", "label": "股东名称"},
                                {"key": "hold_amount", "label": "持股数量", "align": "right", "value_meta": {"type": "compact"}},
                                {"key": "hold_float_ratio", "label": "占流通股本比例", "align": "right"},
                                {"key": "end_date", "label": "报告期"},
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Top 10 tradable shareholders for {ts_code}",
                        metadata={"type": "table", "ts_code": ts_code, "dataset": "top10_floatholders"},
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            holder_num = holder_data.get("holder_number", [])
            if holder_num:
                trend = []
                for r in holder_num:
                    trend.append({
                        "end_date": _parse_date(r.get("end_date") or r.get("ann_date")),
                        "holder_num": r.get("holder_num"),
                    })
                artifacts.append(
                    create_artifact_envelope(
                        component_type="holder_number_trend",
                        name=f"股东户数变化趋势: {ts_code}",
                        content={"ts_code": ts_code, "holder_number_trend": trend},
                        description=f"Shareholder count trend chart (3 years) for {ts_code}",
                        metadata={"type": "holder_number_trend", "ts_code": ts_code, "dataset": "holder_number_trend"},
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            holder_trade = holder_data.get("holder_trade", [])
            if holder_trade:
                # Filter last 1 year by ann_date if available
                from datetime import datetime, timedelta
                cutoff = datetime.now() - timedelta(days=365)
                filtered = []
                for r in holder_trade:
                    ann = _parse_date(r.get("ann_date"))
                    if ann and len(ann) == 10:
                        try:
                            if datetime.strptime(ann, "%Y-%m-%d") < cutoff:
                                continue
                        except Exception:
                            pass
                    filtered.append(r)
                rows = filtered
                mapped = []
                for r in rows:
                    mapped.append({
                        "holder_name": r.get("holder_name"),
                        "holder_type": r.get("holder_type"),
                        "in_de": r.get("in_de"),
                        "change_vol": r.get("change_vol"),
                        "avg_price": r.get("avg_price"),
                        "ann_date": _parse_date(r.get("ann_date")),
                    })
                artifacts.append(
                    create_artifact_envelope(
                        component_type="table",
                        name=f"股东增减持情况: {ts_code}",
                        content={
                            "title": "股东增减持情况",
                            "tag": ts_code,
                            "columns": [
                                {"key": "holder_name", "label": "股东名称"},
                                {"key": "holder_type", "label": "股东类型"},
                                {"key": "in_de", "label": "增减持"},
                                {"key": "change_vol", "label": "变动数量", "align": "right", "value_meta": {"type": "compact", "signDisplay": "exceptZero"}},
                                {"key": "avg_price", "label": "均价", "align": "right", "value_meta": {"type": "number"}},
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Shareholder trading records (1 year) for {ts_code}",
                        metadata={"type": "table", "ts_code": ts_code, "dataset": "holder_trade"},
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            summary_text = f"{ts_code} 前十股东/流通股东、股东人数趋势、股东交易记录"

            if ctx:
                await ctx.info(f"✅ 股东信息获取完成: {ts_code}")

            return create_artifact_list_response(
                summary=summary_text,
                artifacts=artifacts
            )

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="shareholder_info", name=f"{symbol} 股东信息"
            )
        except Exception as e:
            logger.error(f"Get shareholder info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取股东信息失败: {symbol}",
                    extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_dividend_info(symbol: str, ctx: Context = None) -> Dict[str, Any]:
        """Get dividend history info for the given ticker.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA

        Returns:
            ArtifactEnvelope containing dividend history table
        """
        if ctx:
            await ctx.info(f"🔧 获取分红送股信息: {symbol}", extra={"symbol": symbol})

        try:
            logger.info("MCP tool called: get_dividend_info", symbol=symbol)

            data = await fundamental_use_cases.get_dividend_info(symbol)
            rows = data.get("rows", [])
            ts_code = data.get("ts_code") or await _resolve_ts_code(symbol)

            columns = [
                {"key": "end_date", "label": "分红年度"},
                {"key": "div_proc", "label": "实施进度"},
                {"key": "cash_div", "label": "每股分红(税后)", "align": "right"},
                {"key": "cash_div_tax", "label": "每股分红(税前)", "align": "right"},
                {"key": "stk_div", "label": "每股送转", "align": "right"},
                {"key": "stk_bo_rate", "label": "每股送股", "align": "right"},
                {"key": "stk_co_rate", "label": "每股转增", "align": "right"},
                {"key": "record_date", "label": "股权登记日"},
                {"key": "ex_date", "label": "除权除息日"},
                {"key": "pay_date", "label": "派息日"},
                {"key": "ann_date", "label": "预案公告日"},
                {"key": "imp_ann_date", "label": "实施公告日"},
            ]

            table_artifact = create_artifact_envelope(
                component_type="table",
                name=f"Dividend History: {ts_code}",
                content={
                    "title": "分红送股历史",
                    "tag": ts_code,
                    "columns": columns,
                    "rows": rows,
                },
                description=f"Dividend history for {ts_code}",
                metadata={
                    "type": "table",
                    "ts_code": ts_code,
                    "dataset": "dividend",
                },
                visible_to_llm=False,
                display_in_report=True,
            )

            summary_text = f"{ts_code} 分红送股历史: 共 {len(rows)} 条记录"

            if ctx:
                await ctx.info(
                    f"✅ 分红送股信息获取完成: {ts_code}",
                    extra={"rows": len(rows)}
                )

            return create_artifact_response(
                summary=summary_text,
                artifact=table_artifact
            )

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="dividend_info", name=f"{symbol} 分红送股"
            )
        except Exception as e:
            logger.error(f"Get dividend info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取分红送股信息失败: {symbol}",
                    extra={"error": str(e)}
                )
            return {"error": str(e)}
