# src/server/domain/symbols/errors.py
"""Symbol resolution errors."""

from typing import List, Optional


class SymbolResolutionError(Exception):
    def __init__(self, code: str, message: str, raw: str, candidates: Optional[List[str]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw = raw
        self.candidates = candidates or []

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "raw": self.raw,
            "candidates": self.candidates,
        }
