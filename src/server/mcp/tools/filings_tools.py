# src/server/mcp/tools/filings_tools.py
"""MCP tools for SEC and A-share filings.
Provides access to regulatory filings and announcements.
Returns structured data (JSON).
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP, Context

from src.server.core.use_cases import filings as filings_use_cases
from src.server.utils.logger import logger

from src.server.mcp.tools.artifact_utils import (
    create_artifact_envelope,
    create_artifact_response,
    create_mcp_error_result,
    create_mcp_tool_result,
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


_NUMBER_PATTERN = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:%|x|倍|亿美元|亿元|万元)?\b")
_A_SHARE_PATTERN = re.compile(r"^(?:\d{6}|(?:SH|SZ)\d{6}|\d{6}\.(?:SH|SZ))$", re.IGNORECASE)
_US_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_CN_PREFIXES = ("SSE:", "SZSE:", "SH:", "SZ:")
_US_PREFIXES = ("NASDAQ:", "NYSE:", "AMEX:", "OTC:")


def _safe_excerpt(text: str, limit: int = 240) -> str:
    val = (text or "").strip()
    return val if len(val) <= limit else (val[: limit - 3] + "...")


def _looks_a_share_symbol(symbol: str | None) -> bool:
    token = str(symbol or "").strip().upper()
    if not token:
        return False
    if token.startswith(_CN_PREFIXES):
        return True
    if token.endswith((".SH", ".SZ")):
        return True
    if _A_SHARE_PATTERN.match(token):
        return True
    if ":" in token:
        token = token.split(":", 1)[1]
    return token.isdigit() and len(token) == 6


def _is_us_symbol(symbol: str | None) -> bool:
    token = str(symbol or "").strip().upper()
    if not token:
        return False
    if _looks_a_share_symbol(token):
        return False
    if ":" in token:
        return token.startswith(_US_PREFIXES)
    return bool(_US_SYMBOL_PATTERN.match(token))


def _invalid_route_error(
    message: str,
    *,
    details: Dict[str, Any],
    suggested_reroute: str,
) -> Dict[str, Any]:
    return create_mcp_error_result(
        message,
        error_code="INVALID_ROUTE",
        details={**details, "suggested_reroute": suggested_reroute},
    )


def _no_data_result(reason: str, *, details: Dict[str, Any], suggested_reroute: str) -> Dict[str, Any]:
    response = create_mcp_tool_result(
        reason,
        resources=[],
        no_data_reason=reason,
    )
    response.structuredContent.update(
        {
            "scope": details,
            "retriable": False,
            "suggested_reroute": suggested_reroute,
        }
    )
    return response


def _extract_error(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict):
            return dict(error)
    return None


def _extract_no_data_reason(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("no_data_reason") or "").strip()
    return ""


def _extract_metric_lines(
    markdown: str,
    metric_hints: List[str],
    max_items: int = 30,
) -> List[Dict[str, Any]]:
    lines = (markdown or "").splitlines()
    hints = [h.lower() for h in (metric_hints or []) if str(h).strip()]
    items: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue
        lowered = raw.lower()
        matched_hints = [h for h in hints if h in lowered]
        numbers = _NUMBER_PATTERN.findall(raw)
        if not numbers:
            continue
        if hints and not matched_hints:
            continue
        items.append(
            {
                "line_no": idx,
                "text": _safe_excerpt(raw),
                "numbers": numbers[:8],
                "metric_hints": matched_hints[:5],
            }
        )
        if len(items) >= max_items:
            break
    return items


def _extract_section_facts(
    markdown: str,
    section_hints: List[str],
    max_quotes_per_section: int = 5,
) -> List[Dict[str, Any]]:
    lines = (markdown or "").splitlines()
    hints = [h.lower() for h in (section_hints or []) if str(h).strip()]
    sections: List[Dict[str, Any]] = []

    current_heading = ""
    current_lines: List[str] = []
    collected: Dict[str, List[Dict[str, Any]]] = {}

    def _flush():
        nonlocal current_heading, current_lines
        if not current_heading or not current_lines:
            current_heading = ""
            current_lines = []
            return
        heading_key = current_heading.lower()
        matched = [h for h in hints if h in heading_key] if hints else [current_heading]
        if matched:
            snippets = []
            for i, text in enumerate(current_lines):
                clean = text.strip()
                if not clean:
                    continue
                snippets.append({"text": _safe_excerpt(clean, 300)})
                if len(snippets) >= max_quotes_per_section:
                    break
            if snippets:
                bucket = collected.setdefault(current_heading, [])
                bucket.extend(snippets)
        current_heading = ""
        current_lines = []

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("#"):
            _flush()
            current_heading = stripped.lstrip("#").strip()
            continue
        if current_heading:
            current_lines.append(stripped)
    _flush()

    for heading, snippets in collected.items():
        sections.append(
            {
                "heading": heading,
                "facts": snippets[:max_quotes_per_section],
            }
        )
    return sections


def _first_non_empty(record: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        val = str(record.get(key) or "").strip()
        if val:
            return val
    return ""


def _summarize_filing_collection(
    subject: str,
    label: str,
    records: List[Dict[str, Any]],
    *,
    date_keys: List[str],
    type_keys: List[str],
    id_keys: List[str],
    title_keys: List[str],
    fallback_types: Optional[List[str]] = None,
) -> str:
    total = len(records)
    date_vals = [
        _first_non_empty(item, date_keys)
        for item in records
        if isinstance(item, dict)
    ]
    date_vals = [d for d in date_vals if d]
    date_range = f"{min(date_vals)}~{max(date_vals)}" if date_vals else "日期未知"

    type_vals = [
        _first_non_empty(item, type_keys)
        for item in records
        if isinstance(item, dict)
    ]
    type_vals = [t for t in type_vals if t]
    type_counter = Counter(type_vals)
    if type_counter:
        top_types = ",".join([f"{k}({v})" for k, v in type_counter.most_common(3)])
    else:
        defaults = [str(t).strip() for t in (fallback_types or []) if str(t).strip()]
        top_types = ",".join(defaults[:3]) or "N/A"

    latest = ""
    if date_vals:
        dated_records = []
        for item in records:
            if not isinstance(item, dict):
                continue
            item_date = _first_non_empty(item, date_keys)
            if item_date:
                dated_records.append((item_date, item))
        if dated_records:
            latest_item = sorted(dated_records, key=lambda x: x[0], reverse=True)[0][1]
            latest_doc = _first_non_empty(latest_item, id_keys) or "N/A"
            latest_title = _safe_excerpt(_first_non_empty(latest_item, title_keys), 80) or "N/A"
            latest = f"latest_doc={latest_doc}, latest_title={latest_title}"

    summary_parts = [
        f"{subject} {label}: {total}份",
        f"类型={top_types}",
        f"区间={date_range}",
    ]
    if latest:
        summary_parts.append(latest)
    return " | ".join(summary_parts)


def register_filings_tools(mcp: FastMCP):
    """Register filings tools."""

    @mcp.tool(tags={"filings"})
    async def fetch_periodic_sec_filings(
        ticker: str,
        forms: list[str] | None = None,
        year: int | None = None,
        quarter: int | None = None,
        limit: int = 10,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Get SEC periodic filings (10-K/10-Q) for US stocks.

        Designed for regular, scheduled reports with fiscal year/quarter.
        Use this for fundamental analysis and earnings tracking.

        Args:
            ticker: US stock ticker (e.g., "AAPL", "TSLA", "BABA")
            forms: Filing types. Defaults to ["10-K", "10-Q", "20-F", "6-K"]
                - "10-K": Annual reports (US companies)
                - "10-Q": Quarterly reports (US companies)
                - "20-F": Annual reports (Foreign Private Issuers, e.g., BABA)
                - "6-K": Current/Quarterly reports (Foreign Private Issuers)
                Example: ["10-K", "10-Q"]
            year: Fiscal year or list of years (e.g., 2024, [2023, 2024])
                When omitted, returns latest filings using `limit`
            quarter: Fiscal quarter (1-4) or list of quarters
                Requires `year` to be provided
                Example: 3 or [1, 3]
            limit: Max results when year is omitted (default: 10)
            ctx: FastMCP Context for logging

        Returns:
            List of filing dictionaries with metadata

        Examples:
            # Get AAPL's 2024 annual report
            ticker="AAPL", forms=["10-K"], year=2024

            # Get TSLA's Q3 2024 quarterly report
            ticker="TSLA", forms=["10-Q"], year=2024, quarter=3

            # Get latest 5 quarterly reports
            ticker="MSFT", forms=["10-Q"], limit=5
        """
        if ctx:
            await ctx.info(
                f"🔧 获取SEC定期报告: {ticker}",
                extra={"ticker": ticker, "forms": forms, "year": year, "quarter": quarter}
            )

        if _looks_a_share_symbol(ticker) or not _is_us_symbol(ticker):
            return _invalid_route_error(
                "fetch_periodic_sec_filings is US-only and does not accept A-share/non-US symbols.",
                details={"ticker": ticker, "forms": forms, "year": year, "quarter": quarter},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )

        try:
            logger.info(
                "MCP tool called: fetch_periodic_sec_filings",
                ticker=ticker,
                forms=forms,
                year=year,
                quarter=quarter,
                limit=limit,
            )

            results = await filings_use_cases.fetch_periodic_sec_filings(
                ticker=ticker,
                forms=forms,
                year=year,
                quarter=quarter,
                limit=limit,
            )
            
            valid_records = [r for r in results if isinstance(r, dict) and not r.get("error")]
            if not valid_records:
                return _no_data_result(
                    f"No SEC periodic filings found for {ticker} in current query scope.",
                    details={"ticker": ticker, "forms": forms, "year": year, "quarter": quarter, "limit": limit},
                    suggested_reroute="Broaden year/quarter/forms or call event filings as supplement.",
                )

            if ctx:
                await ctx.info(
                    f"✅ SEC定期报告获取完成: {ticker}",
                    extra={"count": len(valid_records)}
                )
                
            result = {
                "items": valid_records,
                "variant": "filings_list"
            }
            
            description = _summarize_filing_collection(
                ticker,
                "SEC定期报告",
                valid_records,
                date_keys=["filing_date", "report_date"],
                type_keys=["form", "type"],
                id_keys=["filing_id", "accession", "doc_id", "accession_number"],
                title_keys=["title", "content_summary"],
                fallback_types=forms,
            )
            
            artifact = create_artifact_envelope(
                variant="filings_list",
                name=f"{ticker} SEC定期报告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="filings_list", name=f"{ticker} SEC定期报告"
            )
        except Exception as e:
            logger.error(f"Fetch periodic SEC filings failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC定期报告失败: {ticker}",
                    extra={"error": str(e)}
                )
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"filings"})
    async def fetch_event_sec_filings(
        ticker: str,
        forms: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Get SEC event-driven filings (8-K, Forms 3/4/5) for US stocks.

        Designed for irregular, event-triggered reports.
        Use this for news tracking and material event monitoring.

        Args:
            ticker: US stock ticker (e.g., "AAPL", "TSLA", "BABA")
            forms: Filing types. Defaults to ["8-K", "6-K"]
                - "8-K": Current reports (US companies)
                - "6-K": Current reports (Foreign Private Issuers, e.g., BABA)
                - "3": Initial insider ownership
                - "4": Changes in insider ownership
                - "5": Annual insider ownership
                Example: ["8-K", "4"]
            start_date: Start date in YYYY-MM-DD format (optional)
            end_date: End date in YYYY-MM-DD format (optional)
            limit: Max results after filtering (default: 10)
            ctx: FastMCP Context for logging

        Returns:
            List of filing dictionaries with metadata

        Examples:
            # Get AAPL's latest 8-K filings
            ticker="AAPL", forms=["8-K"], limit=10

            # Get insider trading in past 30 days
            ticker="TSLA", forms=["4"],
            start_date="2024-11-01", end_date="2024-11-30"

            # Get all event filings in a date range
            ticker="MSFT", start_date="2024-10-01",
            end_date="2024-10-31"
        """
        if ctx:
            await ctx.info(
                f"🔧 获取SEC临时报告: {ticker}",
                extra={"ticker": ticker, "forms": forms}
            )

        if _looks_a_share_symbol(ticker) or not _is_us_symbol(ticker):
            return _invalid_route_error(
                "fetch_event_sec_filings is US-only and does not accept A-share/non-US symbols.",
                details={"ticker": ticker, "forms": forms, "start_date": start_date, "end_date": end_date},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )

        try:
            logger.info(
                "MCP tool called: fetch_event_sec_filings",
                ticker=ticker,
                forms=forms,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

            results = await filings_use_cases.fetch_event_sec_filings(
                ticker=ticker,
                forms=forms,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
            
            valid_records = [r for r in results if isinstance(r, dict) and not r.get("error")]
            if not valid_records:
                return _no_data_result(
                    f"No SEC event filings found for {ticker} in current query scope.",
                    details={"ticker": ticker, "forms": forms, "start_date": start_date, "end_date": end_date, "limit": limit},
                    suggested_reroute="Broaden date range/forms or fetch periodic filings as supplement.",
                )

            if ctx:
                await ctx.info(
                    f"✅ SEC临时报告获取完成: {ticker}",
                    extra={"count": len(valid_records)}
                )
                
            result = {
                "items": valid_records,
                "variant": "filings_list"
            }
            
            description = _summarize_filing_collection(
                ticker,
                "SEC临时报告",
                valid_records,
                date_keys=["filing_date", "report_date"],
                type_keys=["form", "type"],
                id_keys=["filing_id", "accession", "doc_id", "accession_number"],
                title_keys=["title", "content_summary"],
                fallback_types=forms,
            )
            
            artifact = create_artifact_envelope(
                variant="filings_list",
                name=f"{ticker} SEC临时报告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="filings_list", name=f"{ticker} SEC临时报告"
            )
        except Exception as e:
            logger.error(f"Fetch event SEC filings failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC临时报告失败: {ticker}",
                    extra={"error": str(e)}
                )
            return create_mcp_error_result(str(e))

    async def fetch_ashare_filings(
        symbol: str,
        filing_types: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Deprecated route: filings capability is now US-only."""
        if ctx:
            await ctx.info(
                f"🔧 获取A股公告: {symbol}",
                extra={"symbol": symbol, "types": filing_types}
            )
        logger.warning(
            "fetch_ashare_filings called after US-only switch",
            symbol=symbol,
            filing_types=filing_types,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return _invalid_route_error(
            "fetch_ashare_filings is disabled: filings capability is US-only in current runtime.",
            details={
                "symbol": symbol,
                "filing_types": filing_types,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
            },
            suggested_reroute="Use A-share structured/news tools instead of SEC filings route.",
        )

    @mcp.tool(tags={"filings"})
    async def process_document(
        doc_id: str,
        url: str,
        doc_type: str = "unknown",
        ticker: str | None = None,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Process a single document by URL (Download & Extract Text).

        This tool downloads the document content from the given URL.
        - For HTML documents (e.g., SEC filings), it extracts the text content.
        - For PDF documents (e.g., CNINFO announcements), it returns metadata indicating PDF type.
        
        For SEC filings (10-K, 10-Q, 8-K), `ticker` is REQUIRED to use edgartools for correct parsing.

        Args:
            doc_id: Unique document identifier (e.g., Accession Number)
            url: Direct URL to the document
            doc_type: Document type (e.g., "10-K", "annual")
            ticker: Stock ticker (Required for SEC filings)
            ctx: FastMCP Context for logging

        Returns:
            Dictionary containing 'content' (extracted text), 'status', and metadata.
        """
        if ctx:
            await ctx.info(
                f"🔧 处理文档: {doc_id}",
                extra={"url": url, "doc_type": doc_type, "ticker": ticker}
            )

        if ticker and _looks_a_share_symbol(ticker):
            return _invalid_route_error(
                "process_document is US SEC-only and does not accept A-share ticker.",
                details={"ticker": ticker, "doc_id": doc_id, "url": url, "doc_type": doc_type},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )
        if "cninfo" in str(url or "").lower():
            return _invalid_route_error(
                "process_document is US SEC-only and does not accept cninfo source.",
                details={"ticker": ticker, "doc_id": doc_id, "url": url, "doc_type": doc_type},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )

        try:
            logger.info(
                "MCP tool called: process_document",
                doc_id=doc_id,
                url=url,
                doc_type=doc_type,
                ticker=ticker,
            )

            result = await filings_use_cases.process_document(
                doc_id=doc_id,
                url=url,
                doc_type=doc_type,
                ticker=ticker,
            )
            
            if ctx:
                await ctx.info(f"✅ 文档处理完成: {doc_id}")
                
            if isinstance(result, dict):
                error = _extract_error(result)
                if error:
                    return create_mcp_error_result(
                        str(error.get("message") or "Document processing failed"),
                        error_code=str(error.get("code") or "INTERNAL_ERROR"),
                        details=error.get("details") if isinstance(error.get("details"), dict) else None,
                    )
                no_data_reason = _extract_no_data_reason(result)
                if no_data_reason:
                    return _no_data_result(
                        no_data_reason,
                        details={"ticker": ticker, "doc_id": doc_id, "url": url, "doc_type": doc_type},
                        suggested_reroute="Adjust filing range/doc_id or use filing list/chunk tools first.",
                    )
                artifact = create_artifact_envelope(
                    variant="filing_document",
                    name=f"{ticker or doc_id} Processed Document",
                    content=result,
                    description=f"Processed filing document payload for {doc_id}",
                    visible_to_llm=True,
                    display_in_report=False,
                )
                return create_artifact_response(
                    summary=f"Document processed successfully: {doc_id}",
                    artifact=artifact,
                )
            return create_mcp_error_result(
                "process_document returned a non-dict payload",
                error_code="CONTRACT_VIOLATION",
            )

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="filing_document", name=f"{ticker} 文档处理"
            )
        except Exception as e:
            logger.error(f"Process document failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 文档处理失败: {doc_id}",
                    extra={"error": str(e)}
                )
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"filings"})
    async def get_filing_markdown(
        ticker: str,
        doc_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get SEC filing markdown content by ticker + accession number."""
        if _looks_a_share_symbol(ticker):
            return _invalid_route_error(
                "get_filing_markdown is US SEC-only and does not accept A-share ticker.",
                details={"ticker": ticker, "doc_id": doc_id},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )

        if ctx:
            await ctx.info(
                f"🔧 获取SEC文档Markdown: {ticker} {doc_id}",
                extra={"ticker": ticker, "doc_id": doc_id},
            )

        try:
            logger.info(
                "MCP tool called: get_filing_markdown",
                ticker=ticker,
                doc_id=doc_id,
            )
            result = await filings_use_cases.get_filing_markdown(
                ticker=ticker,
                doc_id=doc_id,
            )

            markdown = str(result.get("content") or "") if isinstance(result, dict) else ""
            error = _extract_error(result)
            if error:
                return create_mcp_error_result(
                    f"{ticker} 文档Markdown获取失败: {error.get('message') or error.get('code') or 'unknown error'}",
                    error_code=str(error.get("code") or "INTERNAL_ERROR"),
                    details=error.get("details") if isinstance(error.get("details"), dict) else None,
                )

            no_data_reason = _extract_no_data_reason(result)
            if no_data_reason or not markdown.strip():
                reason = no_data_reason or "filing markdown is empty"
                return _no_data_result(
                    f"{ticker} 文档Markdown无数据: {reason}",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute=(
                        result.get("suggested_reroute")
                        if isinstance(result, dict) and result.get("suggested_reroute")
                        else "Adjust doc_id/time window or fetch filing list first."
                    ),
                )

            summary = (
                f"{ticker} 文档Markdown获取完成: doc_id={doc_id}"
                f" | cached={bool(result.get('cached')) if isinstance(result, dict) else False}"
                f" | length={len(markdown)} chars"
                f" | heading_count={markdown.count('#')}"
            )
            artifact = create_artifact_envelope(
                variant="filing_document",
                name=f"{ticker} SEC文档Markdown",
                content=result,
                description=summary,
                visible_to_llm=False,
                display_in_report=False,
            )
            if ctx:
                await ctx.info(
                    f"✅ SEC文档Markdown获取完成: {ticker}",
                    extra={
                        "doc_id": doc_id,
                        "cached": bool(result.get("cached"))
                        if isinstance(result, dict)
                        else False,
                        "length": len(markdown),
                    },
                )
            return create_artifact_response(summary=summary, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, variant="filing_document", name=f"{ticker} SEC文档Markdown"
            )
        except Exception as e:
            logger.error(f"Get filing markdown failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC文档Markdown失败: {ticker}",
                    extra={"error": str(e)},
                )
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"filings"})
    async def extract_filing_key_metrics(
        ticker: str,
        doc_id: str,
        metric_hints: list[str] | None = None,
        max_items: int = 30,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Extract metric-like lines from filing markdown for quick evidence pickup."""
        if _looks_a_share_symbol(ticker):
            return _invalid_route_error(
                "extract_filing_key_metrics is US SEC-only and does not accept A-share ticker.",
                details={"ticker": ticker, "doc_id": doc_id},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )

        default_hints = [
            "revenue",
            "net income",
            "operating income",
            "eps",
            "margin",
            "capex",
            "guidance",
            "收入",
            "净利润",
            "毛利率",
            "现金流",
            "资本开支",
            "同比",
        ]
        hints = metric_hints or default_hints
        max_items = max(5, min(int(max_items), 100))
        if ctx:
            await ctx.info(
                f"🔧 提取文档关键指标: {ticker} {doc_id}",
                extra={"ticker": ticker, "doc_id": doc_id, "max_items": max_items},
            )

        try:
            markdown_result = await filings_use_cases.get_filing_markdown(
                ticker=ticker,
                doc_id=doc_id,
            )
            error = _extract_error(markdown_result)
            if error:
                return create_mcp_error_result(
                    str(error.get("message") or "unknown error"),
                    error_code=str(error.get("code") or "INTERNAL_ERROR"),
                    details=error.get("details") if isinstance(error.get("details"), dict) else None,
                )
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
            )
            no_data_reason = _extract_no_data_reason(markdown_result)
            if no_data_reason or not markdown.strip():
                return _no_data_result(
                    no_data_reason or "filing markdown unavailable",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Adjust filing range/doc_id or use filing list/chunk tools first.",
                )
            items = _extract_metric_lines(markdown, hints, max_items=max_items)
            preview = []
            for item in items[:2]:
                numbers = ",".join(item.get("numbers", [])[:3])
                preview.append(
                    f"L{item.get('line_no')}: {item.get('text')} [{numbers}]"
                )
            summary = (
                f"{ticker} 关键指标提取完成: {len(items)}条"
                f" | doc_id={doc_id}"
                f" | cached={bool(markdown_result.get('cached')) if isinstance(markdown_result, dict) else False}"
                f" | 示例={' ; '.join(preview) if preview else 'N/A'}"
            )
            artifact = create_artifact_envelope(
                variant="filing_key_metrics",
                name=f"{ticker} Filing Key Metrics",
                content={
                    "ticker": ticker,
                    "doc_id": doc_id,
                    "items": items,
                    "metric_hints": hints,
                },
                description=summary,
                visible_to_llm=True,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, variant="filing_key_metrics", name=f"{ticker} Filing Key Metrics"
            )
        except Exception as e:
            logger.error(f"Extract filing key metrics failed: {e}")
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"filings"})
    async def extract_filing_section_facts(
        ticker: str,
        doc_id: str,
        section_hints: list[str] | None = None,
        max_quotes_per_section: int = 5,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Extract section-level fact snippets from filing markdown."""
        if _looks_a_share_symbol(ticker):
            return _invalid_route_error(
                "extract_filing_section_facts is US SEC-only and does not accept A-share ticker.",
                details={"ticker": ticker, "doc_id": doc_id},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )
        default_sections = ["item 1a", "item 7", "item 8", "risk factors", "md&a"]
        hints = section_hints or default_sections
        max_quotes = max(1, min(int(max_quotes_per_section), 20))
        if ctx:
            await ctx.info(
                f"🔧 提取文档章节事实: {ticker} {doc_id}",
                extra={
                    "ticker": ticker,
                    "doc_id": doc_id,
                    "section_hints": hints,
                },
            )

        try:
            markdown_result = await filings_use_cases.get_filing_markdown(
                ticker=ticker,
                doc_id=doc_id,
            )
            error = _extract_error(markdown_result)
            if error:
                return create_mcp_error_result(
                    str(error.get("message") or "unknown error"),
                    error_code=str(error.get("code") or "INTERNAL_ERROR"),
                    details=error.get("details") if isinstance(error.get("details"), dict) else None,
                )
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
            )
            no_data_reason = _extract_no_data_reason(markdown_result)
            if no_data_reason or not markdown.strip():
                return _no_data_result(
                    no_data_reason or "filing markdown unavailable",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Adjust filing range/doc_id or use filing list/chunk tools first.",
                )
            sections = _extract_section_facts(
                markdown,
                hints,
                max_quotes_per_section=max_quotes,
            )
            quote_count = sum(
                len(sec.get("facts", []))
                for sec in sections
                if isinstance(sec, dict)
            )
            headings = [
                str(sec.get("heading") or "").strip()
                for sec in sections[:3]
                if isinstance(sec, dict)
            ]
            summary = (
                f"{ticker} 章节事实提取完成: {len(sections)}个章节"
                f" | quotes={quote_count}"
                f" | doc_id={doc_id}"
                f" | sections={','.join([h for h in headings if h]) or 'N/A'}"
            )
            artifact = create_artifact_envelope(
                variant="filing_section_facts",
                name=f"{ticker} Filing Section Facts",
                content={
                    "ticker": ticker,
                    "doc_id": doc_id,
                    "sections": sections,
                    "section_hints": hints,
                },
                description=summary,
                visible_to_llm=True,
                display_in_report=True,
            )
            return create_artifact_response(summary=summary, artifact=artifact)
        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, variant="filing_section_facts", name=f"{ticker} Filing Section Facts"
            )
        except Exception as e:
            logger.error(f"Extract filing section facts failed: {e}")
            return create_mcp_error_result(str(e))

    @mcp.tool(tags={"filings"})
    async def build_filing_citations(
        ticker: str,
        doc_id: str,
        metric_hints: list[str] | None = None,
        max_items: int = 15,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build lightweight citation candidates from filing metric lines."""
        if _looks_a_share_symbol(ticker):
            return _invalid_route_error(
                "build_filing_citations is US SEC-only and does not accept A-share ticker.",
                details={"ticker": ticker, "doc_id": doc_id},
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )
        max_items = max(5, min(int(max_items), 50))
        try:
            markdown_result = await filings_use_cases.get_filing_markdown(
                ticker=ticker,
                doc_id=doc_id,
            )
            error = _extract_error(markdown_result)
            if error:
                return create_mcp_error_result(
                    str(error.get("message") or "unknown error"),
                    error_code=str(error.get("code") or "INTERNAL_ERROR"),
                    details=error.get("details") if isinstance(error.get("details"), dict) else None,
                )
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
            )
            no_data_reason = _extract_no_data_reason(markdown_result)
            if no_data_reason or not markdown.strip():
                return _no_data_result(
                    no_data_reason or "filing markdown unavailable",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Adjust filing range/doc_id or use filing list/chunk tools first.",
                )
            metric_items = _extract_metric_lines(
                markdown,
                metric_hints or ["revenue", "net income", "eps", "收入", "净利润", "毛利率"],
                max_items=max_items,
            )
            citations = []
            for item in metric_items[:max_items]:
                line_no = item.get("line_no")
                citations.append(
                    {
                        "ref_id": f"{doc_id}#L{line_no}",
                        "ticker": ticker,
                        "doc_id": doc_id,
                        "line_no": line_no,
                        "quote": item.get("text"),
                        "numbers": item.get("numbers", []),
                    }
                )
            first_ref = citations[0].get("ref_id") if citations else "N/A"
            summary = (
                f"{ticker} 引用锚点构建完成: {len(citations)}条"
                f" | doc_id={doc_id}"
                f" | first_ref={first_ref}"
            )
            artifact = create_artifact_envelope(
                variant="filing_citations",
                name=f"{ticker} Filing Citations",
                content={
                    "ticker": ticker,
                    "doc_id": doc_id,
                    "citations": citations,
                },
                description=summary,
                visible_to_llm=True,
                display_in_report=True,
            )
            if ctx:
                await ctx.info(
                    f"✅ 文档引用锚点构建完成: {ticker}",
                    extra={"doc_id": doc_id, "count": len(citations)},
                )
            return create_artifact_response(summary=summary, artifact=artifact)
        except SymbolResolutionError as e:
            return create_symbol_error_response(
                e, variant="filing_citations", name=f"{ticker} Filing Citations"
            )
        except Exception as e:
            logger.error(f"Build filing citations failed: {e}")
            return create_mcp_error_result(str(e))
