# src/server/mcp/tools/filings_tools.py
"""MCP tools for SEC and A-share filings.
Provides access to regulatory filings and announcements.
Returns structured data (JSON).
"""

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
                "component_type": "table"
            }
            
            filing_dates = [
                str(r.get("filing_date") or r.get("report_date") or "")
                for r in results
                if isinstance(r, dict)
            ]
            filing_dates = [d for d in filing_dates if d]
            date_range = (
                f"{min(filing_dates)}~{max(filing_dates)}" if filing_dates else "日期未知"
            )
            form_set = sorted(
                {
                    str(r.get("form") or "").strip()
                    for r in results
                    if isinstance(r, dict) and r.get("form")
                }
            )
            fallback_forms = [str(f).strip() for f in (forms or []) if str(f).strip()]
            forms_text = ",".join(form_set[:3]) if form_set else ",".join(fallback_forms[:3])
            description = (
                f"{ticker} SEC定期报告: {len(results)}份, "
                f"表单={forms_text or 'N/A'}, 区间={date_range}"
            )
            
            artifact = create_artifact_envelope(
                component_type="table",
                name=f"{ticker} SEC定期报告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="table", name=f"{ticker} SEC定期报告"
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
                "component_type": "table"
            }
            
            filing_dates = [
                str(r.get("filing_date") or r.get("report_date") or "")
                for r in results
                if isinstance(r, dict)
            ]
            filing_dates = [d for d in filing_dates if d]
            date_range = (
                f"{min(filing_dates)}~{max(filing_dates)}" if filing_dates else "日期未知"
            )
            form_set = sorted(
                {
                    str(r.get("form") or "").strip()
                    for r in results
                    if isinstance(r, dict) and r.get("form")
                }
            )
            forms_text = ",".join(form_set[:3]) if form_set else ",".join(forms or [])
            description = (
                f"{ticker} SEC临时报告: {len(results)}份, "
                f"表单={forms_text or 'N/A'}, 区间={date_range}"
            )
            
            artifact = create_artifact_envelope(
                component_type="table",
                name=f"{ticker} SEC临时报告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {ticker}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="table", name=f"{ticker} SEC临时报告"
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
                "component_type": "table"
            }
            
            filing_dates = [
                str(r.get("ann_date") or r.get("pub_date") or r.get("end_date") or "")
                for r in results
                if isinstance(r, dict)
            ]
            filing_dates = [d for d in filing_dates if d]
            date_range = (
                f"{min(filing_dates)}~{max(filing_dates)}" if filing_dates else "日期未知"
            )
            type_set = sorted(
                {
                    str(
                        r.get("filing_type")
                        or r.get("report_type")
                        or r.get("announcement_type")
                        or ""
                    ).strip()
                    for r in results
                    if isinstance(r, dict)
                }
            )
            types_text = ",".join([t for t in type_set if t][:3]) or ",".join(filing_types or [])
            description = (
                f"{symbol} A股公告: {len(results)}份, 类型={types_text or 'N/A'}, "
                f"区间={date_range}"
            )
            
            artifact = create_artifact_envelope(
                component_type="table",
                name=f"{symbol} A股公告",
                content=result,
                description=description,
                visible_to_llm=False,
                display_in_report=True,
            )
            return create_artifact_response(summary=description, artifact=artifact)

        except SymbolResolutionError as e:
            if ctx:
                await ctx.warning(f"⚠️ 符号解析失败: {symbol}", extra=e.to_dict())
            return create_symbol_error_response(
                e, component_type="table", name=f"{symbol} A股公告"
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
