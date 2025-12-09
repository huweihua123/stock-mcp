"""Chunking strategy for 8-K filings (Current Reports).

8-K filings are event-driven and have a different Item numbering scheme
(Section X.XX format, e.g., Item 1.01, Item 2.02).

Common important items:
- Item 1.01: Material Agreements
- Item 1.05: Cybersecurity Incidents  
- Item 2.02: Results of Operations (Earnings)
- Item 5.02: Departure of Directors/Officers
"""

from typing import Generator, List, Optional

from src.server.domain.chunking.base import Chunk, ChunkingStrategy
from src.server.domain.sec_filing_schema import get_filing_schema
from src.server.utils.logger import logger


class EightKStrategy(ChunkingStrategy):
    """Strategy for 8-K filings (Current Reports).
    
    8-K filings have a section-based Item structure (X.XX format)
    that can still be parsed by edgartools.
    
    Processing flow:
    1. extract() returns None (use structured parsing)
    2. chunk() uses edgartools ChunkedDocument.as_dataframe()
    3. Filters by specified items (e.g., Item 2.02 for earnings)
    4. Falls back to markdown chunking if structured parsing fails
    """
    
    @property
    def supported_forms(self) -> List[str]:
        return ["8-K"]
    
    def extract(self, filing) -> Optional[str]:
        """Returns None for 8-K - we use ChunkedDocument when available."""
        return None
    
    def chunk(
        self,
        content: Optional[str],
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Chunk 8-K using structured parsing or markdown fallback."""
        schema = get_filing_schema(filing.form)
        items_to_extract = items if items else schema.default_items
        
        # Try structured chunking first
        chunked_doc = self._get_chunked_document(filing)
        
        if chunked_doc:
            yield from self._chunk_from_dataframe(
                chunked_doc, filing, ticker, items_to_extract, schema
            )
        else:
            # 8-K often works well with markdown fallback
            logger.info("ChunkedDocument not available for 8-K, using markdown")
            yield from self._chunk_from_markdown(filing, ticker)
    
    def _get_chunked_document(self, filing):
        """Get ChunkedDocument from filing, handling errors gracefully."""
        try:
            filing_obj = filing.obj()
            if hasattr(filing_obj, 'doc') and filing_obj.doc is not None:
                return filing_obj.doc
        except Exception as e:
            logger.warning(f"Failed to get ChunkedDocument for 8-K: {e}")
        return None
    
    def _chunk_from_dataframe(
        self,
        chunked_doc,
        filing,
        ticker: str,
        items_to_extract: List[str],
        schema,
    ) -> Generator[Chunk, None, None]:
        """Extract chunks from ChunkedDocument using DataFrame."""
        try:
            df = chunked_doc.as_dataframe()
            logger.info(f"📊 8-K DataFrame loaded: {len(df)} rows")
            
            # Filter out empty chunks
            if 'Empty' in df.columns:
                df = df[~df['Empty']]
            
            # Filter by items if specified
            # 8-K items have format like "Item 2.02"
            if 'Item' in df.columns and items_to_extract:
                df = df[df['Item'].isin(items_to_extract)]
            
            logger.info(f"📦 8-K: After filtering: {len(df)} chunks for items {items_to_extract}")
            
            chunk_index = 0
            for _, row in df.iterrows():
                text = row.get('Text', '') or ''
                if not text.strip():
                    continue
                
                item = row.get('Item', 'unknown')
                
                yield Chunk(
                    text=text.strip(),
                    metadata={
                        "ticker": ticker,
                        "doc_id": filing.accession_no,
                        "form": filing.form,
                        "item": item,
                        "item_name": schema.mapping.get(item, item),
                        "filing_date": str(filing.filing_date),
                        "chunk_index": chunk_index,
                        "is_table": bool(row.get('Table', False)),
                        "source": "structured",
                    }
                )
                chunk_index += 1
                
            logger.info(f"✅ Extracted {chunk_index} chunks from 8-K DataFrame")
            
        except Exception as e:
            logger.error(f"Failed to process 8-K DataFrame: {e}")
            # Fall back to markdown
            yield from self._chunk_from_markdown(filing, ticker)
    
    def _chunk_from_markdown(
        self,
        filing,
        ticker: str,
    ) -> Generator[Chunk, None, None]:
        """Fallback: chunk from markdown content."""
        try:
            markdown_content = filing.markdown()
            if markdown_content:
                yield from self._fallback_chunk(
                    text=markdown_content,
                    filing=filing,
                    ticker=ticker,
                    item="8-K-content",
                    item_name="8-K Content",
                    source="markdown_fallback",
                )
        except Exception as e:
            logger.error(f"8-K markdown fallback failed: {e}")
