# src/server/mcp/tools/chunking_tools.py
"""MCP tools for semantic document chunking using edgartools.
Provides ChunkedDocument-based chunking with item labels for SEC filings.
"""

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP, Context

from src.server.utils.logger import logger


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


def register_chunking_tools(mcp: FastMCP):
    """Register document chunking tools."""

    @mcp.tool(tags={"chunking"})
    async def get_document_chunks(
        ticker: str,
        doc_id: str,
        items: list[str] = None,
        ctx: Context = None
    ) -> Dict[str, Any]:
        """Get semantic chunks from SEC filing with item labels.
        
        Uses edgartools' ChunkedDocument to split SEC filings by logical sections (Items).
        Each chunk includes rich metadata for precise RAG filtering.
        
        Args:
            ticker: Stock ticker (e.g., "AAPL" or "NASDAQ:AAPL")
            doc_id: SEC Accession Number (e.g., "0000320193-24-000123")
            items: Optional list of items to extract. Defaults to important sections:
                   ["Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8"]
                   
                   10-K Item Reference:
                   - Item 1: Business Description
                   - Item 1A: Risk Factors ⭐
                   - Item 1B: Unresolved Staff Comments
                   - Item 2: Properties
                   - Item 3: Legal Proceedings
                   - Item 5: Market for Common Equity
                   - Item 6: Selected Financial Data
                   - Item 7: MD&A (Management Discussion & Analysis) ⭐
                   - Item 7A: Quantitative Disclosures
                   - Item 8: Financial Statements ⭐
                   - Item 9A: Controls and Procedures
            ctx: FastMCP Context for logging
        
        Returns:
            Dict with:
            - status: "success" or "error"
            - chunks: List of {text, metadata} objects
            - chunks_count: Total number of chunks
            
        Example Response:
            {
                "status": "success",
                "doc_id": "0000320193-24-000123",
                "ticker": "AAPL",
                "form": "10-K",
                "chunks_count": 150,
                "chunks": [
                    {
                        "text": "Risk factors include competition...",
                        "metadata": {
                            "ticker": "AAPL",
                            "doc_id": "0000320193-24-000123",
                            "form": "10-K",
                            "item": "Item 1A",
                            "item_name": "Risk Factors",
                            "filing_date": "2024-11-01",
                            "chunk_index": 0
                        }
                    },
                    ...
                ]
            }
        """
        if ctx:
            await ctx.info(
                f"🔧 获取文档分块: {ticker} {doc_id}",
                extra={"ticker": ticker, "doc_id": doc_id, "items": items}
            )
        try:
            from src.server.utils.sec_utils import get_company
            
            # Extract pure symbol from EXCHANGE:SYMBOL format
            pure_symbol = ticker.split(":")[-1] if ":" in ticker else ticker
            accession_number = doc_id.replace("SEC:", "")
            
            logger.info(f"🔍 get_document_chunks: ticker={pure_symbol}, doc_id={accession_number}")
            
            # 1. Get the filing using edgartools via sec_utils (avoids ticker.txt download)
            company = get_company(pure_symbol)
            
            # Search for the specific filing by accession number
            filings = company.get_filings().latest(100)
            
            target_filing = None
            if filings:
                for filing in filings:
                    if filing.accession_no == accession_number:
                        target_filing = filing
                        break
            
            if not target_filing:
                return {
                    "status": "error",
                    "error": f"Filing not found: {accession_number} for {pure_symbol}",
                    "doc_id": doc_id,
                    "ticker": ticker,
                }
            
            logger.info(f"📄 Found filing: {target_filing.form} dated {target_filing.filing_date}")
            
            # 2. Get ChunkedDocument
            try:
                filing_obj = target_filing.obj()
                if not hasattr(filing_obj, 'doc') or filing_obj.doc is None:
                    # Fallback: some filings may not support ChunkedDocument
                    logger.warning(f"ChunkedDocument not available for {accession_number}, using markdown fallback")
                    return await _fallback_markdown_chunking(target_filing, pure_symbol, accession_number, ctx)
                
                chunked_doc = filing_obj.doc
            except Exception as e:
                logger.warning(f"Failed to get ChunkedDocument: {e}, using markdown fallback")
                return await _fallback_markdown_chunking(target_filing, pure_symbol, accession_number, ctx)
            
            # 3. Define items to extract
            default_items = ["Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8"]
            items_to_extract = items if items else default_items
            
            # Map item codes to human-readable names
            item_names = {
                "Item 1": "Business",
                "Item 1A": "Risk Factors",
                "Item 1B": "Unresolved Staff Comments",
                "Item 1C": "Cybersecurity",
                "Item 2": "Properties",
                "Item 3": "Legal Proceedings",
                "Item 4": "Mine Safety Disclosures",
                "Item 5": "Market for Common Equity",
                "Item 6": "Selected Financial Data",
                "Item 7": "MD&A",
                "Item 7A": "Quantitative Disclosures",
                "Item 8": "Financial Statements",
                "Item 9": "Disagreements with Accountants",
                "Item 9A": "Controls and Procedures",
                "Item 9B": "Other Information",
                "Item 10": "Directors and Executive Officers",
                "Item 11": "Executive Compensation",
                "Item 12": "Security Ownership",
                "Item 13": "Certain Relationships",
                "Item 14": "Principal Accountant Fees",
                "Item 15": "Exhibits",
            }
            
            # 4. 使用 as_dataframe() 获取带完整标签的 chunks
            all_chunks = []
            
            try:
                df = chunked_doc.as_dataframe()
                logger.info(f"📊 DataFrame loaded: {len(df)} rows, columns: {df.columns.tolist()}")
                
                # 过滤 DataFrame
                # 1. 过滤掉 Empty 的 chunks
                if 'Empty' in df.columns:
                    df = df[~df['Empty']]
                
                # 2. 只保留指定的 Items
                items_to_filter = items if items and len(items) > 0 else items_to_extract
                if 'Item' in df.columns and items_to_filter:
                    df = df[df['Item'].isin(items_to_filter)]
                
                logger.info(f"📦 After filtering: {len(df)} chunks for items {items_to_filter}")
                
                # 遍历 DataFrame 构建 chunks
                chunk_index = 0
                for idx, row in df.iterrows():
                    text = row.get('Text', '') or ''
                    if not text.strip():
                        continue
                    
                    item = row.get('Item', 'unknown')
                    
                    all_chunks.append({
                        "text": text.strip(),
                        "metadata": {
                            "ticker": pure_symbol,
                            "doc_id": accession_number,
                            "form": target_filing.form,
                            "item": item,
                            "item_name": item_names.get(item, item),
                            "filing_date": str(target_filing.filing_date),
                            "chunk_index": chunk_index,
                            # 额外标签
                            "is_table": bool(row.get('Table', False)),
                            "char_count": int(row.get('Chars', len(text))),
                            "is_signature": bool(row.get('Signature', False)),
                        }
                    })
                    chunk_index += 1
                    
            except Exception as e:
                logger.error(f"Failed to process DataFrame: {e}")
                import traceback
                logger.error(traceback.format_exc())
            
            logger.info(f"✅ Total chunks extracted: {len(all_chunks)}")
            
            if ctx:
                await ctx.info(
                    f"✅ 文档分块完成: {len(all_chunks)}个分块",
                    extra={"count": len(all_chunks)}
                )

            return {
                "status": "success",
                "doc_id": accession_number,
                "ticker": pure_symbol,
                "form": target_filing.form,
                "filing_date": str(target_filing.filing_date),
                "chunks_count": len(all_chunks),
                "chunks": all_chunks,
            }
            
        except Exception as e:
            logger.error(f"get_document_chunks failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            if ctx:
                await ctx.error(
                    f"❌ 文档分块失败: {ticker}",
                    extra={"error": str(e)}
                )
            return {
                "status": "error",
                "error": str(e),
                "doc_id": doc_id,
                "ticker": ticker,
            }


async def _fallback_markdown_chunking(filing, ticker: str, doc_id: str, ctx: Context = None) -> Dict[str, Any]:
    """Fallback chunking using markdown when ChunkedDocument is not available."""
    try:
        markdown_content = filing.markdown()
        if not markdown_content:
            return {
                "status": "error",
                "error": "Empty markdown content",
                "doc_id": doc_id,
                "ticker": ticker,
            }
        
        # Simple paragraph-based chunking
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
        
        # Add last chunk
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
        
        logger.info(f"📝 Fallback chunking: {len(chunks)} chunks")
        
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
        logger.error(f"Fallback chunking failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "doc_id": doc_id,
            "ticker": ticker,
        }
