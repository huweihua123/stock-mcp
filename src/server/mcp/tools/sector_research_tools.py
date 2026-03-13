# src/server/mcp/tools/sector_research_tools.py
"""MCP tools for sector research workflow.

Tools:
- resolve_sector_scope
- build_sector_universe
- build_peer_benchmark_table
- build_sector_evidence_pack
- build_sector_structure_snapshot
- quality_gate_sector_report
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP, Context

from src.server.core.dependencies import Container
from src.server.core.use_cases import market as market_use_cases
from src.server.core.use_cases import money_flow as money_flow_use_cases
from src.server.core.use_cases import fundamental as fundamental_use_cases
from src.server.core.use_cases import technical as technical_use_cases
from src.server.core.use_cases import filings as filings_use_cases
from src.server.mcp.tools.artifact_utils import (
    ComponentType,
    create_artifact_envelope,
    create_artifact_response,
)
from src.server.utils.logger import logger


def _is_cn_text(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _detect_market(market: str, sector_name: str, symbols: Optional[List[str]]) -> str:
    m = (market or "auto").lower().strip()
    if m in {"cn", "a", "ashare", "a-share"}:
        return "CN"
    if m in {"us", "usa"}:
        return "US"
    # auto
    if symbols:
        first = str(symbols[0]).upper()
        if first.startswith(("SSE:", "SZSE:", "BSE:")):
            return "CN"
        if first.startswith(("NASDAQ:", "NYSE:", "AMEX:")):
            return "US"
    if _is_cn_text(sector_name):
        return "CN"
    return "US"


def _to_internal_from_ts(ts_code: str) -> Optional[str]:
    val = (ts_code or "").strip().upper()
    if "." not in val:
        return None
    code, ex = val.split(".", 1)
    if ex == "SH":
        return f"SSE:{code}"
    if ex == "SZ":
        return f"SZSE:{code}"
    if ex == "BJ":
        return f"BSE:{code}"
    return None


def _pick_numeric(data: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        val = data.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _fmt_billions(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    return f"{v:.0f}"


def _count_numeric_facts(text: str) -> int:
    if not text:
        return 0
    # capture numbers like 12, 12.5, 12%, 12.5x, 1,234
    pattern = r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:%|x|倍)?\b"
    return len(re.findall(pattern, text))


def _contains_required_sections(text: str) -> Dict[str, bool]:
    required = [
        "## 执行摘要",
        "## 风险与失效条件",
        "## 结论与下一步",
    ]
    return {sec: (sec in text) for sec in required}


async def _resolve_symbols(symbols: List[str]) -> List[str]:
    gateway = Container.market_gateway()
    resolved: List[str] = []
    for raw in symbols:
        try:
            ticker = await gateway.resolve_ticker(raw)
            resolved.append(ticker)
        except Exception:
            continue
    # stable dedupe
    dedup: List[str] = []
    seen = set()
    for s in resolved:
        if s not in seen:
            dedup.append(s)
            seen.add(s)
    return dedup


async def _build_cn_universe_from_tushare(
    sector_name: str,
    max_companies: int = 10,
) -> Dict[str, Any]:
    manager = Container.adapter_manager()
    tushare = manager.get_adapter_by_provider("tushare")
    if not tushare:
        return {
            "universe": [],
            "source": "manual",
            "note": "tushare adapter unavailable, please provide symbols explicitly",
        }

    client = getattr(tushare, "tushare_conn", None)
    if not client or client.get_client() is None:
        return {
            "universe": [],
            "source": "manual",
            "note": "tushare client unavailable, please provide symbols explicitly",
        }

    if not hasattr(tushare, "_resolve_sector_index"):
        return {
            "universe": [],
            "source": "manual",
            "note": "sector resolver unavailable, please provide symbols explicitly",
        }

    index_code, index_name, candidates = await tushare._resolve_sector_index(sector_name)
    if not index_code:
        return {
            "universe": [],
            "source": "manual",
            "candidates": candidates or [],
            "note": "sector is ambiguous or not found",
        }

    member_df = await tushare._run(
        tushare.tushare_conn.get_client().ths_member,
        ts_code=index_code,
    )
    if member_df is None or member_df.empty:
        return {
            "universe": [],
            "source": "manual",
            "index_code": index_code,
            "index_name": index_name,
            "note": "empty member list",
        }

    universe: List[Dict[str, Any]] = []
    for _, row in member_df.head(max_companies).iterrows():
        ts_code = str(row.get("con_code") or row.get("ts_code") or "").strip()
        ticker = _to_internal_from_ts(ts_code)
        if not ticker:
            continue
        universe.append(
            {
                "ticker": ticker,
                "company_name": row.get("con_name") or row.get("name") or "",
                "sector_name": index_name or sector_name,
                "index_code": index_code,
            }
        )

    return {
        "universe": universe,
        "source": "tushare",
        "index_code": index_code,
        "index_name": index_name or sector_name,
    }


async def _build_peer_rows(symbols: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ticker in symbols:
        asset = await market_use_cases.get_asset_info(ticker) or {}
        name = asset.get("name") or ticker

        market = "US" if ticker.startswith(("NASDAQ:", "NYSE:", "AMEX:")) else "CN"
        pe = pb = ev_ebitda = mcap = None
        revenue_growth = ebitda_margin = None

        if market == "US":
            try:
                val = await fundamental_use_cases.get_us_valuation_metrics(ticker)
                pe = _pick_numeric(val, ["pe_ttm", "pe", "forward_pe"])
                pb = _pick_numeric(val, ["pb", "price_to_book"])
                ev_ebitda = _pick_numeric(val, ["ev_ebitda"])
                mcap = _pick_numeric(val, ["market_cap"])
                revenue_growth = _pick_numeric(val, ["revenue_growth", "sales_growth"])
                ebitda_margin = _pick_numeric(val, ["ebitda_margin"])
            except Exception:
                pass
        else:
            try:
                val = await fundamental_use_cases.get_valuation_metrics(ticker, days=250)
                pe = _pick_numeric(val, ["pe_ttm", "pe", "current_pe"])
                pb = _pick_numeric(val, ["pb", "current_pb"])
                ev_ebitda = _pick_numeric(val, ["ev_ebitda"])
                mcap = _pick_numeric(val, ["market_cap", "total_mv"])
                revenue_growth = _pick_numeric(val, ["revenue_growth", "growth"])
                ebitda_margin = _pick_numeric(val, ["ebitda_margin"])
            except Exception:
                pass

        rows.append(
            {
                "ticker": ticker,
                "company_name": name,
                "market": market,
                "pe": pe,
                "pb": pb,
                "ev_ebitda": ev_ebitda,
                "market_cap": mcap,
                "revenue_growth": revenue_growth,
                "ebitda_margin": ebitda_margin,
            }
        )

    rows.sort(
        key=lambda x: (x.get("market_cap") is not None, x.get("market_cap") or 0),
        reverse=True,
    )
    return rows


def _infer_value_chain_stage(text: str) -> Tuple[str, str, List[str]]:
    """Infer value-chain stage from merged company/segment text."""
    source = (text or "").lower()
    keyword_map = {
        "上游": [
            "上游",
            "原材料",
            "矿",
            "采掘",
            "设备",
            "晶圆",
            "wafer",
            "materials",
            "equipment",
        ],
        "中游": [
            "中游",
            "制造",
            "生产",
            "封测",
            "模组",
            "组装",
            "manufacturing",
            "assembly",
            "module",
        ],
        "下游": [
            "下游",
            "渠道",
            "零售",
            "消费",
            "终端",
            "应用",
            "服务",
            "distribution",
            "retail",
            "application",
            "service",
        ],
    }

    scores: Dict[str, int] = {k: 0 for k in keyword_map}
    matched: Dict[str, List[str]] = {k: [] for k in keyword_map}
    for stage, keywords in keyword_map.items():
        for kw in keywords:
            if kw in source:
                scores[stage] += 1
                matched[stage].append(kw)

    best_stage = max(scores, key=lambda s: scores[s])
    best_score = scores[best_stage]
    if best_score <= 0:
        return "待判定", "low", []

    ranked = sorted(scores.values(), reverse=True)
    gap = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
    confidence = "high" if best_score >= 3 and gap >= 2 else ("medium" if best_score >= 2 else "low")
    return best_stage, confidence, matched[best_stage]


def register_sector_research_tools(mcp: FastMCP):
    """Register sector research tools."""

    @mcp.tool(tags={"sector-research", "research"})
    async def resolve_sector_scope(
        sector_name: str,
        market: str = "auto",
        depth: str = "standard",
        horizon_days: int = 365,
        peer_count: int = 10,
        angle: str = "neutral",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Normalize sector research scope for downstream MCP calls."""
        detected_market = _detect_market(market, sector_name, None)
        safe_peer_count = max(5, min(int(peer_count), 30))
        safe_horizon = max(30, min(int(horizon_days), 3650))

        scope = {
            "sector_name": sector_name,
            "market": detected_market,
            "depth": depth,
            "horizon_days": safe_horizon,
            "peer_count": safe_peer_count,
            "angle": angle,
            "as_of_date": datetime.utcnow().strftime("%Y-%m-%d"),
        }

        recommended_tools = (
            [
                "build_sector_universe",
                "build_peer_benchmark_table",
                "build_sector_evidence_pack",
            ]
            if detected_market == "US"
            else [
                "build_sector_universe",
                "get_sector_trend",
                "get_sector_valuation_metrics",
                "build_peer_benchmark_table",
                "build_sector_evidence_pack",
            ]
        )

        summary = (
            f"研究范围已规范化: {sector_name} ({detected_market}), "
            f"深度={depth}, 同业样本={safe_peer_count}, 观察窗={safe_horizon}天"
        )
        artifact = create_artifact_envelope(
            component_type=ComponentType.PLAN_TASK,
            name=f"Sector Scope: {sector_name}",
            content={"scope": scope, "recommended_tools": recommended_tools},
            description=summary,
            visible_to_llm=True,
            display_in_report=False,
        )
        if ctx:
            await ctx.info("✅ Sector scope resolved", extra=scope)
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def build_sector_universe(
        sector_name: str,
        market: str = "auto",
        symbols: List[str] = None,
        max_companies: int = 10,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build a research universe for a sector.

        For CN market, best-effort auto constituents retrieval is attempted via
        Tushare (ths_index + ths_member). For US market, pass symbols explicitly.
        """
        symbols = symbols or []
        detected_market = _detect_market(market, sector_name, symbols)
        max_companies = max(5, min(int(max_companies), 50))

        if symbols:
            resolved = await _resolve_symbols(symbols)
            universe = []
            for ticker in resolved[:max_companies]:
                asset = await market_use_cases.get_asset_info(ticker) or {}
                universe.append(
                    {
                        "ticker": ticker,
                        "company_name": asset.get("name", ""),
                        "market": "US"
                        if ticker.startswith(("NASDAQ:", "NYSE:", "AMEX:"))
                        else "CN",
                    }
                )
            source = "manual"
            extra = {}
        elif detected_market == "CN":
            cn = await _build_cn_universe_from_tushare(
                sector_name=sector_name,
                max_companies=max_companies,
            )
            universe = cn.get("universe", [])
            source = cn.get("source", "manual")
            extra = {
                "index_code": cn.get("index_code"),
                "index_name": cn.get("index_name"),
                "candidates": cn.get("candidates", []),
                "note": cn.get("note"),
            }
        else:
            universe = []
            source = "manual"
            extra = {
                "note": "US market requires explicit symbols currently",
            }

        summary = (
            f"{sector_name} 样本池构建完成: {len(universe)}家公司, "
            f"market={detected_market}, source={source}"
        )
        artifact = create_artifact_envelope(
            component_type=ComponentType.TABLE,
            name=f"Sector Universe: {sector_name}",
            content={
                "sector_name": sector_name,
                "market": detected_market,
                "source": source,
                "universe": universe,
                **extra,
            },
            description=summary,
            visible_to_llm=True,
            display_in_report=True,
        )
        if ctx:
            await ctx.info(
                "✅ Sector universe built",
                extra={
                    "sector_name": sector_name,
                    "market": detected_market,
                    "count": len(universe),
                    "source": source,
                },
            )
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def build_peer_benchmark_table(
        sector_name: str,
        symbols: List[str],
        market: str = "auto",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build peer benchmark table for a list of companies."""
        if not symbols:
            summary = "构建同业对比失败: symbols 不能为空"
            artifact = create_artifact_envelope(
                component_type=ComponentType.TABLE,
                name=f"Peer Benchmark: {sector_name}",
                content={"error": "symbols is empty", "rows": []},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        resolved = await _resolve_symbols(symbols)
        rows = await _build_peer_rows(resolved)
        detected_market = _detect_market(market, sector_name, resolved)

        summary = (
            f"{sector_name} 同业对比完成: {len(rows)}家公司, "
            f"market={detected_market}, "
            f"Top市值={rows[0]['ticker'] if rows else 'N/A'} "
            f"({_fmt_billions(rows[0].get('market_cap')) if rows else 'N/A'})"
        )

        artifact = create_artifact_envelope(
            component_type=ComponentType.TABLE,
            name=f"Peer Benchmark: {sector_name}",
            content={
                "sector_name": sector_name,
                "market": detected_market,
                "rows": rows,
            },
            description=summary,
            visible_to_llm=True,
            display_in_report=True,
        )
        if ctx:
            await ctx.info(
                "✅ Peer benchmark built",
                extra={"sector_name": sector_name, "count": len(rows)},
            )
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def classify_value_chain(
        sector_name: str,
        symbols: List[str],
        market: str = "auto",
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Classify sector peers into upstream/midstream/downstream buckets."""
        if not symbols:
            summary = "价值链分类失败: symbols 不能为空"
            artifact = create_artifact_envelope(
                component_type=ComponentType.TABLE,
                name=f"Value Chain: {sector_name}",
                content={"error": "symbols is empty", "rows": [], "stage_counts": {}},
                description=summary,
                visible_to_llm=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)

        resolved = await _resolve_symbols(symbols)
        detected_market = _detect_market(market, sector_name, resolved)

        rows: List[Dict[str, Any]] = []
        stage_counts: Dict[str, int] = {"上游": 0, "中游": 0, "下游": 0, "待判定": 0}
        for ticker in resolved:
            asset = await market_use_cases.get_asset_info(ticker) or {}
            name = asset.get("name") or ticker
            company_market = (
                "US" if ticker.startswith(("NASDAQ:", "NYSE:", "AMEX:")) else "CN"
            )

            segment_lines: List[str] = []
            if company_market == "CN":
                try:
                    mainbz = await fundamental_use_cases.get_mainbz_info(ticker) or {}
                    bz_rows = mainbz.get("rows") if isinstance(mainbz, dict) else []
                    if isinstance(bz_rows, list) and bz_rows:
                        sorted_rows = sorted(
                            [r for r in bz_rows if isinstance(r, dict)],
                            key=lambda r: _pick_numeric(r, ["bz_sales", "bz_profit"]) or 0,
                            reverse=True,
                        )
                        for rec in sorted_rows[:5]:
                            item = str(rec.get("bz_item") or rec.get("bz_name") or "").strip()
                            if item:
                                segment_lines.append(item)
                except Exception:
                    segment_lines = []

            text_blob = " ".join([sector_name, str(name), *segment_lines])
            stage, confidence, matched_keywords = _infer_value_chain_stage(text_blob)
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

            rows.append(
                {
                    "ticker": ticker,
                    "company_name": name,
                    "market": company_market,
                    "value_chain_stage": stage,
                    "confidence": confidence,
                    "evidence_keywords": matched_keywords[:5],
                    "business_segments": segment_lines[:5],
                }
            )

        summary = (
            f"{sector_name} 价值链分类完成: 共{len(rows)}家公司, "
            f"上游/中游/下游/待判定="
            f"{stage_counts.get('上游', 0)}/{stage_counts.get('中游', 0)}/"
            f"{stage_counts.get('下游', 0)}/{stage_counts.get('待判定', 0)}"
        )
        artifact = create_artifact_envelope(
            component_type=ComponentType.TABLE,
            name=f"Value Chain: {sector_name}",
            content={
                "sector_name": sector_name,
                "market": detected_market,
                "rows": rows,
                "stage_counts": stage_counts,
            },
            description=summary,
            visible_to_llm=True,
            display_in_report=True,
        )
        if ctx:
            await ctx.info(
                "✅ Value chain classification built",
                extra={
                    "sector_name": sector_name,
                    "market": detected_market,
                    "count": len(rows),
                },
            )
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def build_sector_evidence_pack(
        sector_name: str,
        market: str = "auto",
        symbols: List[str] = None,
        days: int = 120,
        include_filings: bool = True,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build structured evidence pack for sector-overview skill."""
        symbols = symbols or []
        detected_market = _detect_market(market, sector_name, symbols)
        safe_days = max(30, min(int(days), 750))

        if symbols:
            universe = await _resolve_symbols(symbols)
        elif detected_market == "CN":
            cn = await _build_cn_universe_from_tushare(sector_name, max_companies=10)
            universe = [i["ticker"] for i in cn.get("universe", [])]
        else:
            universe = []

        # 1) sector snapshot
        sector_snapshot: Dict[str, Any] = {"market": detected_market}
        if detected_market == "CN":
            try:
                sector_snapshot["trend"] = await money_flow_use_cases.get_sector_trend(
                    sector_name, days=min(safe_days, 90)
                )
            except Exception as e:
                sector_snapshot["trend_error"] = str(e)
            try:
                sector_snapshot[
                    "valuation"
                ] = await money_flow_use_cases.get_sector_valuation_metrics(
                    sector_name=sector_name,
                    days=min(safe_days, 250),
                    sample_size=max(20, len(universe) or 20),
                )
            except Exception as e:
                sector_snapshot["valuation_error"] = str(e)
        else:
            try:
                sector_snapshot[
                    "etf"
                ] = await technical_use_cases.get_us_sector_etf_analysis(
                    sector_name=sector_name,
                    days=min(safe_days, 120),
                )
            except Exception as e:
                sector_snapshot["etf_error"] = str(e)

        # 2) peer rows
        peer_rows = await _build_peer_rows(universe) if universe else []

        # 3) filings digest
        filings_digest: List[Dict[str, Any]] = []
        if include_filings and universe:
            for ticker in universe[:5]:
                item: Dict[str, Any] = {"ticker": ticker}
                try:
                    if detected_market == "US":
                        periodic = await filings_use_cases.fetch_periodic_sec_filings(
                            ticker=ticker,
                            forms=["10-K", "10-Q", "20-F"],
                            limit=3,
                        )
                        event = await filings_use_cases.fetch_event_sec_filings(
                            ticker=ticker,
                            forms=["8-K", "6-K"],
                            limit=3,
                        )
                        item["periodic_count"] = len(periodic or [])
                        item["event_count"] = len(event or [])
                        latest = None
                        for rec in (periodic or []) + (event or []):
                            fd = rec.get("filingDate") or rec.get("filing_date")
                            if fd and (latest is None or str(fd) > str(latest)):
                                latest = fd
                        item["latest_filing_date"] = latest
                    else:
                        ashare = await filings_use_cases.fetch_ashare_filings(
                            symbol=ticker,
                            filing_types=["annual", "semi-annual", "quarterly"],
                            limit=3,
                        )
                        item["filing_count"] = len(ashare or [])
                        latest = None
                        for rec in ashare or []:
                            fd = rec.get("filing_date") or rec.get("filingDate")
                            if fd and (latest is None or str(fd) > str(latest)):
                                latest = fd
                        item["latest_filing_date"] = latest
                except Exception as e:
                    item["error"] = str(e)
                filings_digest.append(item)

        evidence_pack = {
            "scope": {
                "sector_name": sector_name,
                "market": detected_market,
                "days": safe_days,
                "generated_at": datetime.utcnow().isoformat(),
            },
            "universe": universe,
            "sector_snapshot": sector_snapshot,
            "peer_benchmark": peer_rows,
            "filings_digest": filings_digest,
        }

        summary = (
            f"{sector_name} 证据包已生成: market={detected_market}, "
            f"universe={len(universe)}, peers={len(peer_rows)}, "
            f"filings={len(filings_digest)}"
        )
        artifact = create_artifact_envelope(
            component_type=ComponentType.RESEARCH_REPORTS,
            name=f"Sector Evidence Pack: {sector_name}",
            content=evidence_pack,
            description=summary,
            metadata={
                "sector_name": sector_name,
                "market": detected_market,
                "days": safe_days,
            },
            visible_to_llm=True,
            display_in_report=False,
        )
        if ctx:
            await ctx.info(
                "✅ Sector evidence pack built",
                extra={
                    "sector_name": sector_name,
                    "market": detected_market,
                    "universe": len(universe),
                    "peers": len(peer_rows),
                },
            )
        logger.info(
            "MCP tool called: build_sector_evidence_pack",
            sector_name=sector_name,
            market=detected_market,
            universe_count=len(universe),
            peer_count=len(peer_rows),
        )
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def build_sector_structure_snapshot(
        sector_name: str,
        market: str = "auto",
        days: int = 90,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build compact sector structure snapshot for decision framing.

        Snapshot dimensions:
        - price momentum
        - capital flow signal
        - valuation regime
        - aggregate structure score (-3 ~ +3)
        """
        detected_market = _detect_market(market, sector_name, None)
        safe_days = max(30, min(int(days), 365))

        snapshot: Dict[str, Any] = {
            "sector_name": sector_name,
            "market": detected_market,
            "days": safe_days,
            "price_momentum": None,
            "flow_signal": "unknown",
            "valuation_level": "unknown",
            "structure_score": 0,
        }
        evidence: Dict[str, Any] = {}

        score = 0
        if detected_market == "CN":
            try:
                trend = await money_flow_use_cases.get_sector_trend(
                    sector_name, days=min(safe_days, 90)
                )
                evidence["trend"] = trend
                total_pct = (
                    trend.get("total_pct_chg")
                    if isinstance(trend, dict)
                    else None
                )
                if isinstance(total_pct, (int, float)):
                    snapshot["price_momentum"] = float(total_pct)
                    score += 1 if total_pct > 0 else -1
            except Exception as e:
                evidence["trend_error"] = str(e)

            try:
                flow = await money_flow_use_cases.get_sector_money_flow_history(
                    sector_name, days=min(safe_days, 120)
                )
                evidence["flow"] = flow
                flow_trend = (
                    (flow.get("summary") or {}).get("trend")
                    if isinstance(flow, dict)
                    else None
                )
                if isinstance(flow_trend, str) and flow_trend:
                    snapshot["flow_signal"] = flow_trend
                    if "流入" in flow_trend:
                        score += 1
                    elif "流出" in flow_trend:
                        score -= 1
            except Exception as e:
                evidence["flow_error"] = str(e)

            try:
                val = await money_flow_use_cases.get_sector_valuation_metrics(
                    sector_name=sector_name,
                    days=min(safe_days, 250),
                    sample_size=60,
                )
                evidence["valuation"] = val
                level = (
                    (val.get("summary") or {}).get("valuation_level")
                    if isinstance(val, dict)
                    else None
                )
                if isinstance(level, str) and level:
                    snapshot["valuation_level"] = level
                    if level in {"低估", "偏低"}:
                        score += 1
                    elif level in {"高估", "偏高"}:
                        score -= 1
            except Exception as e:
                evidence["valuation_error"] = str(e)
        else:
            try:
                etf = await technical_use_cases.get_us_sector_etf_analysis(
                    sector_name=sector_name,
                    days=min(safe_days, 120),
                )
                evidence["etf"] = etf
                total_pct = (
                    etf.get("total_change_pct")
                    if isinstance(etf, dict)
                    else None
                )
                if isinstance(total_pct, (int, float)):
                    snapshot["price_momentum"] = float(total_pct)
                    score += 1 if total_pct > 0 else -1
            except Exception as e:
                evidence["etf_error"] = str(e)

            # US valuation/flow structured sector capability is limited for now.
            # Keep conservative defaults and rely on peer benchmark + filings.
            snapshot["flow_signal"] = "limited_data"
            snapshot["valuation_level"] = "limited_data"

        snapshot["structure_score"] = max(-3, min(3, score))

        summary = (
            f"{sector_name} 结构快照: score={snapshot['structure_score']}, "
            f"momentum={snapshot['price_momentum'] if snapshot['price_momentum'] is not None else 'N/A'}, "
            f"flow={snapshot['flow_signal']}, valuation={snapshot['valuation_level']}"
        )
        artifact = create_artifact_envelope(
            component_type=ComponentType.MARKET_LIQUIDITY,
            name=f"Sector Structure Snapshot: {sector_name}",
            content={"snapshot": snapshot, "evidence": evidence},
            description=summary,
            metadata={"sector_name": sector_name, "market": detected_market},
            visible_to_llm=True,
            display_in_report=True,
        )
        if ctx:
            await ctx.info(
                "✅ Sector structure snapshot built",
                extra={
                    "sector_name": sector_name,
                    "market": detected_market,
                    "score": snapshot["structure_score"],
                },
            )
        return create_artifact_response(summary=summary, artifact=artifact)

    @mcp.tool(tags={"sector-research", "research"})
    async def quality_gate_sector_report(
        report_markdown: str,
        evidence_pack: Dict[str, Any] = None,
        min_numeric_facts: int = 6,
        require_question: bool = True,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Run quality gate checks for sector-overview report draft."""
        evidence_pack = evidence_pack or {}
        min_numeric = max(3, min(int(min_numeric_facts), 30))

        section_checks = _contains_required_sections(report_markdown or "")
        numeric_count = _count_numeric_facts(report_markdown or "")
        has_question = ("?" in (report_markdown or "")) or ("？" in (report_markdown or ""))

        peer_rows = ((evidence_pack.get("peer_benchmark") or []) if isinstance(evidence_pack, dict) else [])
        filings_digest = ((evidence_pack.get("filings_digest") or []) if isinstance(evidence_pack, dict) else [])
        universe = ((evidence_pack.get("universe") or []) if isinstance(evidence_pack, dict) else [])

        checks = [
            {
                "name": "required_sections",
                "pass": all(section_checks.values()),
                "detail": section_checks,
            },
            {
                "name": "numeric_facts",
                "pass": numeric_count >= min_numeric,
                "detail": {"count": numeric_count, "threshold": min_numeric},
            },
            {
                "name": "peer_rows",
                "pass": len(peer_rows) >= 3,
                "detail": {"count": len(peer_rows), "threshold": 3},
            },
            {
                "name": "filings_digest",
                "pass": len(filings_digest) >= 1,
                "detail": {"count": len(filings_digest), "threshold": 1},
            },
            {
                "name": "universe_non_empty",
                "pass": len(universe) >= 1,
                "detail": {"count": len(universe), "threshold": 1},
            },
            {
                "name": "ending_question",
                "pass": (has_question if require_question else True),
                "detail": {
                    "required": bool(require_question),
                    "has_question": has_question,
                },
            },
        ]

        failed = [c["name"] for c in checks if not c["pass"]]
        pass_all = len(failed) == 0
        summary = (
            f"Sector report quality gate: {'PASS' if pass_all else 'FAIL'}; "
            f"numeric={numeric_count}, peer_rows={len(peer_rows)}, filings={len(filings_digest)}"
        )

        result = {
            "pass": pass_all,
            "failed_checks": failed,
            "checks": checks,
            "metrics": {
                "numeric_facts": numeric_count,
                "peer_rows": len(peer_rows),
                "filings_digest": len(filings_digest),
                "universe_count": len(universe),
            },
        }

        artifact = create_artifact_envelope(
            component_type=ComponentType.PLAN_TASK,
            name="Sector Report Quality Gate",
            content=result,
            description=summary,
            metadata={"gate": "sector_report"},
            visible_to_llm=True,
            display_in_report=False,
        )
        if ctx:
            await ctx.info(
                "✅ Sector report quality gate finished",
                extra={"pass": pass_all, "failed": failed},
            )
        return create_artifact_response(summary=summary, artifact=artifact)
