"""Chunking strategy for Form 20-F (Foreign Private Issuer Annual Report).

This strategy handles the unique characteristics of 20-F filings:
1. Different Item structure than 10-K (Item 3.D vs Item 1A).
2. "Shell" filings (like BABA) that primarily incorporate by reference.
3. Hybrid content (structured items + potential attachments).
"""

from typing import Generator, List, Optional

from src.server.domain.chunking.base import Chunk
from src.server.domain.chunking.strategies.tenk_strategy import TenKStrategy
from src.server.domain.sec_filing_schema import get_filing_schema
from src.server.utils.logger import logger


class TwentyFStrategy(TenKStrategy):
    """Strategy for 20-F filings with smart fallback."""
    
    @property
    def supported_forms(self) -> List[str]:
        return ["20-F"]
    
    def chunk(
        self,
        content: Optional[str],
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """
        Smart chunking for 20-F:
        1. Try structured parsing (ChunkedDocument).
        2. Check if the result is "thin" (e.g., just reference statements).
        3. If thin, fallback to full markdown or attachment search.
        """
        schema = get_filing_schema(filing.form)
        items_to_extract = items if items else schema.default_items
        
        # 1. Try structured chunking first (Standard 10-K logic)
        chunked_doc = self._get_chunked_document(filing)
        
        chunks_yielded = 0
        total_chars = 0
        
        if chunked_doc:
            # Buffer chunks to analyze quality before yielding
            buffered_chunks = []
            try:
                for chunk in self._chunk_from_dataframe(
                    chunked_doc, filing, ticker, items_to_extract, schema
                ):
                    buffered_chunks.append(chunk)
                    total_chars += len(chunk.text)
                
                chunks_yielded = len(buffered_chunks)
                logger.info(f"📊 20-F Structured parse: {chunks_yielded} chunks, {total_chars} chars")
                
            except Exception as e:
                logger.warning(f"20-F structured parsing failed: {e}")
                chunks_yielded = 0
        

        # 2. Quality Check: Is this a "Shell" filing?
        # Threshold: < 20000 chars suggests it might just be "Incorporated by reference" statements
        # A full 20-F is usually > 100k chars.
        is_thin_content = total_chars < 20000
        
        if chunks_yielded > 0 and not is_thin_content:
            # Good structured content, yield it
            yield from buffered_chunks
            return

        # 3. Fallback Logic
        if is_thin_content:
            logger.info(f"⚠️ 20-F content thin ({total_chars} chars). Likely 'Incorporation by Reference'. Switching to fallback.")
            
            # If we have some structured chunks, yield them first (they might contain useful metadata/signatures)
            if buffered_chunks:
                yield from buffered_chunks
            
            # Fallback 1: Try to find "Annual Report" attachment (like 6-K logic)
            # TODO: Implement attachment search if needed. For now, Markdown fallback is safer for 20-F.
            
            # Fallback 2: Full Markdown (Valuecell style)
            # This captures "unlabeled" text that might have been missed by Item filtering
            logger.info("🔄 Using Markdown fallback to capture full content...")
            yield from self._chunk_from_markdown(filing, ticker)
        else:
            # No structured content at all
            logger.warning("ChunkedDocument not available for 20-F, using markdown fallback")
            yield from self._chunk_from_markdown(filing, ticker)
