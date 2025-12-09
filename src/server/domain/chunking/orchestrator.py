"""Chunking Orchestrator - Central coordinator for SEC filing chunking.

The orchestrator is responsible for:
1. Selecting the appropriate strategy based on form type
2. Invoking the strategy's process method
3. Providing a unified interface for the API/MCP layers

Usage:
    from src.server.domain.chunking import ChunkingOrchestrator
    
    # Get all chunks for a filing
    for chunk in ChunkingOrchestrator.process(filing, ticker="AAPL"):
        print(chunk.text)
    
    # Get specific items only
    for chunk in ChunkingOrchestrator.process(
        filing, 
        ticker="AAPL", 
        items=["Item 1A", "Item 7"]
    ):
        print(chunk.metadata["item"])
"""

from typing import Dict, Generator, List, Optional, Type

from src.server.domain.chunking.base import Chunk, ChunkingStrategy

from src.server.domain.chunking.strategies import (
    TenKStrategy,
    SixKStrategy,
    EightKStrategy,
    TwentyFStrategy,
)
from src.server.utils.logger import logger


class ChunkingOrchestrator:
    """Central coordinator for SEC filing chunking.
    
    Uses the Strategy Pattern to route filings to appropriate handlers
    based on their form type.
    
    Strategy mapping:
        - 10-K, 10-Q → TenKStrategy
        - 20-F → TwentyFStrategy
        - 6-K → SixKStrategy  
        - 8-K → EightKStrategy
        - Unknown → TenKStrategy (default fallback)
    """
    
    # Singleton strategy instances
    _strategy_instances: Dict[str, ChunkingStrategy] = {}
    
    # Form type to strategy class mapping
    _strategy_mapping: Dict[str, Type[ChunkingStrategy]] = {
        "10-K": TenKStrategy,
        "10-Q": TenKStrategy,
        "20-F": TwentyFStrategy,
        "6-K": SixKStrategy,
        "8-K": EightKStrategy,
    }
    
    # Default strategy for unknown form types
    _default_strategy_class: Type[ChunkingStrategy] = TenKStrategy
    
    @classmethod
    def _get_strategy_instance(cls, form_type: str) -> ChunkingStrategy:
        """Get or create a strategy instance for the given form type.
        
        Strategies are cached as singletons for efficiency.
        """
        # Normalize form type: "10-K/A" -> "10-K"
        base_form = form_type.upper().split('/')[0].strip() if form_type else ""
        
        # Get strategy class
        strategy_class = cls._strategy_mapping.get(base_form, cls._default_strategy_class)
        
        # Get or create instance
        class_name = strategy_class.__name__
        if class_name not in cls._strategy_instances:
            cls._strategy_instances[class_name] = strategy_class()
            logger.debug(f"Created new strategy instance: {class_name}")
        
        return cls._strategy_instances[class_name]
    
    @classmethod
    def get_strategy(cls, form_type: str) -> ChunkingStrategy:
        """Get the appropriate strategy for a form type.
        
        Args:
            form_type: SEC form type (e.g., "10-K", "6-K", "8-K/A")
            
        Returns:
            ChunkingStrategy instance
        """
        return cls._get_strategy_instance(form_type)
    
    @classmethod
    def process(
        cls,
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[Chunk, None, None]:
        """Main entry point: process a filing and yield chunks.
        
        This method:
        1. Determines the filing's form type
        2. Selects the appropriate chunking strategy
        3. Delegates to the strategy's process method
        
        Args:
            filing: An edgartools Filing object
            ticker: Stock ticker symbol
            items: Optional list of items to filter (e.g., ["Item 1A", "Item 7"])
            
        Yields:
            Chunk objects with text and metadata
            
        Example:
            from edgar import Company
            
            company = Company("AAPL")
            filing = company.get_filings(form="10-K").latest(1)[0]
            
            for chunk in ChunkingOrchestrator.process(filing, ticker="AAPL"):
                print(f"[{chunk.metadata['item']}] {chunk.text[:100]}...")
        """
        form_type = getattr(filing, 'form', '') or ''
        logger.info(f"🎯 ChunkingOrchestrator: Processing {form_type} for {ticker}")
        
        strategy = cls.get_strategy(form_type)
        logger.info(f"📋 Selected strategy: {strategy.__class__.__name__}")
        
        chunk_count = 0
        for chunk in strategy.process(filing, ticker, items):
            chunk_count += 1
            yield chunk
        
        logger.info(f"✅ ChunkingOrchestrator: Yielded {chunk_count} chunks")
    
    @classmethod
    def process_with_header_footer(
        cls,
        filing,
        ticker: str,
        items: Optional[List[str]] = None,
    ) -> Generator[dict, None, None]:
        """Process filing and yield NDJSON-compatible dictionaries.
        
        This is a convenience method for streaming responses that includes
        header and footer objects.
        
        Yields:
            - First: {"type": "header", "doc_id": "...", "ticker": "...", ...}
            - Middle: {"type": "chunk", "text": "...", "metadata": {...}}
            - Last: {"type": "footer", "chunks_count": N, "status": "success"}
        """
        doc_id = getattr(filing, 'accession_no', '')
        form_type = getattr(filing, 'form', '')
        filing_date = str(getattr(filing, 'filing_date', ''))
        
        # Yield header
        yield {
            "type": "header",
            "doc_id": doc_id,
            "ticker": ticker,
            "form": form_type,
            "filing_date": filing_date,
        }
        
        # Yield chunks
        chunk_count = 0
        for chunk in cls.process(filing, ticker, items):
            chunk_count += 1
            yield chunk.to_dict()
        
        # Yield footer
        yield {
            "type": "footer",
            "chunks_count": chunk_count,
            "status": "success",
        }
    
    @classmethod
    def register_strategy(cls, form_type: str, strategy_class: Type[ChunkingStrategy]):
        """Register a custom strategy for a form type.
        
        This allows extensions without modifying core code.
        
        Args:
            form_type: Form type to register (e.g., "DEF 14A")
            strategy_class: ChunkingStrategy subclass
            
        Example:
            class ProxyStrategy(ChunkingStrategy):
                ...
            
            ChunkingOrchestrator.register_strategy("DEF 14A", ProxyStrategy)
        """
        cls._strategy_mapping[form_type.upper()] = strategy_class
        logger.info(f"Registered strategy {strategy_class.__name__} for {form_type}")
    
    @classmethod
    def list_supported_forms(cls) -> List[str]:
        """Return list of all supported form types."""
        return list(cls._strategy_mapping.keys())
