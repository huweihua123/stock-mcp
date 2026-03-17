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

    async def _get_financial_reports_impl(
        symbol: str, ctx: Context = None
    ) -> Dict[str, Any]:
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
                    value.strip().replace("-", "").replace("/", "").replace(".", "")
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
                revenue = _first_value(
                    row, ["revenue", "营业收入", "营业总收入", "主营业务收入"]
                )
                net_income = _first_value(
                    row,
                    [
                        "n_income_attr_p",
                        "归母净利润",
                        "归属于母公司所有者的净利润",
                        "净利润",
                        "n_income",
                    ],
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
                        q2v = bars.get("Q2") or (
                            q2 - q1 if q2 is not None and q1 is not None else 0
                        )
                        q3v = bars.get("Q3") or 0
                        bars["Q4"] = q4 - q1v - q2v - q3v
                data = []
                for year in sorted(yearly.keys()):
                    bars = yearly[year]
                    data.append(
                        {
                            "category": year,
                            "bars": {
                                "Q1": bars.get("Q1"),
                                "Q2": bars.get("Q2"),
                                "Q3": bars.get("Q3"),
                                "Q4": bars.get("Q4"),
                            },
                        }
                    )
                # compute YoY based on annual total
                prev_total = None
                for item in data:
                    bars = item["bars"]
                    total = sum(
                        [v for v in bars.values() if isinstance(v, (int, float))]
                    )
                    if prev_total and prev_total != 0:
                        item["line"] = (total / prev_total) - 1
                    else:
                        item["line"] = None
                    prev_total = total
                return data

            revenue_data = _build_quarter_bars(income_sorted, "revenue")
            net_income_data = _build_quarter_bars(
                [
                    {
                        **row,
                        "n_income_attr_p": row.get("n_income_attr_p")
                        or row.get("n_income"),
                    }
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
                    metadata={
                        "type": "financial_chart",
                        "ts_code": ts_code,
                        "chart_type": "revenue",
                    },
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
                    metadata={
                        "type": "financial_chart",
                        "ts_code": ts_code,
                        "chart_type": "net_income",
                    },
                ),
            ]

            latest_rev_year = revenue_data[-1]["category"] if revenue_data else "N/A"
            latest_ni_year = net_income_data[-1]["category"] if net_income_data else "N/A"
            latest_rev_total = (
                sum(
                    v
                    for v in (revenue_data[-1].get("bars", {}) or {}).values()
                    if isinstance(v, (int, float))
                )
                if revenue_data
                else None
            )
            latest_ni_total = (
                sum(
                    v
                    for v in (net_income_data[-1].get("bars", {}) or {}).values()
                    if isinstance(v, (int, float))
                )
                if net_income_data
                else None
            )
            rev_yoy = revenue_data[-1].get("line") if revenue_data else None
            ni_yoy = net_income_data[-1].get("line") if net_income_data else None

            summary_text = (
                f"{ts_code} 财务趋势: 最新年度营收"
                f"{(latest_rev_total / 1e8):.2f}亿元"
                f"{f' (同比{rev_yoy:+.1%})' if isinstance(rev_yoy, (int, float)) else ''}; "
                f"归母净利润{(latest_ni_total / 1e8):.2f}亿元"
                f"{f' (同比{ni_yoy:+.1%})' if isinstance(ni_yoy, (int, float)) else ''}; "
                f"样本年度={latest_rev_year}/{latest_ni_year}"
                if isinstance(latest_rev_total, (int, float))
                and isinstance(latest_ni_total, (int, float))
                else f"{ts_code} 财务趋势: 季度营收与净利润图表已生成"
            )

            if ctx:
                await ctx.info(
                    f"✅ 财务报告获取完成: {ts_code}", extra={"ts_code": ts_code}
                )

            response: Dict[str, Any] = {
                **create_artifact_list_response(
                    summary=summary_text, artifacts=artifacts
                )
            }
            response["tool_scope"] = (
                "This tool provides revenue/net-income trend charts only, not a full fundamental report."
            )
            response["coverage"] = {
                "provided": [
                    "revenue_trend",
                    "net_income_trend",
                ],
                "missing": [
                    "main_business_structure",
                    "shareholder_structure",
                    "dividend_history",
                    "valuation_metrics",
                    "profitability_ratios",
                ],
            }
            response["next_recommended_tools"] = [
                "get_mainbz_info",
                "get_shareholder_info",
                "get_dividend_info",
            ]
            return response

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
                    f"❌ 获取财务报告失败: {symbol}", extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_financial_reports(symbol: str, ctx: Context = None) -> Dict[str, Any]:
        """Get revenue/net-income trend charts for the given ticker.

        This tool is intentionally narrow: it focuses on trend charts and does
        not represent a complete fundamental analysis package.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
                - 美股: NASDAQ:AAPL, NYSE:TSLA

        Returns:
            Artifact list response with:
            - summary + artifacts (trend charts)
            - coverage (provided/missing dimensions)
            - next_recommended_tools for follow-up
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
            return {
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": "symbol or ts_code is required",
                }
            }

        raw_symbol = ts_code or symbol
        if ctx:
            await ctx.info(
                f"🔧 获取主营业务构成: {raw_symbol}", extra={"symbol": raw_symbol}
            )

        try:
            logger.info("MCP tool called: get_mainbz_info", symbol=raw_symbol)

            data = await fundamental_use_cases.get_mainbz_info(raw_symbol)
            rows = data.get("rows", [])
            if not isinstance(rows, list):
                rows = []
            rows = [r for r in rows if isinstance(r, dict)]
            ts_code = data.get("ts_code") or await _resolve_ts_code(raw_symbol)
            upstream_status = str(data.get("status") or "").strip().lower()
            coverage = data.get("coverage") if isinstance(data, dict) else {}

            if not rows:
                no_data_reason = (
                    str(data.get("no_data_reason") or "").strip()
                    or "tushare.fina_mainbz returned empty rows"
                )
                summary_text = (
                    f"{ts_code} 主营构成暂无数据 (NO_DATA): {no_data_reason}. "
                    "建议补证：改用财务/股东结构化工具，并用公告或网页检索补主营结构叙事。"
                )
                empty_artifact = create_artifact_envelope(
                    component_type="table",
                    name=f"Main Business Composition (No Data): {ts_code}",
                    content={
                        "ts_code": ts_code,
                        "status": "no_data",
                        "reason": no_data_reason,
                        "source": "fina_mainbz",
                        "rows": [],
                        "coverage": coverage
                        if isinstance(coverage, dict)
                        else {
                            "row_count": 0,
                            "dimensions_found": [],
                            "expected_dimensions": ["P", "D", "I"],
                            "latest_period": None,
                        },
                        "source_type_required_next": [
                            "structured_financial",
                            "filings_or_web_news",
                        ],
                    },
                    description=summary_text,
                    metadata={
                        "type": "main_business",
                        "ts_code": ts_code,
                        "source": "fina_mainbz",
                        "result_status": "no_data",
                        "upstream_status": upstream_status or "unknown",
                    },
                    visible_to_llm=True,
                    display_in_report=True,
                )
                response: Dict[str, Any] = {
                    **create_artifact_response(summary=summary_text, artifact=empty_artifact)
                }
                response["result_status"] = "no_data"
                response["no_data_reason"] = no_data_reason
                response["scope"] = {
                    "tool": "get_mainbz_info",
                    "ts_code": ts_code,
                    "source": "fina_mainbz",
                    "reason": no_data_reason,
                }
                response["retriable"] = False
                response["next_recommended_tools"] = [
                    "get_financial_reports",
                    "get_shareholder_info",
                    "get_dividend_info",
                ]
                response["reroute_if_blocked"] = (
                    "If main business mix is required, route to filings/web search evidence."
                )
                if ctx:
                    await ctx.warning(
                        f"⚠️ 主营业务构成无数据: {ts_code}",
                        extra={"reason": no_data_reason},
                    )
                return response

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

                periods = [
                    {"date": d, "data": v}
                    for d, v in sorted(
                        periods_map.items(), key=lambda x: x[0], reverse=True
                    )
                ]
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

            dimensions = [a.get("metadata", {}).get("dimension") for a in artifacts]
            dimension_name_map = {"P": "产品", "D": "地区", "I": "行业"}
            dimension_labels = [
                dimension_name_map.get(d, str(d)) for d in dimensions if d
            ]
            latest_periods = []
            for a in artifacts:
                default_date = (
                    (a.get("content") or {}).get("default_date")
                    if isinstance(a.get("content"), dict)
                    else None
                )
                if default_date:
                    latest_periods.append(str(default_date))
            latest_period = max(latest_periods) if latest_periods else "N/A"
            summary_text = (
                f"{ts_code} 主营构成: 维度{len(artifacts)}个"
                f"({','.join(dimension_labels) or 'N/A'}), "
                f"最新报告期{latest_period}"
            )

            if ctx:
                await ctx.info(
                    f"✅ 主营业务构成获取完成: {ts_code}", extra={"rows": len(rows)}
                )

            return create_artifact_list_response(
                summary=summary_text, artifacts=artifacts
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
                    f"❌ 获取主营业务构成失败: {symbol}", extra={"error": str(e)}
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
            await ctx.info(
                f"🔧 获取股东信息: {raw_symbol}", extra={"symbol": raw_symbol}
            )

        try:
            logger.info("MCP tool called: get_shareholder_info", symbol=raw_symbol)

            data = await fundamental_use_cases.get_shareholder_info(raw_symbol)
            holder_data = data.get("data", {})
            ts_code = data.get("ts_code") or await _resolve_ts_code(raw_symbol)

            def _latest_by_date(
                rows: list[dict], date_key: str
            ) -> tuple[str, list[dict]]:
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
                    mapped.append(
                        {
                            "holder_name": r.get("holder_name"),
                            "hold_amount": r.get("hold_amount"),
                            "hold_ratio": _fmt_pct(r.get("hold_ratio")),
                            "end_date": _parse_date(r.get("end_date")),
                            "ann_date": _parse_date(r.get("ann_date")),
                        }
                    )
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
                                {
                                    "key": "hold_amount",
                                    "label": "持股数量",
                                    "align": "right",
                                    "value_meta": {"type": "compact"},
                                },
                                {
                                    "key": "hold_ratio",
                                    "label": "持股比例",
                                    "align": "right",
                                },
                                {"key": "end_date", "label": "报告期"},
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Top 10 shareholders for {ts_code}",
                        metadata={
                            "type": "table",
                            "ts_code": ts_code,
                            "dataset": "top10_holders",
                        },
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            float10 = holder_data.get("top10_floatholders", [])
            if float10:
                latest_date, rows = _latest_by_date(float10, "end_date")
                mapped = []
                for r in rows:
                    mapped.append(
                        {
                            "holder_name": r.get("holder_name"),
                            "hold_amount": r.get("hold_amount"),
                            "hold_float_ratio": _fmt_pct(
                                r.get("hold_float_ratio") or r.get("hold_ratio")
                            ),
                            "end_date": _parse_date(r.get("end_date")),
                            "ann_date": _parse_date(r.get("ann_date")),
                        }
                    )
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
                                {
                                    "key": "hold_amount",
                                    "label": "持股数量",
                                    "align": "right",
                                    "value_meta": {"type": "compact"},
                                },
                                {
                                    "key": "hold_float_ratio",
                                    "label": "占流通股本比例",
                                    "align": "right",
                                },
                                {"key": "end_date", "label": "报告期"},
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Top 10 tradable shareholders for {ts_code}",
                        metadata={
                            "type": "table",
                            "ts_code": ts_code,
                            "dataset": "top10_floatholders",
                        },
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            holder_num = holder_data.get("holder_number", [])
            if holder_num:
                trend = []
                for r in holder_num:
                    trend.append(
                        {
                            "end_date": _parse_date(
                                r.get("end_date") or r.get("ann_date")
                            ),
                            "holder_num": r.get("holder_num"),
                        }
                    )
                artifacts.append(
                    create_artifact_envelope(
                        component_type="holder_number_trend",
                        name=f"股东户数变化趋势: {ts_code}",
                        content={"ts_code": ts_code, "holder_number_trend": trend},
                        description=f"Shareholder count trend chart (3 years) for {ts_code}",
                        metadata={
                            "type": "holder_number_trend",
                            "ts_code": ts_code,
                            "dataset": "holder_number_trend",
                        },
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
                    mapped.append(
                        {
                            "holder_name": r.get("holder_name"),
                            "holder_type": r.get("holder_type"),
                            "in_de": r.get("in_de"),
                            "change_vol": r.get("change_vol"),
                            "avg_price": r.get("avg_price"),
                            "ann_date": _parse_date(r.get("ann_date")),
                        }
                    )
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
                                {
                                    "key": "change_vol",
                                    "label": "变动数量",
                                    "align": "right",
                                    "value_meta": {
                                        "type": "compact",
                                        "signDisplay": "exceptZero",
                                    },
                                },
                                {
                                    "key": "avg_price",
                                    "label": "均价",
                                    "align": "right",
                                    "value_meta": {"type": "number"},
                                },
                                {"key": "ann_date", "label": "公告期"},
                            ],
                            "rows": mapped,
                        },
                        description=f"Shareholder trading records (1 year) for {ts_code}",
                        metadata={
                            "type": "table",
                            "ts_code": ts_code,
                            "dataset": "holder_trade",
                        },
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            top10_count = len(top10) if isinstance(top10, list) else 0
            float10_count = len(float10) if isinstance(float10, list) else 0
            holder_num_latest = None
            holder_num_prev = None
            if holder_num:
                sorted_holder_num = sorted(
                    holder_num, key=lambda x: str(x.get("end_date") or x.get("ann_date") or "")
                )
                if sorted_holder_num:
                    holder_num_latest = sorted_holder_num[-1].get("holder_num")
                if len(sorted_holder_num) >= 2:
                    holder_num_prev = sorted_holder_num[-2].get("holder_num")
            holder_trade_count = len(holder_trade) if isinstance(holder_trade, list) else 0
            holder_delta_text = ""
            if isinstance(holder_num_latest, (int, float)) and isinstance(holder_num_prev, (int, float)):
                delta = holder_num_latest - holder_num_prev
                holder_delta_text = f", 户数环比{delta:+.0f}"
            summary_text = (
                f"{ts_code} 股东结构: 前十股东记录{top10_count}条, "
                f"前十流通股东记录{float10_count}条, "
                f"近一年增减持记录{holder_trade_count}条"
                f"{holder_delta_text}"
            )

            if ctx:
                await ctx.info(f"✅ 股东信息获取完成: {ts_code}")

            return create_artifact_list_response(
                summary=summary_text, artifacts=artifacts
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
                    f"❌ 获取股东信息失败: {symbol}", extra={"error": str(e)}
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

            def _normalize_end_date(value: Any) -> str:
                if value is None:
                    return ""
                text = str(value).strip().replace("-", "").replace("/", "")
                return text[:8] if len(text) >= 8 else ""

            def _to_float(v: Any) -> float | None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            rows_sorted = sorted(
                rows, key=lambda r: _normalize_end_date(r.get("end_date")), reverse=True
            )
            latest_row = rows_sorted[0] if rows_sorted else {}
            latest_end_date = _normalize_end_date(latest_row.get("end_date"))
            latest_end_date = (
                f"{latest_end_date[:4]}-{latest_end_date[4:6]}-{latest_end_date[6:8]}"
                if len(latest_end_date) == 8
                else "N/A"
            )

            latest_cash_div_pre_tax = _to_float(latest_row.get("cash_div_tax"))
            latest_cash_div_after_tax = _to_float(latest_row.get("cash_div"))

            implemented = 0
            total_cash_div_pre_tax = 0.0
            total_cash_div_count = 0
            for row in rows:
                div_proc = str(row.get("div_proc") or "")
                if "实施" in div_proc or "完成" in div_proc:
                    implemented += 1
                div_val = _to_float(row.get("cash_div_tax"))
                if div_val is not None:
                    total_cash_div_pre_tax += div_val
                    total_cash_div_count += 1

            summary_text = (
                f"{ts_code} 分红送股({len(rows)}期): 最近{latest_end_date}税前/税后每股"
                f"{f'{latest_cash_div_pre_tax:.3f}' if latest_cash_div_pre_tax is not None else 'N/A'}"
                f"/{f'{latest_cash_div_after_tax:.3f}' if latest_cash_div_after_tax is not None else 'N/A'}, "
                f"已实施{implemented}/{len(rows)}期, "
                f"税前累计每股分红"
                f"{f'{total_cash_div_pre_tax:.3f}' if total_cash_div_count > 0 else 'N/A'}"
            )

            if ctx:
                await ctx.info(
                    f"✅ 分红送股信息获取完成: {ts_code}", extra={"rows": len(rows)}
                )

            return create_artifact_response(
                summary=summary_text, artifact=table_artifact
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
                    f"❌ 获取分红送股信息失败: {symbol}", extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_forecast_info(
        symbol: str | None = None,
        ts_code: str | None = None,
        limit: int = 50,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get performance forecast info for the given A-share ticker."""
        if not symbol and not ts_code:
            return {"error": "symbol or ts_code is required"}

        raw_symbol = ts_code or symbol
        if ctx:
            await ctx.info(
                f"🔧 获取业绩预告: {raw_symbol}",
                extra={"symbol": raw_symbol, "limit": limit},
            )

        try:
            logger.info(
                "MCP tool called: get_forecast_info",
                symbol=raw_symbol,
                limit=limit,
            )

            data = await fundamental_use_cases.get_forecast_info(raw_symbol, limit=limit)
            rows = data.get("rows", [])
            ts_code_value = data.get("ts_code") or await _resolve_ts_code(raw_symbol)

            columns = [
                {"key": "ts_code", "label": "代码"},
                {"key": "ann_date", "label": "公告日期"},
                {"key": "end_date", "label": "报告期"},
                {"key": "type", "label": "预告类型"},
                {"key": "p_change_min", "label": "预告净利润变动幅度下限%", "align": "right"},
                {"key": "p_change_max", "label": "预告净利润变动幅度上限%", "align": "right"},
                {"key": "net_profit_min", "label": "预告净利润下限(万)", "align": "right"},
                {"key": "net_profit_max", "label": "预告净利润上限(万)", "align": "right"},
                {"key": "last_parent_net", "label": "上年同期净利润", "align": "right"},
                {"key": "summary", "label": "业绩预告摘要"},
                {"key": "change_reason", "label": "业绩变动原因"},
            ]

            table_artifact = create_artifact_envelope(
                component_type="table",
                name=f"Performance Forecast: {ts_code_value}",
                content={
                    "title": "业绩预告",
                    "tag": ts_code_value,
                    "columns": columns,
                    "rows": rows,
                },
                description=f"Performance forecast for {ts_code_value}",
                metadata={
                    "type": "table",
                    "ts_code": ts_code_value,
                    "dataset": "forecast",
                },
                visible_to_llm=False,
                display_in_report=True,
            )

            latest = rows[0] if rows else {}
            latest_ann_date = latest.get("ann_date") or "N/A"
            latest_type = latest.get("type") or "N/A"
            summary_text = (
                f"{ts_code_value} 业绩预告共{len(rows)}条, 最近公告日{latest_ann_date}, "
                f"最新预告类型={latest_type}"
            )

            if ctx:
                await ctx.info(
                    f"✅ 业绩预告获取完成: {ts_code_value}",
                    extra={"rows": len(rows)},
                )

            return create_artifact_response(
                summary=summary_text,
                artifact=table_artifact,
            )
        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {raw_symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="table", name=f"{raw_symbol} 业绩预告"
            )
        except Exception as e:
            logger.error(f"Get forecast info failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取业绩预告失败: {raw_symbol}",
                    extra={"error": str(e)},
                )
            return {"error": str(e)}

    @mcp.tool(tags={"fundamental"})
    async def get_valuation_metrics(
        symbol: str, days: int = 250, ctx: Context = None
    ) -> Dict[str, Any]:
        """Get valuation metrics with historical percentile ranking.

        Fetches PE/PB/PS and other valuation indicators for the given stock,
        calculates where the current value sits in its historical range
        (percentile), and provides an overall valuation level assessment.

        Args:
            symbol: Asset ticker. Format: EXCHANGE:SYMBOL
                - A股: SSE:600519 (上交所), SZSE:000001 (深交所)
            days: Number of trading days for historical percentile
                  calculation (default 250, ~1 year)

        Returns:
            Artifact response containing:
            - Current PE/PB/PS values and their historical percentiles
            - Historical time series for charting
            - Valuation level assessment (低估/偏低/适中/偏高/高估)
        """
        if ctx:
            await ctx.info(
                f"🔧 获取估值指标: {symbol}",
                extra={"symbol": symbol, "days": days},
            )

        try:
            logger.info(
                "MCP tool called: get_valuation_metrics",
                symbol=symbol,
                days=days,
            )

            data = await fundamental_use_cases.get_valuation_metrics(
                symbol, days=days
            )

            if data.get("error"):
                return {"error": data["error"]}

            ts_code = data.get("symbol") or await _resolve_ts_code(symbol)
            metrics = data.get("metrics", {})
            summary_info = data.get("summary", {})
            level = summary_info.get("valuation_level", "未知")
            pe_pct = summary_info.get("pe_ttm_percentile")
            pb_pct = summary_info.get("pb_percentile")

            # Build summary text for LLM
            pe_ttm = metrics.get("pe_ttm", {})
            pb = metrics.get("pb", {})
            ps_ttm = metrics.get("ps_ttm", {})

            lines = [f"{ts_code} 估值指标 (近{days}个交易日):"]
            if pe_ttm.get("current") is not None:
                lines.append(
                    f"- PE(TTM): {pe_ttm['current']:.2f}" f" (百分位 {pe_pct}%)"
                    if pe_pct is not None
                    else f"- PE(TTM): {pe_ttm['current']:.2f}"
                )
            if pb.get("current") is not None:
                lines.append(
                    f"- PB: {pb['current']:.2f}" f" (百分位 {pb_pct}%)"
                    if pb_pct is not None
                    else f"- PB: {pb['current']:.2f}"
                )
            if ps_ttm.get("current") is not None:
                lines.append(f"- PS(TTM): {ps_ttm['current']:.2f}")
            lines.append(f"- 估值水平: {level}")

            market_cap = data.get("market_cap", {})
            total_mv = market_cap.get("total_mv")
            if total_mv is not None:
                lines.append(f"- 总市值: {total_mv / 10000:.2f}亿元")

            summary_text = "\n".join(lines)

            # Build artifacts
            artifacts = []

            # 1) Valuation metrics table artifact
            metric_rows = []
            label_map = {
                "pe": "PE (静态)",
                "pe_ttm": "PE (TTM)",
                "pb": "PB",
                "ps": "PS (静态)",
                "ps_ttm": "PS (TTM)",
                "dv_ratio": "股息率",
                "dv_ttm": "股息率(TTM)",
            }
            for key, label in label_map.items():
                m = metrics.get(key)
                if not m:
                    continue
                metric_rows.append(
                    {
                        "metric": label,
                        "current": m.get("current"),
                        "percentile": (
                            f"{m['percentile']}%"
                            if m.get("percentile") is not None
                            else None
                        ),
                        "min": m.get("min"),
                        "max": m.get("max"),
                        "mean": m.get("mean"),
                        "median": m.get("median"),
                    }
                )

            artifacts.append(
                create_artifact_envelope(
                    component_type="table",
                    name=f"Valuation Metrics: {ts_code}",
                    content={
                        "title": "估值指标",
                        "tag": ts_code,
                        "valuation_level": level,
                        "columns": [
                            {"key": "metric", "label": "指标"},
                            {"key": "current", "label": "当前值", "align": "right"},
                            {
                                "key": "percentile",
                                "label": "历史百分位",
                                "align": "right",
                            },
                            {"key": "min", "label": "最小值", "align": "right"},
                            {"key": "max", "label": "最大值", "align": "right"},
                            {"key": "mean", "label": "均值", "align": "right"},
                            {"key": "median", "label": "中位数", "align": "right"},
                        ],
                        "rows": metric_rows,
                    },
                    description=f"Valuation metrics with percentile ranking for {ts_code}",
                    metadata={
                        "type": "table",
                        "ts_code": ts_code,
                        "dataset": "valuation_metrics",
                    },
                    visible_to_llm=False,
                    display_in_report=True,
                )
            )

            # 2) Valuation history chart artifact
            history = data.get("history", {})
            if history.get("dates"):
                artifacts.append(
                    create_artifact_envelope(
                        component_type="valuation_chart",
                        name=f"Valuation History: {ts_code}",
                        content={
                            "title": "估值历史走势",
                            "ts_code": ts_code,
                            "dates": history.get("dates", []),
                            "pe_ttm": history.get("pe_ttm", []),
                            "pb": history.get("pb", []),
                            "ps_ttm": history.get("ps_ttm", []),
                        },
                        description=(
                            f"Historical PE(TTM)/PB/PS(TTM) chart for {ts_code} "
                            f"({days} trading days)"
                        ),
                        metadata={
                            "type": "valuation_chart",
                            "ts_code": ts_code,
                        },
                        visible_to_llm=False,
                        display_in_report=True,
                    )
                )

            if ctx:
                await ctx.info(
                    f"✅ 估值指标获取完成: {ts_code}",
                    extra={"level": level},
                )

            return create_artifact_list_response(
                summary=summary_text,
                artifacts=artifacts,
            )

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e,
                component_type="valuation_metrics",
                name=f"{symbol} 估值指标",
            )
        except Exception as e:
            logger.error(f"Get valuation metrics failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取估值指标失败: {symbol}",
                    extra={"error": str(e)},
                )
            return {"error": str(e)}
