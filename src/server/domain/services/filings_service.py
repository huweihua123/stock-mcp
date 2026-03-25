# src/server/domain/services/filings_service.py
"""FilingsService – fetch regulatory filings and announcements.

Uses the runtime provider facade to retrieve data from appropriate sources
(Finnhub for SEC, Akshare for A-share) and returns structured JSON.
"""

from datetime import datetime
import re
from typing import Any, Dict, List, Optional

from src.server.utils.logger import logger


class FilingsService:
    """Service for retrieving regulatory filings and announcements."""

    _CN_EXCHANGE_PREFIXES = ("SSE:", "SZSE:", "SH:", "SZ:")
    _US_EXCHANGE_PREFIXES = ("NASDAQ:", "NYSE:", "AMEX:", "OTC:")
    _A_SHARE_CODE_RE = re.compile(r"^(?:\d{6}|(?:SH|SZ)\d{6}|\d{6}\.(?:SH|SZ))$", re.IGNORECASE)
    _US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

    def __init__(self, provider_facade, minio_client=None):
        """Initialize the service with the runtime provider facade."""
        self.provider_facade = provider_facade
        self.minio_client = minio_client
        self.logger = logger

        # Initialize edgartools identity and cache via sec_utils
        # This ensures consistent identity and avoids ticker.txt downloads
        from src.server.utils.sec_utils import get_company, get_cik_or_symbol

        # No need to call set_identity here, sec_utils handles it on import

    def _extract_symbol(self, ticker: str) -> str:
        """Extract pure symbol from ticker (e.g. 'NASDAQ:AAPL' -> 'AAPL')."""
        if ":" in ticker:
            return ticker.split(":", 1)[1]
        return ticker

    def _looks_a_share_ticker(self, ticker: str) -> bool:
        token = str(ticker or "").strip().upper()
        if not token:
            return False
        if token.startswith(self._CN_EXCHANGE_PREFIXES):
            return True
        if token.endswith((".SH", ".SZ")):
            return True
        if self._A_SHARE_CODE_RE.match(token):
            return True
        pure = self._extract_symbol(token)
        if pure.isdigit() and len(pure) == 6:
            return True
        return False

    def _is_us_ticker(self, ticker: str) -> bool:
        token = str(ticker or "").strip().upper()
        if not token:
            return False
        if self._looks_a_share_ticker(token):
            return False
        if ":" in token:
            return token.startswith(self._US_EXCHANGE_PREFIXES)
        return bool(self._US_SYMBOL_RE.match(token))

    @staticmethod
    def _error_result(
        *,
        code: str,
        message: str,
        details: Dict[str, Any] | None = None,
        retriable: bool = False,
        suggested_reroute: str = "Reroute to an appropriate data route before retrying.",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "error",
            "message": message,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
            "retriable": retriable,
            "suggested_reroute": suggested_reroute,
        }
        return payload

    @staticmethod
    def _no_data_result(
        *,
        reason: str,
        details: Dict[str, Any] | None = None,
        suggested_reroute: str = "Broaden time window, change symbol scope, or switch to alternative evidence source.",
    ) -> Dict[str, Any]:
        return {
            "status": "no_data",
            "message": reason,
            "no_data_reason": reason,
            "scope": details or {},
            "retriable": False,
            "suggested_reroute": suggested_reroute,
        }

    @staticmethod
    def _first_non_empty(record: Dict[str, Any], keys: List[str]) -> Any:
        """Return first non-empty value from candidate keys."""
        for key in keys:
            value = record.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _normalize_filing_record(
        self,
        ticker: str,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Normalize heterogeneous adapter payload to stable filing schema.

        Keep original keys while adding canonical fields used by LLM/tooling:
        - doc_id/accession/accession_number
        - filing_date/report_date
        - form/type/filing_url/symbol
        """
        if not isinstance(record, dict):
            return record
        if "error" in record:
            return record

        accession = self._first_non_empty(
            record,
            [
                "doc_id",
                "accession",
                "accession_number",
                "accessionNumber",
                "filing_id",
            ],
        )
        filing_date = self._first_non_empty(
            record,
            ["filing_date", "filingDate", "filed_date", "filedDate", "ann_date", "pub_date"],
        )
        report_date = self._first_non_empty(
            record,
            ["report_date", "reportDate", "period_of_report", "periodOfReport"],
        )
        form = self._first_non_empty(record, ["form", "type", "filing_type"])
        filing_url = self._first_non_empty(record, ["filing_url", "filingUrl", "url"])
        description = self._first_non_empty(
            record,
            ["description", "title", "content_summary", "announcement_title"],
        )
        symbol = self._first_non_empty(record, ["symbol", "ticker", "secCode"]) or self._extract_symbol(
            ticker
        )

        normalized = dict(record)
        if accession is not None:
            normalized.setdefault("doc_id", accession)
            normalized.setdefault("accession", accession)
            normalized.setdefault("accession_number", accession)
            normalized.setdefault("accessionNumber", accession)
        if filing_date is not None:
            normalized.setdefault("filing_date", filing_date)
            normalized.setdefault("filingDate", filing_date)
        if report_date is not None:
            normalized.setdefault("report_date", report_date)
            normalized.setdefault("reportDate", report_date)
        if form is not None:
            normalized.setdefault("form", form)
            normalized.setdefault("type", form)
        if filing_url is not None:
            normalized.setdefault("filing_url", filing_url)
            normalized.setdefault("filingUrl", filing_url)
            normalized.setdefault("url", filing_url)
        if description is not None:
            normalized.setdefault("description", description)
        if symbol is not None:
            normalized.setdefault("symbol", symbol)

        return normalized

    async def fetch_periodic_sec_filings(
        self,
        ticker: str,
        forms: Optional[List[str]] = None,
        year: Optional[int] = None,
        quarter: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch SEC periodic filings (10-K/10-Q) with year/quarter.

        Args:
            ticker: US stock ticker
            forms: Filing forms (default: ["10-Q"])
            year: Fiscal year (e.g., 2024)
            quarter: Fiscal quarter (1-4)
            limit: Max results when year is omitted

        Returns:
            List of filing dictionaries
        """
        filing_types = forms or ["10-K", "10-Q", "20-F", "6-K"]

        # Convert year/quarter to date range for adapter
        start_date = None
        end_date = None

        if year:
            # Use year as date range
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

        return await self._fetch_filings(
            ticker, filing_types, start_date, end_date, limit
        )

    async def fetch_event_sec_filings(
        self,
        ticker: str,
        forms: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch SEC event-driven filings (8-K, 3/4/5) with date range.

        Args:
            ticker: US stock ticker
            forms: Filing forms (default: ["8-K"])
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            limit: Max results

        Returns:
            List of filing dictionaries
        """
        filing_types = forms or ["8-K", "6-K"]

        return await self._fetch_filings(
            ticker, filing_types, start_date, end_date, limit
        )

    async def fetch_ashare_filings(
        self,
        symbol: str,
        filing_types: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch A-share announcements.

        Note: Ticker normalization is handled by AkshareAdapter.
        """
        # Delegate to unified method - adapter will handle format
        return await self._fetch_filings(
            symbol, filing_types, start_date, end_date, limit
        )

    async def _fetch_filings(
        self,
        ticker: str,
        filing_types: Optional[List[str]],
        start_date_str: Optional[str],
        end_date_str: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Internal unified method to fetch filings via runtime provider facade."""
        try:
            start = (
                datetime.strptime(start_date_str, "%Y-%m-%d")
                if start_date_str
                else None
            )
            end = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else None

            filings = await self.provider_facade.get_filings(
                ticker,
                start_date=start,
                end_date=end,
                limit=limit,
                filing_types=filing_types,
            )
            if not isinstance(filings, list):
                return []
            return [
                self._normalize_filing_record(ticker=ticker, record=item)
                if isinstance(item, dict)
                else item
                for item in filings
            ]

        except Exception as e:
            self.logger.error(f"Failed to fetch filings for {ticker}: {e}")
            return [{"error": str(e)}]

    async def process_document(
        self,
        doc_id: str,
        url: str,
        doc_type: str,
        ticker: str = None,
    ) -> Dict[str, Any]:
        """Process SEC filing document (US-only) and upload markdown/pdf to MinIO."""
        if not self.minio_client:
            return self._error_result(
                code="INTERNAL_ERROR",
                message="MinIO client not configured",
                retriable=False,
            )

        try:
            if not ticker:
                return self._error_result(
                    code="INVALID_ARGUMENT",
                    message="ticker is required for SEC document processing",
                    details={"doc_id": doc_id, "url": url, "doc_type": doc_type},
                    retriable=False,
                )

            if self._looks_a_share_ticker(ticker) or "cninfo" in str(url or "").lower():
                return self._error_result(
                    code="INVALID_ROUTE",
                    message="process_document is US SEC-only; A-share/cninfo inputs are not supported.",
                    details={"ticker": ticker, "doc_id": doc_id, "url": url},
                    retriable=False,
                    suggested_reroute="Use A-share announcement/news tools for CN filings.",
                )
            if not self._is_us_ticker(ticker):
                return self._error_result(
                    code="INVALID_ROUTE",
                    message="process_document only supports US tickers.",
                    details={"ticker": ticker, "doc_id": doc_id},
                    retriable=False,
                    suggested_reroute="Provide a valid US ticker (e.g., AAPL, NASDAQ:NVDA).",
                )

            is_sec = (
                doc_type in ["10-K", "10-Q", "8-K", "20-F", "6-K"]
                or "SEC" in str(doc_id or "").upper()
                or "edgar" in str(url or "").lower()
            )
            if not is_sec:
                return self._error_result(
                    code="INVALID_ROUTE",
                    message="process_document only supports SEC filing documents.",
                    details={"ticker": ticker, "doc_id": doc_id, "doc_type": doc_type, "url": url},
                    retriable=False,
                    suggested_reroute="Route non-SEC evidence requests to news/structured data tools.",
                )

            pure_symbol = self._extract_symbol(ticker)
            self.logger.info(f"Processing SEC filing for {ticker} (pure symbol: {pure_symbol})")

            from src.server.utils.sec_utils import get_company

            company = get_company(pure_symbol)
            accession_number = doc_id.replace("SEC:", "")
            filings = company.get_filings().latest(50)

            target_filing = None
            if filings:
                for filing in filings:
                    if filing.accession_no == accession_number:
                        target_filing = filing
                        break

            if not target_filing:
                filings_by_form = company.get_filings(form=doc_type).latest(20)
                if filings_by_form:
                    for filing in filings_by_form:
                        if filing.accession_no == accession_number:
                            target_filing = filing
                            break

            if not target_filing:
                return self._no_data_result(
                    reason=f"SEC filing not found for ticker={ticker}, accession={accession_number}",
                    details={"ticker": ticker, "doc_id": doc_id, "doc_type": doc_type},
                    suggested_reroute="Adjust accession/doc_type or fetch filing lists first.",
                )

            full_markdown = f"# {doc_type} Filing: {ticker} ({target_filing.filing_date})\n\n"
            try:
                main_content = target_filing.markdown()
                if main_content:
                    full_markdown += "## Main Document\n\n" + main_content + "\n\n"
            except Exception as e:
                self.logger.warning(f"Failed to convert main document: {e}")

            if target_filing.attachments:
                self.logger.info(f"Processing attachments for {accession_number}...")
                has_attachments = False
                for attachment in target_filing.attachments:
                    doc_type_upper = (attachment.document_type or "").upper()
                    desc_upper = (attachment.description or "").upper()
                    is_relevant = (
                        doc_type_upper.startswith("EX-99")
                        or "PRESS RELEASE" in desc_upper
                        or "EARNINGS" in desc_upper
                        or "ANNOUNCEMENT" in desc_upper
                        or "RESULTS" in desc_upper
                    )
                    if not is_relevant:
                        continue
                    try:
                        att_content = attachment.markdown()
                        if att_content:
                            if not has_attachments:
                                full_markdown += "---\n\n# Attachments\n\n"
                                has_attachments = True
                            full_markdown += (
                                f"## Attachment: {attachment.document_type} - {attachment.description or ''}\n\n"
                            )
                            full_markdown += att_content + "\n\n"
                    except Exception as e:
                        self.logger.warning(f"Failed to convert attachment {attachment.document_type}: {e}")

            if not full_markdown.strip():
                return self._no_data_result(
                    reason="edgartools returned empty content for SEC filing",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Try get_filing_markdown or alternate filing within the same period.",
                )

            object_name = f"processed/{doc_type}/{doc_id}.md"
            storage_path = await self.minio_client.upload_bytes(
                object_name,
                full_markdown.encode("utf-8"),
                "text/markdown",
            )

            return {
                "status": "ok",
                "doc_id": doc_id,
                "type": "text",
                "url": url,
                "storage_path": storage_path,
                "message": "SEC filing (including attachments) processed via edgartools and uploaded.",
            }

        except Exception as e:
            self.logger.error(f"Process document failed for {doc_id}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return self._error_result(
                code="INTERNAL_ERROR",
                message=str(e),
                details={"doc_id": doc_id, "ticker": ticker, "doc_type": doc_type},
                retriable=False,
            )

    async def get_filing_markdown(
        self,
        ticker: str,
        doc_id: str,
    ) -> Dict[str, Any]:
        """Get SEC filing content as Markdown with MinIO caching.
        
        This method:
        1. Checks if the Markdown is already cached in MinIO
        2. If cached, returns the cached content
        3. If not cached, fetches from SEC using edgartools, caches, and returns
        
        Args:
            ticker: Stock ticker (e.g., 'AAPL' or 'NASDAQ:AAPL')
            doc_id: SEC Accession Number (e.g., '0000320193-25-000079')
            
        Returns:
            Dict with 'content' (Markdown string), 'cached' (bool), and 'status'
        """
        if self._looks_a_share_ticker(ticker):
            return self._error_result(
                code="INVALID_ROUTE",
                message="get_filing_markdown is US SEC-only; A-share ticker is not supported.",
                details={"ticker": ticker, "doc_id": doc_id},
                retriable=False,
                suggested_reroute="Use A-share announcement/news tools for CN filings.",
            )
        if not self._is_us_ticker(ticker):
            return self._error_result(
                code="INVALID_ROUTE",
                message="get_filing_markdown only supports US tickers.",
                details={"ticker": ticker, "doc_id": doc_id},
                retriable=False,
                suggested_reroute="Provide a valid US ticker (e.g., AAPL, NASDAQ:NVDA).",
            )

        # Normalize doc_id (remove potential prefix)
        accession_number = doc_id.replace("SEC:", "")

        # Build cache key path in MinIO
        pure_symbol = self._extract_symbol(ticker)
        cache_object_name = f"cache/markdown/{pure_symbol}/{accession_number}.md"
        
        try:
            # 1. Check MinIO cache first
            if self.minio_client:
                exists = await self.minio_client.object_exists(cache_object_name)
                if exists:
                    self.logger.info(f"✅ Cache HIT for {ticker}/{doc_id}")
                    cached_bytes = await self.minio_client.download_bytes(cache_object_name)
                    if cached_bytes:
                        return {
                            "status": "ok",
                            "cached": True,
                            "content": cached_bytes.decode("utf-8"),
                            "doc_id": doc_id,
                            "ticker": ticker,
                        }
            
            self.logger.info(f"📥 Cache MISS for {ticker}/{doc_id}, fetching from SEC...")
            
            # 2. Fetch from SEC using edgartools via sec_utils
            from src.server.utils.sec_utils import get_company
            
            company = get_company(pure_symbol)
            
            # Search for the filing by accession number
            filings = company.get_filings().latest(50)
            
            target_filing = None
            if filings:
                for filing in filings:
                    if filing.accession_no == accession_number:
                        target_filing = filing
                        break
            
            if not target_filing:
                # Try broader search
                filings_all = company.get_filings().latest(100)
                if filings_all:
                    for filing in filings_all:
                        if filing.accession_no == accession_number:
                            target_filing = filing
                            break
            
            if not target_filing:
                return self._no_data_result(
                    reason=f"SEC filing not found: {doc_id} for {ticker}",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Adjust doc_id/time window or fetch filing list first.",
                )
            
            # 3. Convert to Markdown
            self.logger.info(f"🔄 Converting {accession_number} to Markdown...")
            
            doc_type = target_filing.form or "SEC"
            full_markdown = f"# {doc_type} Filing: {ticker} ({target_filing.filing_date})\n\n"
            
            # Main document
            try:
                main_content = target_filing.markdown()
                if main_content:
                    full_markdown += "## Main Document\n\n" + main_content + "\n\n"
            except Exception as e:
                self.logger.warning(f"Failed to convert main document: {e}")
            
            # Process Attachments (important for 8-K/6-K)
            if target_filing.attachments:
                self.logger.info(f"Processing attachments for {accession_number}...")
                has_attachments = False
                
                for attachment in target_filing.attachments:
                    doc_type_upper = (attachment.document_type or "").upper()
                    desc_upper = (attachment.description or "").upper()
                    
                    is_relevant = (
                        doc_type_upper.startswith("EX-99") or 
                        "PRESS RELEASE" in desc_upper or 
                        "EARNINGS" in desc_upper or
                        "ANNOUNCEMENT" in desc_upper or
                        "RESULTS" in desc_upper
                    )
                    
                    if is_relevant:
                        try:
                            att_content = attachment.markdown()
                            if att_content:
                                if not has_attachments:
                                    full_markdown += "---\n\n# Attachments\n\n"
                                    has_attachments = True
                                    
                                full_markdown += f"## Attachment: {attachment.document_type} - {attachment.description or ''}\n\n"
                                full_markdown += att_content + "\n\n"
                                self.logger.info(f"Included attachment: {attachment.document_type}")
                        except Exception as e:
                            self.logger.warning(f"Failed to convert attachment {attachment.document_type}: {e}")
            
            if not full_markdown.strip():
                return self._no_data_result(
                    reason="edgartools returned empty content",
                    details={"ticker": ticker, "doc_id": doc_id},
                    suggested_reroute="Try another filing or use filing sections/chunks as fallback.",
                )
            
            # 4. Cache to MinIO
            if self.minio_client:
                try:
                    await self.minio_client.upload_bytes(
                        cache_object_name,
                        full_markdown.encode('utf-8'),
                        "text/markdown"
                    )
                    self.logger.info(f"💾 Cached Markdown to MinIO: {cache_object_name}")
                except Exception as e:
                    self.logger.warning(f"Failed to cache to MinIO: {e}")
            
            return {
                "status": "ok",
                "cached": False,
                "content": full_markdown,
                "doc_id": doc_id,
                "ticker": ticker,
                "form_type": target_filing.form,
                "filing_date": str(target_filing.filing_date),
            }

        except Exception as e:
            self.logger.error(f"get_filing_markdown failed for {ticker}/{doc_id}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return self._error_result(
                code="INTERNAL_ERROR",
                message=str(e),
                details={"ticker": ticker, "doc_id": doc_id},
                retriable=False,
            )
