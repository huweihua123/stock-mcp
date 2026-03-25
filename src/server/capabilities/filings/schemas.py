from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ProcessDocumentRequest(BaseModel):
    doc_id: str
    url: str
    doc_type: str
    ticker: Optional[str] = None
