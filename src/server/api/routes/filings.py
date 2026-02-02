from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.server.core.use_cases import filings as filings_use_cases


router = APIRouter(prefix="/filings", tags=["filings"])

# --- Request Models ---

class ProcessDocumentRequest(BaseModel):
    doc_id: str
    url: str
    doc_type: str
    ticker: Optional[str] = None

# --- Endpoints ---

@router.get("/sec/periodic")
async def get_periodic_sec_filings(
    ticker: str = Query(..., description="US stock ticker (e.g., AAPL)"),
    year: Optional[int] = Query(None, description="Fiscal year"),
    quarter: Optional[int] = Query(None, description="Fiscal quarter (1-4)"),
    forms: Optional[List[str]] = Query(None, description="Filing forms (e.g., 10-K, 10-Q)"),
    limit: int = Query(10, description="Max results when year is omitted"),
):
    """Fetch SEC periodic filings (10-K/10-Q)."""
    try:
        return await filings_use_cases.fetch_periodic_sec_filings(
            ticker=ticker,
            forms=forms,
            year=year,
            quarter=quarter,
            limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sec/event")
async def get_event_sec_filings(
    ticker: str = Query(..., description="US stock ticker (e.g., AAPL)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    forms: Optional[List[str]] = Query(None, description="Filing forms (e.g., 8-K)"),
    limit: int = Query(10, description="Max results"),
):
    """Fetch SEC event-driven filings (8-K, etc.)."""
    try:
        return await filings_use_cases.fetch_event_sec_filings(
            ticker=ticker,
            forms=forms,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ashare")
async def get_ashare_filings(
    symbol: str = Query(..., description="A-share symbol (e.g., 600519)"),
    filing_types: Optional[List[str]] = Query(None, description="Report types (annual, quarterly)"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(10, description="Max results"),
):
    """Fetch A-share announcements."""
    try:
        return await filings_use_cases.fetch_ashare_filings(
            symbol=symbol,
            filing_types=filing_types,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/process")
async def process_document(request: ProcessDocumentRequest):
    """Process a document (download, clean, upload to MinIO)."""
    try:
        result = await filings_use_cases.process_document(
            doc_id=request.doc_id,
            url=request.url,
            doc_type=request.doc_type,
            ticker=request.ticker
        )
        
        if result.get("status") == "failed" or result.get("status") == "error":
             raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
             
        return result
    except Exception as e:
        # If the service raised an exception directly
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/markdown")
async def get_filing_markdown(
    ticker: str = Query(..., description="Stock ticker (e.g., AAPL or NASDAQ:AAPL)"),
    doc_id: str = Query(..., description="SEC Accession Number (e.g., 0000320193-25-000079)"),
    stream: bool = Query(False, description="If true, return plain text Markdown; otherwise return JSON"),
):
    """Get SEC filing content as Markdown with caching.
    
    This endpoint:
    1. Checks MinIO cache for pre-converted Markdown
    2. If cached, returns immediately (fast path)
    3. If not cached, fetches from SEC, converts to Markdown, caches, and returns
    
    Use `stream=true` for plain text response (suitable for direct file download).
    """
    from fastapi.responses import PlainTextResponse
    
    try:
        result = await filings_use_cases.get_filing_markdown(
            ticker=ticker, doc_id=doc_id
        )
        
        if result.get("status") == "error":
            raise HTTPException(
                status_code=404 if "not found" in result.get("error", "").lower() else 500,
                detail=result.get("error", "Unknown error")
            )
        
        # Return plain text if stream=true
        if stream:
            return PlainTextResponse(
                content=result.get("content", ""),
                media_type="text/markdown",
                headers={
                    "X-Cached": str(result.get("cached", False)),
                    "X-Doc-Id": doc_id,
                    "X-Ticker": ticker,
                }
            )
        
        # Default: return JSON with metadata
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _chunk_to_text(chunk_obj) -> str:
    """Convert edgartools chunk to text string.
    
    edgartools ChunkedDocument.chunks_for_item() returns list[TextBlock].
    TextBlock has a .text property to get the actual content.
    """
    if isinstance(chunk_obj, str):
        return chunk_obj
    
    if isinstance(chunk_obj, list):
        # Join all TextBlocks in the list
        parts = []
        for elem in chunk_obj:
            # TextBlock has .text property
            if hasattr(elem, 'text'):
                text = elem.text.strip() if elem.text else ""
            else:
                text = str(elem).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)
    
    # Single TextBlock
    if hasattr(chunk_obj, 'text'):
        return chunk_obj.text.strip() if chunk_obj.text else ""
    
    # Fallback: just convert to string
    return str(chunk_obj)

@router.get("/chunks")
async def get_document_chunks_stream(
    ticker: str = Query(..., description="Stock ticker (e.g., AAPL or NASDAQ:AAPL)"),
    doc_id: str = Query(..., description="SEC Accession Number (e.g., 0000320193-25-000079)"),
    items: Optional[List[str]] = Query(None, description="Optional list of items to extract (e.g., Item 1A, Item 7)"),
):
    """Get SEC filing semantic chunks with item labels (Streaming NDJSON).
    
    Uses the Strategy Pattern via ChunkingOrchestrator to handle different filing types:
    - 10-K/10-Q/20-F: Structured item-based chunking
    - 6-K: Attachment extraction (EX-99.1)
    - 8-K: Event-driven section chunking
    
    Returns NDJSON (Newline Delimited JSON) format for streaming:
    - First line: {"type": "header", "doc_id": "...", "ticker": "...", "form": "...", "filing_date": "..."}
    - Chunk lines: {"type": "chunk", "text": "...", "metadata": {...}}
    - Last line: {"type": "footer", "chunks_count": N, "status": "success"}
    
    Args:
        ticker: Stock ticker
        doc_id: SEC Accession Number
        items: Optional list of items to extract. Defaults to important sections.
    """
    from fastapi.responses import StreamingResponse
    from src.server.utils.logger import logger
    from src.server.domain.chunking import ChunkingOrchestrator
    import json
    
    async def generate_chunks():
        """Generator for streaming NDJSON chunks using ChunkingOrchestrator."""
        try:
            # Extract pure symbol
            pure_symbol = ticker.split(":")[-1] if ":" in ticker else ticker
            accession_number = doc_id.replace("SEC:", "")
            
            logger.info(f"🔍 get_document_chunks (stream): ticker={pure_symbol}, doc_id={accession_number}")
            
            # Get the filing using edgartools via sec_utils (avoids ticker.txt download)
            from src.server.utils.sec_utils import get_company
            company = get_company(pure_symbol)
            filings = company.get_filings().latest(100)
            
            target_filing = None
            if filings:
                for filing in filings:
                    if filing.accession_no == accession_number:
                        target_filing = filing
                        break
            
            if not target_filing:
                yield json.dumps({
                    "type": "error",
                    "error": f"Filing not found: {accession_number} for {pure_symbol}"
                }) + "\n"
                return
            
            logger.info(f"📄 Found filing: {target_filing.form} dated {target_filing.filing_date}")
            
            # Normalize items parameter
            items_to_extract = None
            if items and len(items) > 0 and items[0]:
                items_to_extract = items
            
            # Use ChunkingOrchestrator with Strategy Pattern
            # This delegates to the appropriate strategy based on form type:
            # - TenKStrategy for 10-K/10-Q/20-F
            # - SixKStrategy for 6-K (attachment extraction)
            # - EightKStrategy for 8-K
            chunk_count = 0
            for item in ChunkingOrchestrator.process_with_header_footer(
                filing=target_filing,
                ticker=pure_symbol,
                items=items_to_extract,
            ):
                if item["type"] == "chunk":
                    chunk_count += 1
                yield json.dumps(item) + "\n"
            
            logger.info(f"✅ Streamed {chunk_count} chunks via ChunkingOrchestrator")
            
        except Exception as e:
            logger.error(f"Streaming chunks failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            yield json.dumps({
                "type": "error",
                "error": str(e)
            }) + "\n"
    
    return StreamingResponse(
        generate_chunks(),
        media_type="application/x-ndjson",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        }
    )


async def _api_fallback_chunking(filing, ticker: str, doc_id: str) -> dict:
    """Fallback chunking using markdown."""
    from src.server.utils.logger import logger
    
    try:
        markdown_content = filing.markdown()
        if not markdown_content:
            return {
                "status": "error",
                "error": "Empty markdown content",
            }
        
        paragraphs = markdown_content.split("\n\n")
        chunks = []
        
        TARGET_SIZE = 4000
        current_chunk = ""
        chunk_index = 0
        
        for para in paragraphs:
            if len(current_chunk) + len(para) > TARGET_SIZE and current_chunk:
                chunks.append({
                    "text": current_chunk.strip(),
                    "metadata": {
                        "ticker": ticker,
                        "doc_id": doc_id,
                        "form": filing.form,
                        "item": "fallback",
                        "item_name": "Fallback Chunking",
                        "filing_date": str(filing.filing_date),
                        "chunk_index": chunk_index,
                    }
                })
                chunk_index += 1
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para
        
        if current_chunk.strip():
            chunks.append({
                "text": current_chunk.strip(),
                "metadata": {
                    "ticker": ticker,
                    "doc_id": doc_id,
                    "form": filing.form,
                    "item": "fallback",
                    "item_name": "Fallback Chunking",
                    "filing_date": str(filing.filing_date),
                    "chunk_index": chunk_index,
                }
            })
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "ticker": ticker,
            "form": filing.form,
            "filing_date": str(filing.filing_date),
            "chunks_count": len(chunks),
            "chunks": chunks,
            "fallback": True,
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
