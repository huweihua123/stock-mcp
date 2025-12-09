# src/server/domain/services/filings_service.py
"""FilingsService – fetches regulatory filings and announcements.
Uses AdapterManager to retrieve data from appropriate sources (Finnhub for SEC, Akshare for A-share).
Returns structured data (JSON).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.server.utils.logger import logger


class FilingsService:
    """Service for retrieving regulatory filings and announcements."""

    def __init__(self, adapter_manager, minio_client=None):
        self.adapter_manager = adapter_manager
        self.minio_client = minio_client
        self.logger = logger
        
        # Initialize edgartools identity and cache via sec_utils
        # This ensures consistent identity and avoids ticker.txt downloads
        from src.server.utils.sec_utils import get_company, get_cik_or_symbol
        
        # No need to call set_identity here, sec_utils handles it on import

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
        """Internal unified method to fetch filings via AdapterManager."""
        try:
            start = (
                datetime.strptime(start_date_str, "%Y-%m-%d")
                if start_date_str
                else None
            )
            end = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else None

            filings = await self.adapter_manager.get_filings(
                ticker, start, end, limit, filing_types
            )

            return filings

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
        """Process a single document: download, extract text, and upload to MinIO.
        
        Uses `edgartools` for SEC filings to ensure correct parsing of index pages and tables.
        
        Args:
            doc_id: Unique document identifier (Accession Number for SEC)
            url: Document URL
            doc_type: Document type (e.g., "10-K", "8-K")
            ticker: Stock ticker (Required for SEC filings to use edgartools)
            
        Returns:
            Dict with storage_path and status
        """
        if not self.minio_client:
            return {"error": "MinIO client not configured"}

        try:
            # Check if it is an SEC filing (based on doc_type or doc_id format)
            is_sec = doc_type in ["10-K", "10-Q", "8-K", "20-F", "6-K"] or "SEC" in doc_id or "edgar" in url
            
            if is_sec:
                if not ticker:
                    return {
                        "doc_id": doc_id,
                        "status": "failed",
                        "error": "Ticker is required for processing SEC filings with edgartools."
                    }
                
                # Extract pure symbol from EXCHANGE:SYMBOL format (e.g., 'NASDAQ:AAPL' -> 'AAPL')
                pure_symbol = self._extract_symbol(ticker)
                self.logger.info(f"Processing SEC filing for {ticker} (pure symbol: {pure_symbol})")
                
                # 1. Initialize Company using sec_utils (avoids ticker.txt download)
                from src.server.utils.sec_utils import get_company
                self.logger.info(f"Initializing Company for: {pure_symbol}")
                company = get_company(pure_symbol)
                
                # 2. Find the specific filing
                # edgartools doesn't support get_by_accession directly, so we fetch recent filings and filter
                # doc_id for SEC is usually the Accession Number (e.g., 0000320193-25-000079)
                accession_number = doc_id.replace("SEC:", "") # Handle potential prefix
                
                # Fetch a batch of filings (e.g., latest 50) to find the match
                # This is a trade-off: we assume the filing is relatively recent.
                # If not found, we might need a different strategy or larger limit.
                filings = company.get_filings().latest(50)
                
                target_filing = None
                if filings:
                    for filing in filings:
                        if filing.accession_no == accession_number:
                            target_filing = filing
                            break
                
                if not target_filing:
                     # Try fetching by form type if accession match failed (fallback)
                     filings_by_form = company.get_filings(form=doc_type).latest(20)
                     if filings_by_form:
                         for filing in filings_by_form:
                             if filing.accession_no == accession_number:
                                 target_filing = filing
                                 break
                
                if not target_filing:
                    return {
                        "doc_id": doc_id,
                        "status": "failed",
                        "error": f"Could not find filing with accession number {accession_number} for {ticker} using edgartools."
                    }
                
                # 3. Convert to Markdown using edgartools
                # This handles the index page resolution and table conversion automatically
                self.logger.info(f"Converting SEC filing {accession_number} to Markdown using edgartools...")
                
                # Start with main document
                full_markdown = f"# {doc_type} Filing: {ticker} ({target_filing.filing_date})\n\n"
                
                try:
                    main_content = target_filing.markdown()
                    if main_content:
                        full_markdown += "## Main Document\n\n" + main_content + "\n\n"
                except Exception as e:
                    self.logger.warning(f"Failed to convert main document: {e}")

                # Process Attachments (Crucial for 8-K/6-K)
                if target_filing.attachments:
                    self.logger.info(f"Processing attachments for {accession_number}...")
                    has_attachments = False
                    
                    for attachment in target_filing.attachments:
                        # Filter criteria for relevant attachments:
                        # 1. Document Type starts with EX-99 (Press Releases, Earnings)
                        # 2. Description contains keywords like "PRESS RELEASE", "EARNINGS", "ANNOUNCEMENT"
                        # 3. Avoid XML/XBRL/Graphics
                        
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
                     return {
                        "doc_id": doc_id,
                        "status": "failed",
                        "error": "edgartools returned empty content (main + attachments)."
                    }

                # 4. Upload to MinIO
                object_name = f"processed/{doc_type}/{doc_id}.md"
                storage_path = await self.minio_client.upload_bytes(
                    object_name, 
                    full_markdown.encode('utf-8'), 
                    "text/markdown"
                )
                
                return {
                    "doc_id": doc_id,
                    "type": "text",
                    "url": url,
                    "status": "success",
                    "storage_path": storage_path,
                    "message": "SEC filing (including attachments) processed via edgartools and uploaded."
                }

            else:
                # Non-SEC logic (e.g. PDF direct download for A-shares)
                import aiohttp
                
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "User-Agent": "ValueCell/1.0 (contact@valuecell.ai)",
                        "Accept": "*/*",
                    }
                    
                    async with session.get(url, headers=headers) as response:
                        if response.status != 200:
                            return {
                                "doc_id": doc_id,
                                "status": "failed",
                                "error": f"Download failed with status {response.status}"
                            }
                        
                        content_type = response.headers.get("Content-Type", "").lower()
                        content_bytes = await response.read()
                        
                        is_pdf = "pdf" in content_type or url.lower().endswith(".pdf")
                        
                        if is_pdf:
                            object_name = f"raw/{doc_type}/{doc_id}.pdf"
                            storage_path = await self.minio_client.upload_bytes(
                                object_name, 
                                content_bytes, 
                                "application/pdf"
                            )
                            return {
                                "doc_id": doc_id,
                                "type": "pdf",
                                "url": url,
                                "status": "success",
                                "storage_path": storage_path,
                                "message": "PDF uploaded to MinIO."
                            }
                        else:
                            # Fallback for non-SEC HTML (unlikely path given current scope)
                            return {
                                "doc_id": doc_id,
                                "status": "failed",
                                "error": "Unsupported document type for non-SEC source."
                            }

        except Exception as e:
            self.logger.error(f"Process document failed for {doc_id}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                "doc_id": doc_id,
                "status": "error",
                "error": str(e)
            }

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
                            "status": "success",
                            "cached": True,
                            "content": cached_bytes.decode('utf-8'),
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
                return {
                    "status": "error",
                    "cached": False,
                    "error": f"Filing not found: {doc_id} for {ticker}",
                    "doc_id": doc_id,
                    "ticker": ticker,
                }
            
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
                return {
                    "status": "error",
                    "cached": False,
                    "error": "edgartools returned empty content",
                    "doc_id": doc_id,
                    "ticker": ticker,
                }
            
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
                "status": "success",
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
            return {
                "status": "error",
                "cached": False,
                "error": str(e),
                "doc_id": doc_id,
                "ticker": ticker,
            }

