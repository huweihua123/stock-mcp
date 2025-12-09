from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.server.core.dependencies import Container
from src.server.domain.services.filings_service import FilingsService
from src.server.domain.sec_filing_schema import get_filing_schema

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
    service: FilingsService = Container.filings_service()
    try:
        return await service.fetch_periodic_sec_filings(
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
    service: FilingsService = Container.filings_service()
    try:
        return await service.fetch_event_sec_filings(
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
    service: FilingsService = Container.filings_service()
    try:
        return await service.fetch_ashare_filings(
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
    service: FilingsService = Container.filings_service()
    try:
        result = await service.process_document(
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
    
    service: FilingsService = Container.filings_service()
    try:
        result = await service.get_filing_markdown(ticker=ticker, doc_id=doc_id)
        
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
    
    Uses edgartools' ChunkedDocument for intelligent chunking based on document structure.
    Each chunk includes rich metadata for precise RAG filtering.
    
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
    from edgar import Company, set_identity
    from src.server.utils.logger import logger
    import json
    
    set_identity("ValueCell Agent <contact@valuecell.ai>")
    
    async def generate_chunks():
        """Generator for streaming NDJSON chunks."""
        try:
            # Extract pure symbol
            pure_symbol = ticker.split(":")[-1] if ":" in ticker else ticker
            accession_number = doc_id.replace("SEC:", "")
            
            logger.info(f"🔍 get_document_chunks (stream): ticker={pure_symbol}, doc_id={accession_number}")
            
            # Get the filing using edgartools
            company = Company(pure_symbol)
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
            
            # Send header first
            yield json.dumps({
                "type": "header",
                "doc_id": accession_number,
                "ticker": pure_symbol,
                "form": target_filing.form,
                "filing_date": str(target_filing.filing_date)
            }) + "\n"
            
            # Get ChunkedDocument
            chunked_doc = None
            try:
                filing_obj = target_filing.obj()
                if hasattr(filing_obj, 'doc') and filing_obj.doc is not None:
                    chunked_doc = filing_obj.doc
            except Exception as e:
                logger.warning(f"Failed to get ChunkedDocument: {e}")
            
            # Get Schema based on form type
            schema = get_filing_schema(target_filing.form)
            item_names = schema.mapping
            
            total_chunk_index = 0
            
            if chunked_doc:
                # 使用 as_dataframe() 获取带完整标签的 chunks
                try:
                    df = chunked_doc.as_dataframe()
                    logger.info(f"📊 DataFrame loaded: {len(df)} rows, columns: {df.columns.tolist()}")
                    
                    # 定义要过滤的 items
                    items_to_extract = items if items and len(items) > 0 and items[0] else schema.default_items
                    
                    # 过滤 DataFrame
                    # 1. 过滤掉 Empty 的 chunks
                    # 2. 只保留指定的 Items（如果有指定）
                    if 'Empty' in df.columns:
                        df = df[~df['Empty']]
                    
                    if 'Item' in df.columns and items_to_extract:
                        df = df[df['Item'].isin(items_to_extract)]
                    
                    logger.info(f"📦 After filtering: {len(df)} chunks for items {items_to_extract}")
                    
                    # 遍历 DataFrame 流式输出
                    for idx, row in df.iterrows():
                        # 获取文本内容
                        text = row.get('Text', '') or ''
                        if not text.strip():
                            continue
                        
                        # 获取 Item 标签
                        item = row.get('Item', 'unknown')
                        
                        # 构建丰富的元数据
                        metadata = {
                            "ticker": pure_symbol,
                            "doc_id": accession_number,
                            "form": target_filing.form,
                            "item": item,
                            "item_name": item_names.get(item, item),
                            "filing_date": str(target_filing.filing_date),
                            "chunk_index": total_chunk_index,
                            # 额外的标签信息
                            "is_table": bool(row.get('Table', False)),
                            "char_count": int(row.get('Chars', len(text))),
                            "is_signature": bool(row.get('Signature', False)),
                        }
                        
                        yield json.dumps({
                            "type": "chunk",
                            "text": text.strip(),
                            "metadata": metadata
                        }) + "\n"
                        total_chunk_index += 1
                        
                except Exception as e:
                    logger.error(f"Failed to process DataFrame: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                # Fallback: markdown chunking
                logger.warning("ChunkedDocument not available, using markdown fallback")
                try:
                    markdown_content = target_filing.markdown()
                    if markdown_content:
                        paragraphs = markdown_content.split("\n\n")
                        TARGET_SIZE = 4000
                        current_chunk = ""
                        
                        for para in paragraphs:
                            if len(current_chunk) + len(para) > TARGET_SIZE and current_chunk:
                                yield json.dumps({
                                    "type": "chunk",
                                    "text": current_chunk.strip(),
                                    "metadata": {
                                        "ticker": pure_symbol,
                                        "doc_id": accession_number,
                                        "form": target_filing.form,
                                        "item": "fallback",
                                        "item_name": "Fallback Chunking",
                                        "filing_date": str(target_filing.filing_date),
                                        "chunk_index": total_chunk_index,
                                    }
                                }) + "\n"
                                total_chunk_index += 1
                                current_chunk = para
                            else:
                                current_chunk += "\n\n" + para if current_chunk else para
                        
                        # Last chunk
                        if current_chunk.strip():
                            yield json.dumps({
                                "type": "chunk",
                                "text": current_chunk.strip(),
                                "metadata": {
                                    "ticker": pure_symbol,
                                    "doc_id": accession_number,
                                    "form": target_filing.form,
                                    "item": "fallback",
                                    "item_name": "Fallback Chunking",
                                    "filing_date": str(target_filing.filing_date),
                                    "chunk_index": total_chunk_index,
                                }
                            }) + "\n"
                            total_chunk_index += 1
                except Exception as e:
                    logger.error(f"Fallback chunking failed: {e}")
            
            logger.info(f"✅ Streamed {total_chunk_index} chunks")
            
            # Send footer
            yield json.dumps({
                "type": "footer",
                "chunks_count": total_chunk_index,
                "status": "success"
            }) + "\n"
            
        except Exception as e:
            logger.error(f"Streaming chunks failed: {e}")
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
