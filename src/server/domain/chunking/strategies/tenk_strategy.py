"""Chunking strategy for structured filings: 10-K, 10-Q, 20-F.

These filings have well-defined Item structures that edgartools can parse
into ChunkedDocument with DataFrame representation.
"""

from typing import Generator, List, Optional

from src.server.domain.chunking.base import Chunk, ChunkingStrategy
from src.server.domain.sec_filing_schema import get_filing_schema
from src.server.utils.logger import logger


class TenKStrategy(ChunkingStrategy):
    """Strategy for 10-K, 10-Q, and 20-F filings.
    
    These filings have standardized Item structures (Item 1, Item 1A, etc.)
    that can be parsed using edgartools' ChunkedDocument.
    
    Processing flow:
    1. extract() returns None (use structured parsing)
    2. chunk() uses edgartools ChunkedDocument.as_dataframe()
    3. Filters by specified items
    4. Falls back to markdown chunking if structured parsing fails
    """
    
    @property
    def supported_forms(self) -> List[str]:
        return ["10-K", "10-Q", "20-F"]
    
    def extract(self, filing) -> Optional[str]:
        """Returns None for structured filings - we use ChunkedDocument directly."""
        return None
    
    def chunk(
        self,
        content: Optional[str],
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Chunk using edgartools' structured document parsing.
        
        Uses the ChunkedDocument.as_dataframe() method to get pre-chunked
        content with Item labels.
        """
        schema = get_filing_schema(filing.form)
        items_to_extract = items if items else schema.default_items
        
        # Try structured chunking first
        chunked_doc = self._get_chunked_document(filing)
        
        if chunked_doc:
            yield from self._chunk_from_dataframe(
                chunked_doc, filing, ticker, items_to_extract, schema
            )
        else:
            # Fallback to markdown chunking
            logger.warning(f"ChunkedDocument not available for {filing.form}, using markdown fallback")
            yield from self._chunk_from_markdown(filing, ticker)
    
    def _get_chunked_document(self, filing):
        """Get ChunkedDocument from filing, handling errors gracefully."""
        try:
            filing_obj = filing.obj()
            if hasattr(filing_obj, 'doc') and filing_obj.doc is not None:
                return filing_obj.doc
        except Exception as e:
            logger.warning(f"Failed to get ChunkedDocument: {e}")
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
            logger.info(f"📊 DataFrame loaded: {len(df)} rows, columns: {df.columns.tolist()}")
            
            # Filter out empty chunks
            if 'Empty' in df.columns:
                df = df[~df['Empty']]
            
            # Filter by items if specified
            if 'Item' in df.columns and items_to_extract:
                df = df[df['Item'].isin(items_to_extract)]
            
            logger.info(f"📦 After filtering: {len(df)} chunks for items {items_to_extract}")
            
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
                        "is_signature": bool(row.get('Signature', False)),
                        "source": "structured",
                    }
                )
                chunk_index += 1
                
            logger.info(f"✅ Extracted {chunk_index} chunks from DataFrame")
            
        except Exception as e:
            logger.error(f"Failed to process DataFrame: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
                    item="fallback",
                    item_name="Fallback Chunking",
                    source="markdown_fallback",
                )
        except Exception as e:
            logger.error(f"Markdown fallback also failed: {e}")
