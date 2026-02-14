"""Shared ticker normalization utilities."""

from __future__ import annotations

from typing import Optional, Tuple

from src.server.utils.logger import logger


_EXCHANGE_ALIASES = {
    "SH": "SSE",
    "SHSE": "SSE",
    "SS": "SSE",
    "SZ": "SZSE",
    "SZSE": "SZSE",
    "BJ": "BSE",
    "HK": "HKEX",
    "US": "NASDAQ",
}


def _normalize_exchange(exchange: str) -> str:
    exchange = (exchange or "").strip().upper()
    return _EXCHANGE_ALIASES.get(exchange, exchange)


def _autocorrect_a_share_exchange(
    exchange: str, symbol: str, raw_symbol: str
) -> Tuple[str, str]:
    """Auto-correct A-share exchange prefix when it mismatches the 6-digit code."""
    if not exchange or not symbol:
        return exchange, symbol
    if not symbol.isdigit() or len(symbol) != 6:
        return exchange, symbol

    ex = exchange.upper()
    if ex in {"SSE", "SZSE"}:
        if symbol.startswith("6") and ex != "SSE":
            logger.info(
                "Auto-correct A-share exchange prefix",
                raw=raw_symbol,
                from_exchange=ex,
                to_exchange="SSE",
                symbol=symbol,
            )
            return "SSE", symbol
        if symbol.startswith(("0", "3")) and ex != "SZSE":
            logger.info(
                "Auto-correct A-share exchange prefix",
                raw=raw_symbol,
                from_exchange=ex,
                to_exchange="SZSE",
                symbol=symbol,
            )
            return "SZSE", symbol
    return exchange, symbol


def normalize_ticker(symbol: str, default_us_exchange: Optional[str] = "NASDAQ") -> str:
    """Normalize ticker to internal EXCHANGE:SYMBOL format.

    Args:
        symbol: Input ticker (supports EXCHANGE:SYMBOL, 600519.SH, 600519, AAPL).
        default_us_exchange: When symbol is non-numeric and has no exchange prefix,
            prepend this exchange (defaults to NASDAQ). Set to None to keep raw symbol.
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        return raw

    if ":" in raw:
        exchange, sym = raw.split(":", 1)
        exchange = _normalize_exchange(exchange)
        sym = sym.strip().upper()
        exchange, sym = _autocorrect_a_share_exchange(exchange, sym, raw)
        if exchange and sym:
            return f"{exchange}:{sym}"
        return raw

    if "." in raw:
        code, suffix = raw.split(".", 1)
        exchange = _normalize_exchange(suffix)
        code = code.strip().upper()
        if exchange:
            exchange, code = _autocorrect_a_share_exchange(exchange, code, raw)
            return f"{exchange}:{code}"
        return raw

    if raw.isdigit():
        if len(raw) == 6:
            if raw.startswith("6"):
                return f"SSE:{raw}"
            if raw.startswith(("0", "3")):
                return f"SZSE:{raw}"
            if raw.startswith("8"):
                return f"BSE:{raw}"
        if len(raw) == 5:
            return f"HKEX:{raw.zfill(5)}"

    if default_us_exchange:
        return f"{default_us_exchange}:{raw}"
    return raw


def to_ts_code(symbol: str, fallback_exchange: Optional[str] = None) -> str:
    """Convert internal EXCHANGE:SYMBOL or plain symbol to ts_code format.

    Examples:
        SSE:600519 -> 600519.SH
        SZSE:000001 -> 000001.SZ
        BSE:430047 -> 430047.BJ
        600519.SH -> 600519.SH
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        return raw

    if "." in raw:
        return raw

    if ":" in raw:
        exchange, sym = raw.split(":", 1)
        exchange = _normalize_exchange(exchange)
        sym = sym.strip().upper()
        if exchange == "SSE":
            return f"{sym}.SH"
        if exchange == "SZSE":
            return f"{sym}.SZ"
        if exchange == "BSE":
            return f"{sym}.BJ"
        return sym

    if raw.isdigit() and len(raw) == 6 and fallback_exchange:
        ex = _normalize_exchange(fallback_exchange)
        if ex == "SSE":
            return f"{raw}.SH"
        if ex == "SZSE":
            return f"{raw}.SZ"
        if ex == "BSE":
            return f"{raw}.BJ"

    return raw
