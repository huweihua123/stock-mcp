from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from src.server.domain.chunking import ChunkingOrchestrator
from src.server.utils.sec_utils import get_company

_A_SHARE_PATTERN = re.compile(r"^(?:\d{6}|(?:SH|SZ)\d{6}|\d{6}\.(?:SH|SZ))$", re.IGNORECASE)
_US_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_CN_PREFIXES = ("SSE:", "SZSE:", "SH:", "SZ:")
_US_PREFIXES = ("NASDAQ:", "NYSE:", "AMEX:", "OTC:")


def looks_a_share_symbol(symbol: str | None) -> bool:
    token = str(symbol or "").strip().upper()
    if not token:
        return False
    if token.startswith(_CN_PREFIXES):
        return True
    if token.endswith((".SH", ".SZ")):
        return True
    if _A_SHARE_PATTERN.match(token):
        return True
    if ":" in token:
        token = token.split(":", 1)[1]
    return token.isdigit() and len(token) == 6


def is_us_symbol(symbol: str | None) -> bool:
    token = str(symbol or "").strip().upper()
    if not token:
        return False
    if looks_a_share_symbol(token):
        return False
    if ":" in token:
        return token.startswith(_US_PREFIXES)
    return bool(_US_SYMBOL_PATTERN.match(token))


def normalize_symbol(symbol: str) -> str:
    return symbol.split(":")[-1] if ":" in symbol else symbol


def fetch_filing_by_accession(symbol: str, doc_id: str):
    pure_symbol = normalize_symbol(symbol)
    accession_number = doc_id.replace("SEC:", "")
    company = get_company(pure_symbol)
    filings = company.get_filings().latest(100)

    target_filing = None
    if filings:
        for filing in filings:
            if filing.accession_no == accession_number:
                target_filing = filing
                break
    return pure_symbol, accession_number, target_filing


def build_chunk_payload(filing, ticker: str, doc_id: str, items: list[str] | None = None) -> dict[str, Any]:
    pure_symbol = normalize_symbol(ticker)
    accession_number = doc_id.replace("SEC:", "")
    chunks = []
    for chunk in ChunkingOrchestrator.process(filing=filing, ticker=pure_symbol, items=items):
        chunks.append(chunk.to_dict())
    return {
        "status": "ok",
        "doc_id": accession_number,
        "ticker": pure_symbol,
        "form": getattr(filing, "form", ""),
        "filing_date": str(getattr(filing, "filing_date", "")),
        "chunks_count": len(chunks),
        "chunks": chunks,
    }


async def iter_ndjson_chunks(filing, ticker: str, items: list[str] | None = None) -> AsyncIterator[str]:
    pure_symbol = normalize_symbol(ticker)
    for item in ChunkingOrchestrator.process_with_header_footer(
        filing=filing,
        ticker=pure_symbol,
        items=items,
    ):
        yield json.dumps(item) + "\n"
