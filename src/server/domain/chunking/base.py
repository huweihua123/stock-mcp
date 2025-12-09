"""Base classes for chunking strategies.

This module defines the core abstractions:
- Chunk: A dataclass representing a text segment with metadata
- ChunkingStrategy: Abstract base class for all chunking strategies
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


@dataclass
class Chunk:
    """Represents a document chunk with rich metadata.
    
    Attributes:
        text: The actual text content of this chunk
        metadata: Dictionary containing contextual information:
            - ticker: Stock symbol (e.g., "AAPL")
            - doc_id: SEC Accession Number
            - form: Filing type (e.g., "10-K", "6-K")
            - item: Item identifier (e.g., "Item 1A", "6-K-attachment")
            - item_name: Human-readable item name (e.g., "Risk Factors")
            - filing_date: Date of the filing
            - chunk_index: Sequential index within the document
            - is_table: Whether this chunk contains tabular data
            - char_count: Character count of the text
            - source: Source of the chunk (e.g., "structured", "attachment", "fallback")
    """
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Ensure metadata has default values."""
        defaults = {
            "ticker": "",
            "doc_id": "",
            "form": "",
            "item": "unknown",
            "item_name": "Unknown",
            "filing_date": "",
            "chunk_index": 0,
            "is_table": False,
            "char_count": len(self.text) if self.text else 0,
            "source": "unknown",
        }
        for key, value in defaults.items():
            if key not in self.metadata:
                self.metadata[key] = value
        
        # Update char_count based on actual text
        self.metadata["char_count"] = len(self.text) if self.text else 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for JSON serialization."""
        return {
            "type": "chunk",
            "text": self.text,
            "metadata": self.metadata,
        }


class ChunkingStrategy(ABC):
    """Abstract base class for document chunking strategies.
    
    Each strategy is responsible for:
    1. extract(): Extracting processable content from a Filing
    2. chunk(): Breaking the content into semantic chunks
    
    Different filing types (10-K, 6-K, 8-K, etc.) have different structures
    and require different extraction/chunking logic.
    """
    
    # Target size for fallback paragraph-based chunking
    TARGET_CHUNK_SIZE = 4000
    
    @property
    @abstractmethod
    def supported_forms(self) -> List[str]:
        """Return list of form types this strategy handles (e.g., ["10-K", "10-Q"])."""
        pass
    
    @abstractmethod
    def extract(self, filing) -> Optional[str]:
        """Extract content from a Filing for chunking.
        
        For structured filings (10-K, 10-Q): Returns None, using edgartools' 
        ChunkedDocument for structured parsing.
        
        For attachment-based filings (6-K): Returns the extracted attachment 
        content as text.
        
        Args:
            filing: An edgartools Filing object
            
        Returns:
            Optional[str]: Extracted text content, or None if using structured parsing
        """
        pass
    
    @abstractmethod
    def chunk(
        self,
        content: Optional[str],
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Chunk the content into semantic segments.
        
        Args:
            content: Content from extract() (may be None for structured filings)
            filing: Original Filing object for metadata
            ticker: Stock ticker symbol
            items: Optional list of items to filter (e.g., ["Item 1A", "Item 7"])
            
        Yields:
            Chunk objects with text and metadata
        """
        pass
    
    def process(
        self,
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Main entry point: extract then chunk.
        
        This is a template method that orchestrates the extract/chunk flow.
        
        Args:
            filing: An edgartools Filing object
            ticker: Stock ticker symbol
            items: Optional list of items to filter
            
        Yields:
            Chunk objects
        """
        content = self.extract(filing)
        yield from self.chunk(content, filing, ticker, items)
    
    def _fallback_chunk(
        self,
        text: str,
        filing,
        ticker: str,
        item: str = "fallback",
        item_name: str = "Fallback Chunking",
        source: str = "fallback",
    ) -> Generator[Chunk, None, None]:
        """Fallback paragraph-based chunking.
        
        Used when structured parsing fails or is not available.
        Splits text by double newlines and groups into target-sized chunks.
        
        Args:
            text: Full text to chunk
            filing: Filing object for metadata
            ticker: Stock ticker
            item: Item label to use
            item_name: Human-readable item name
            source: Source identifier
            
        Yields:
            Chunk objects
        """
        if not text or not text.strip():
            return
            
        paragraphs = text.split("\n\n")
        current_chunk = ""
        chunk_index = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
                
            # Check if adding this paragraph would exceed target size
            if len(current_chunk) + len(para) > self.TARGET_CHUNK_SIZE and current_chunk:
                yield Chunk(
                    text=current_chunk.strip(),
                    metadata={
                        "ticker": ticker,
                        "doc_id": getattr(filing, 'accession_no', ''),
                        "form": getattr(filing, 'form', ''),
                        "item": item,
                        "item_name": item_name,
                        "filing_date": str(getattr(filing, 'filing_date', '')),
                        "chunk_index": chunk_index,
                        "is_table": False,
                        "source": source,
                    }
                )
                chunk_index += 1
                current_chunk = para
            else:
                current_chunk = current_chunk + "\n\n" + para if current_chunk else para
        
        # Emit final chunk
        if current_chunk.strip():
            yield Chunk(
                text=current_chunk.strip(),
                metadata={
                    "ticker": ticker,
                    "doc_id": getattr(filing, 'accession_no', ''),
                    "form": getattr(filing, 'form', ''),
                    "item": item,
                    "item_name": item_name,
                    "filing_date": str(getattr(filing, 'filing_date', '')),
                    "chunk_index": chunk_index,
                    "is_table": False,
                    "source": source,
                }
            )
