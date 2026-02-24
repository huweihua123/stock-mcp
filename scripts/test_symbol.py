#!/usr/bin/env python3
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.server.domain.symbols.normalize import normalize_ticker, to_ts_code


def main() -> int:
    raw = sys.argv[1] if len(sys.argv) > 1 else "SHSE:600519"
    normalized = normalize_ticker(raw)
    ts_code = to_ts_code(normalized)

    print(f"raw: {raw}")
    print(f"normalized: {normalized}")
    print(f"ts_code: {ts_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
