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
    create_symbol_error_response,
)
from src.server.domain.symbols.errors import SymbolResolutionError


_NUMBER_PATTERN = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:%|x|倍|亿美元|亿元|万元)?\b")


def _safe_excerpt(text: str, limit: int = 240) -> str:
    val = (text or "").strip()
    return val if len(val) <= limit else (val[: limit - 3] + "...")


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
        forms: list[str] = None,
        year: int = None,
        quarter: int = None,
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
            
            if ctx:
                await ctx.info(
                    f"✅ SEC定期报告获取完成: {ticker}",
                    extra={"count": len(results)}
                )
                
            result = {
                "items": results,
                "component_type": "filings_list"
            }
            
            description = _summarize_filing_collection(
                ticker,
                "SEC定期报告",
                [r for r in results if isinstance(r, dict)],
                date_keys=["filing_date", "report_date"],
                type_keys=["form", "type"],
                id_keys=["filing_id", "accession", "doc_id", "accession_number"],
                title_keys=["title", "content_summary"],
                fallback_types=forms,
            )
            
            artifact = create_artifact_envelope(
                component_type="filings_list",
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
                e, component_type="filings_list", name=f"{ticker} SEC定期报告"
            )
        except Exception as e:
            logger.error(f"Fetch periodic SEC filings failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC定期报告失败: {ticker}",
                    extra={"error": str(e)}
                )
            return [{"error": str(e)}]

    @mcp.tool(tags={"filings"})
    async def fetch_event_sec_filings(
        ticker: str,
        forms: list[str] = None,
        start_date: str = None,
        end_date: str = None,
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
            
            if ctx:
                await ctx.info(
                    f"✅ SEC临时报告获取完成: {ticker}",
                    extra={"count": len(results)}
                )
                
            result = {
                "items": results,
                "component_type": "filings_list"
            }
            
            description = _summarize_filing_collection(
                ticker,
                "SEC临时报告",
                [r for r in results if isinstance(r, dict)],
                date_keys=["filing_date", "report_date"],
                type_keys=["form", "type"],
                id_keys=["filing_id", "accession", "doc_id", "accession_number"],
                title_keys=["title", "content_summary"],
                fallback_types=forms,
            )
            
            artifact = create_artifact_envelope(
                component_type="filings_list",
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
                e, component_type="filings_list", name=f"{ticker} SEC临时报告"
            )
        except Exception as e:
            logger.error(f"Fetch event SEC filings failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC临时报告失败: {ticker}",
                    extra={"error": str(e)}
                )
            return [{"error": str(e)}]

    @mcp.tool(tags={"filings"})
    async def fetch_ashare_filings(
        symbol: str,
        filing_types: list[str] = None,
        start_date: str = None,
        end_date: str = None,
        limit: int = 10,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Get A-share announcements from CNINFO.

        Args:
            symbol: A-share ticker in format EXCHANGE:CODE
                Examples:
                - SSE:600519 (Kweichow Moutai, Shanghai Stock Exchange)
                - SZSE:000001 (Ping An Bank, Shenzhen Stock Exchange)
                - SZSE:300750 (CATL, ChiNext)
                Note: Plain codes like "600519" are also accepted
            filing_types: Report types in ENGLISH ONLY. Supported values:
                - "annual": Annual reports (年报)
                - "semi-annual": Semi-annual reports (半年报/中报)
                - "quarterly": Quarterly reports (季报)
                Example: ["annual", "quarterly"]
                IMPORTANT: Chinese terms like "年报/半年报/季报" are NOT supported
            start_date: Start date in YYYY-MM-DD format (optional)
            end_date: End date in YYYY-MM-DD format (optional)
            limit: Maximum number of results (default: 10)
            ctx: FastMCP Context for logging

        Returns:
            List of filing dictionaries with metadata and PDF URLs

        Examples:
            - Get 2024 annual report:
              symbol="SSE:600519", filing_types=["annual"],
              start_date="2024-01-01", end_date="2024-12-31"
            - Get latest quarterly reports:
              symbol="SZSE:300750", filing_types=["quarterly"], limit=5
            - Get all types:
              symbol="SSE:600519", filing_types=None
        """
        if ctx:
            await ctx.info(
                f"🔧 获取A股公告: {symbol}",
                extra={"symbol": symbol, "types": filing_types}
            )

        try:
            logger.info(
                "MCP tool called: fetch_ashare_filings",
                symbol=symbol,
                types=filing_types,
                limit=limit,
            )

            results = await filings_use_cases.fetch_ashare_filings(
                symbol=symbol,
                filing_types=filing_types,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
            
            if ctx:
                await ctx.info(
                    f"✅ A股公告获取完成: {symbol}",
                    extra={"count": len(results)}
                )
                
            result = {
                "items": results,
                "component_type": "filings_list"
            }
            
            description = _summarize_filing_collection(
                symbol,
                "A股公告",
                [r for r in results if isinstance(r, dict)],
                date_keys=["ann_date", "pub_date", "filing_date", "end_date", "filingDate"],
                type_keys=["filing_type", "report_type", "announcement_type", "type", "form"],
                id_keys=["filing_id", "announcement_id", "doc_id", "secCode"],
                title_keys=["title", "announcement_title", "content_summary"],
                fallback_types=filing_types,
            )
            
            artifact = create_artifact_envelope(
                component_type="filings_list",
                name=f"{symbol} A股公告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=False,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="filings_list", name=f"{symbol} A股公告"
            )
        except Exception as e:
            logger.error(f"Fetch A-share filings failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取A股公告失败: {symbol}",
                    extra={"error": str(e)}
                )
            return [{"error": str(e)}]

    @mcp.tool(tags={"filings"})
    async def process_document(
        doc_id: str,
        url: str,
        doc_type: str = "unknown",
        ticker: str = None,
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
                
            return result

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="filing_document", name=f"{ticker} 文档处理"
            )
        except Exception as e:
            logger.error(f"Process document failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 文档处理失败: {doc_id}",
                    extra={"error": str(e)}
                )
            return {"error": str(e)}

    @mcp.tool(tags={"filings"})
    async def get_filing_markdown(
        ticker: str,
        doc_id: str,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get SEC filing markdown content by ticker + accession number."""
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

            markdown = ""
            if isinstance(result, dict):
                markdown = str(result.get("content") or "")

            if isinstance(result, dict) and result.get("status") == "error":
                summary = (
                    f"{ticker} 文档Markdown获取失败: "
                    f"{result.get('error') or 'unknown error'}"
                )
                artifact = create_artifact_envelope(
                    component_type="filing_document",
                    name=f"{ticker} SEC文档Markdown",
                    content=result,
                    description=summary,
                    visible_to_llm=True,
                    display_in_report=False,
                )
                return create_artifact_response(summary=summary, artifact=artifact)

            summary = (
                f"{ticker} 文档Markdown获取完成: doc_id={doc_id}"
                f" | cached={bool(result.get('cached')) if isinstance(result, dict) else False}"
                f" | length={len(markdown)} chars"
                f" | heading_count={markdown.count('#')}"
            )
            artifact = create_artifact_envelope(
                component_type="filing_document",
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
                e, component_type="filing_document", name=f"{ticker} SEC文档Markdown"
            )
        except Exception as e:
            logger.error(f"Get filing markdown failed: {e}")
            if ctx:
                await ctx.error(
                    f"❌ 获取SEC文档Markdown失败: {ticker}",
                    extra={"error": str(e)},
                )
            return {"error": str(e)}

    @mcp.tool(tags={"filings"})
    async def extract_filing_key_metrics(
        ticker: str,
        doc_id: str,
        metric_hints: list[str] = None,
        max_items: int = 30,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Extract metric-like lines from filing markdown for quick evidence pickup."""
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
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
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
                component_type="filing_key_metrics",
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
                e, component_type="filing_key_metrics", name=f"{ticker} Filing Key Metrics"
            )
        except Exception as e:
            logger.error(f"Extract filing key metrics failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"filings"})
    async def extract_filing_section_facts(
        ticker: str,
        doc_id: str,
        section_hints: list[str] = None,
        max_quotes_per_section: int = 5,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Extract section-level fact snippets from filing markdown."""
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
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
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
                component_type="filing_section_facts",
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
                e, component_type="filing_section_facts", name=f"{ticker} Filing Section Facts"
            )
        except Exception as e:
            logger.error(f"Extract filing section facts failed: {e}")
            return {"error": str(e)}

    @mcp.tool(tags={"filings"})
    async def build_filing_citations(
        ticker: str,
        doc_id: str,
        metric_hints: list[str] = None,
        max_items: int = 15,
        ctx: Context = None,
    ) -> Dict[str, Any]:
        """Build lightweight citation candidates from filing metric lines."""
        max_items = max(5, min(int(max_items), 50))
        try:
            markdown_result = await filings_use_cases.get_filing_markdown(
                ticker=ticker,
                doc_id=doc_id,
            )
            markdown = (
                str(markdown_result.get("content") or "")
                if isinstance(markdown_result, dict)
                else ""
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
                component_type="filing_citations",
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
                e, component_type="filing_citations", name=f"{ticker} Filing Citations"
            )
        except Exception as e:
            logger.error(f"Build filing citations failed: {e}")
            return {"error": str(e)}
