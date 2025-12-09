"""SEC Filing Chunking Strategy Pattern Implementation.

This module provides a flexible, extensible chunking architecture for SEC filings.
It uses the Strategy Pattern to handle different filing types (10-K, 10-Q, 6-K, 8-K, etc.)
with their unique processing requirements.

Architecture:
    ChunkingOrchestrator -> ChunkingStrategy (abstract)
                              ├── TenKStrategy (10-K, 10-Q, 20-F)
                              ├── SixKStrategy (6-K)
                              └── EightKStrategy (8-K)

Usage:
    from src.server.domain.chunking import ChunkingOrchestrator
    
    # Process a filing
    for chunk in ChunkingOrchestrator.process(filing, items=["Item 1A", "Item 7"]):
        print(chunk.text, chunk.metadata)
"""

from .base import Chunk, ChunkingStrategy
from .orchestrator import ChunkingOrchestrator

__all__ = [
    "Chunk",
    "ChunkingStrategy", 
    "ChunkingOrchestrator",
]
