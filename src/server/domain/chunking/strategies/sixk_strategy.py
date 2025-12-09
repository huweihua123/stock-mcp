"""Chunking strategy for 6-K filings (Foreign Private Issuer Current Reports).

6-K filings are unique because they often contain only a cover page,
with the actual content in attachments (typically EX-99.1).

This strategy:
1. Extracts content from the primary attachment (EX-99.1 or similar)
2. Converts HTML to text using edgar.htmltools
3. Chunks the extracted content by paragraph
"""

from typing import Generator, List, Optional

from src.server.domain.chunking.base import Chunk, ChunkingStrategy
from src.server.utils.logger import logger


class SixKStrategy(ChunkingStrategy):
    """Strategy for 6-K filings (Foreign Private Issuer Current Reports).
    
    6-K filings for companies like BABA, JD, PDD typically have:
    - A cover page as the main document
    - Actual content (earnings, announcements) in EX-99.1 attachment
    
    Processing flow:
    1. extract(): Find and extract EX-99.1 attachment content
    2. chunk(): Split extracted content into paragraph-based chunks
    """
    
    # Priority order for attachment search
    ATTACHMENT_PRIORITIES = [
        "EX-99.1",
        "EX-99",
        "99.1",
        "EXHIBIT 99.1",
    ]
    
    # Keywords to match attachment descriptions
    CONTENT_KEYWORDS = [
        "PRESS RELEASE",
        "ANNOUNCEMENT", 
        "EARNINGS",
        "FINANCIAL RESULTS",
        "QUARTERLY RESULTS",
        "NEWS RELEASE",
    ]
    
    @property
    def supported_forms(self) -> List[str]:
        return ["6-K"]
    
    def extract(self, filing) -> Optional[str]:
        """Extract content from 6-K attachment (typically EX-99.1).
        
        Returns:
            Extracted text content from the attachment, or None if extraction fails.
        """
        try:
            # Get attachments - note: it's a lazy object, must convert to list
            attachments = list(filing.attachments)
            logger.info(f"📎 Found {len(attachments)} attachments for 6-K")
            
            if not attachments:
                logger.warning("No attachments found in 6-K filing")
                return None
            
            # Find the target attachment
            target_attachment = self._find_primary_attachment(attachments)
            
            if not target_attachment:
                logger.warning("Could not find suitable content attachment in 6-K")
                return None
            
            logger.info(f"📄 Selected attachment: {getattr(target_attachment, 'document_type', 'unknown')}")
            
            # Download and convert to text
            return self._extract_attachment_content(target_attachment)
            
        except Exception as e:
            logger.error(f"Failed to extract 6-K attachment: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _find_primary_attachment(self, attachments):
        """Find the primary content attachment using priority matching."""
        # Log all attachments for debugging
        for i, att in enumerate(attachments[:5]):  # Only log first 5
            doc_type = getattr(att, 'document_type', '')
            desc = getattr(att, 'description', '')
            doc = getattr(att, 'document', '')
            logger.debug(f"  Attachment[{i}]: type={doc_type}, desc={desc}, doc={doc}")
        
        # First pass: exact document type match (most reliable)
        for att in attachments:
            doc_type = getattr(att, 'document_type', '') or ''
            if doc_type == "EX-99.1":
                logger.info(f"📎 Found exact match: EX-99.1")
                return att
        
        # Second pass: document type contains match
        for priority in self.ATTACHMENT_PRIORITIES:
            for att in attachments:
                doc_type = getattr(att, 'document_type', '') or ''
                if priority.lower() in doc_type.lower():
                    logger.info(f"📎 Found by type pattern: {doc_type}")
                    return att
        
        # Third pass: description keyword match
        for att in attachments:
            description = getattr(att, 'description', '') or ''
            for keyword in self.CONTENT_KEYWORDS:
                if keyword.lower() in description.lower():
                    logger.info(f"📎 Found by description keyword: {description[:50]}")
                    return att
        
        # Fallback: return first non-cover attachment if available
        if len(attachments) > 1:
            logger.info("📎 Using fallback: second attachment (skip cover)")
            return attachments[1]  # Skip cover page
        elif attachments:
            logger.info("📎 Using fallback: first attachment only")
            return attachments[0]
        
        return None
    
    def _extract_attachment_content(self, attachment) -> Optional[str]:
        """Download attachment and convert HTML to text."""
        try:
            from edgar.htmltools import html_to_text
            
            # Download the attachment content
            raw_content = attachment.download()
            
            if not raw_content:
                logger.warning("Attachment download returned empty content")
                return None
            
            # Handle bytes response - decode to string first
            if isinstance(raw_content, bytes):
                html_content = raw_content.decode('utf-8', errors='ignore')
            else:
                html_content = str(raw_content)
            
            # Convert HTML to text
            text_content = html_to_text(html_content)
            
            if text_content and len(text_content.strip()) > 100:
                logger.info(f"✅ Extracted {len(text_content)} chars from attachment")
                return text_content
            else:
                logger.warning(f"Attachment content too short: {len(text_content) if text_content else 0} chars")
                return None
                
        except ImportError:
            logger.error("edgar.htmltools not available, cannot convert HTML")
            return None
        except Exception as e:
            logger.error(f"Failed to extract attachment content: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def chunk(
        self,
        content: Optional[str],
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Chunk 6-K content into semantic segments.
        
        Since 6-K doesn't have standardized Items like 10-K,
        we use paragraph-based chunking with special labels.
        """
        if content and content.strip():
            # We have attachment content - chunk it
            yield from self._fallback_chunk(
                text=content,
                filing=filing,
                ticker=ticker,
                item="6-K-attachment",
                item_name="6-K Attachment Content",
                source="attachment",
            )
        else:
            # Try fallback to main document markdown
            logger.warning("No attachment content, falling back to main document")
            yield from self._fallback_to_markdown(filing, ticker)
    
    def _fallback_to_markdown(
        self,
        filing,
        ticker: str,
    ) -> Generator[Chunk, None, None]:
        """Fallback: try to get content from main document markdown."""
        try:
            markdown_content = filing.markdown()
            if markdown_content and len(markdown_content.strip()) > 200:
                yield from self._fallback_chunk(
                    text=markdown_content,
                    filing=filing,
                    ticker=ticker,
                    item="6-K-main",
                    item_name="6-K Main Document",
                    source="markdown_fallback",
                )
            else:
                logger.warning("6-K main document also has insufficient content")
        except Exception as e:
            logger.error(f"6-K markdown fallback failed: {e}")
