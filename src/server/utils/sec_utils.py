
"""SEC Utilities for edgartools integration.

This module handles:
1. SEC Identity configuration (User-Agent).
2. Local Ticker-to-CIK cache loading (to avoid downloading ticker.txt).
3. Unified Company object creation.
"""

import os
import json
from pathlib import Path
from typing import Dict, Optional
from edgar import Company, set_identity
from src.server.utils.logger import logger

# Global cache for Ticker -> CIK mapping
_TICKER_TO_CIK: Dict[str, str] = {
    # Built-in fallback for major tech companies
    "BABA": "0001577552",
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOG": "0001652044",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "TSLA": "0001318605",
    "NVDA": "0001045810",
    "META": "0001326801",
    "NFLX": "0001065280",
}

_IDENTITY_SET = False

def _ensure_identity():
    """Ensure SEC identity is set from environment variables."""
    global _IDENTITY_SET
    if _IDENTITY_SET:
        return

    sec_email = os.getenv("SEC_EMAIL")
    if sec_email:
        set_identity(sec_email)
        logger.info(f"✅ SEC Identity set: {sec_email}")
    else:
        # Fallback identity (should be avoided in production)
        default_identity = "ValueCellAgent contact@valuecell.ai"
        set_identity(default_identity)
        logger.warning(f"⚠️ SEC_EMAIL not found, using default identity: {default_identity}")
    
    _IDENTITY_SET = True

def _load_local_ticker_cache():
    """Load Ticker to CIK mapping from local ~/.edgar/company_tickers.json"""
    global _TICKER_TO_CIK
    
    cache_path = Path.home() / ".edgar" / "company_tickers.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # company_tickers.json structure: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
                count = 0
                for item in data.values():
                    ticker = item.get("ticker")
                    cik = item.get("cik_str")
                    if ticker and cik:
                        # Ensure CIK is 10 digits string
                        cik_str = str(cik).zfill(10)
                        _TICKER_TO_CIK[ticker.upper()] = cik_str
                        count += 1
                logger.info(f"✅ Loaded {count} tickers from local cache: {cache_path}")
        except Exception as e:
            logger.warning(f"Failed to load local ticker cache: {e}")
    else:
        logger.info("ℹ️ Local ticker cache not found, using built-in fallback.")

# Initialize cache on module load
_ensure_identity()
_load_local_ticker_cache()

def get_cik_or_symbol(ticker: str) -> str:
    """Get CIK if available in local mapping, otherwise return pure symbol.
    
    Args:
        ticker: Stock ticker (e.g., 'AAPL', 'NASDAQ:AAPL')
        
    Returns:
        CIK string (10 digits) or pure symbol
    """
    # Extract pure symbol
    if ':' in ticker:
        _, symbol = ticker.split(':', 1)
    else:
        symbol = ticker
        
    symbol = symbol.upper()
    
    # Check cache
    return _TICKER_TO_CIK.get(symbol, symbol)

def get_company(ticker: str) -> Company:
    """Get edgar.Company object using CIK optimization.
    
    This is the recommended way to create Company objects to avoid 
    unnecessary 'ticker.txt' downloads from SEC.
    
    Args:
        ticker: Stock ticker (e.g., 'AAPL', 'NASDAQ:AAPL')
        
    Returns:
        edgar.Company object initialized with CIK (if found) or Ticker
    """
    identifier = get_cik_or_symbol(ticker)
    if identifier != ticker and identifier.isdigit():
        logger.debug(f"Creating Company with CIK: {identifier} (derived from {ticker})")
    else:
        logger.debug(f"Creating Company with Symbol: {identifier}")
        
    return Company(identifier)
