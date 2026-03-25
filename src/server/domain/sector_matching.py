"""Shared sector query matching utilities for CN board/sector resolve."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Generic, Iterable, List, Optional, Tuple, TypeVar

try:
    import jieba  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    jieba = None

T = TypeVar("T")

_NOISE_TERMS = (
    "板块",
    "行业",
    "概念",
    "指数",
    "主题",
    "方向",
    "领域",
    "相关",
    "a股",
)
_STOP_TOKENS = {"", "的", "和", "及", "与", "在", "了", "是", "有"}
_GENERIC_SINGLE_TOKENS = {"板", "块", "行", "业", "概", "念", "指", "数", "股"}


@dataclass(frozen=True)
class RankedMatch(Generic[T]):
    item: T
    name: str
    score: int
    overlap: int
    exact: bool
    contains: bool
    reverse_contains: bool


def normalize_sector_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace("（", "(").replace("）", ")")
    raw = re.sub(r"\([^)]*\)", "", raw)
    for term in _NOISE_TERMS:
        raw = raw.replace(term, "")
    raw = re.sub(r"[\s\-_/,.;:]+", "", raw)
    raw = raw.replace('"', "").replace("'", "")
    return raw.strip()


def tokenize_sector_text(text: str) -> List[str]:
    normalized = normalize_sector_text(text)
    if not normalized:
        return []

    tokens: List[str] = []
    if jieba is not None:
        try:
            tokens = [str(tok or "").strip() for tok in jieba.lcut(normalized, HMM=False)]
        except Exception:
            tokens = []

    if not tokens:
        if len(normalized) <= 2:
            tokens = [normalized]
        else:
            tokens = [normalized[i : i + 2] for i in range(len(normalized) - 1)]

    # Keep full normalized query as a token for phrase-level overlap.
    tokens.append(normalized)

    dedup: List[str] = []
    seen = set()
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok in _STOP_TOKENS:
            continue
        if len(tok) == 1 and tok in _GENERIC_SINGLE_TOKENS:
            continue
        if tok in seen:
            continue
        dedup.append(tok)
        seen.add(tok)
    return dedup


def _score_match(query_text: str, candidate_name: str) -> Tuple[int, int, bool, bool, bool]:
    query_norm = normalize_sector_text(query_text)
    cand_norm = normalize_sector_text(candidate_name)
    if not query_norm or not cand_norm:
        return 0, 0, False, False, False

    query_tokens = tokenize_sector_text(query_norm)
    cand_tokens = tokenize_sector_text(cand_norm)
    q_set = set(query_tokens)
    c_set = set(cand_tokens)
    overlap_tokens = q_set.intersection(c_set)

    exact = query_norm == cand_norm
    contains = bool(query_norm and query_norm in cand_norm)
    reverse_contains = bool(cand_norm and len(cand_norm) >= 2 and cand_norm in query_norm)

    score = 0
    if exact:
        score += 1200
    if contains:
        score += 700 + min(len(query_norm), 12) * 10
    if reverse_contains:
        score += 460 + min(len(cand_norm), 12) * 8

    overlap = len(overlap_tokens)
    if overlap > 0:
        score += overlap * 130
        score += int(overlap / max(len(q_set), 1) * 120)
        score += int(overlap / max(len(c_set), 1) * 60)

    q_chars = set(query_norm)
    c_chars = set(cand_norm)
    if q_chars:
        score += int(len(q_chars.intersection(c_chars)) / len(q_chars) * 80)

    if not exact and not contains and not reverse_contains and overlap == 0:
        score -= 120
    if len(query_norm) >= 4 and overlap <= 1 and not contains and not reverse_contains:
        score -= 80

    return score, overlap, exact, contains, reverse_contains


def rank_sector_candidates(
    query_text: str,
    candidates: Iterable[T],
    *,
    name_getter: Callable[[T], str],
    top_k: int = 20,
) -> List[RankedMatch[T]]:
    ranked: List[RankedMatch[T]] = []
    for item in candidates:
        name = str(name_getter(item) or "").strip()
        if not name:
            continue
        score, overlap, exact, contains, reverse_contains = _score_match(query_text, name)
        if score <= 0:
            continue
        ranked.append(
            RankedMatch(
                item=item,
                name=name,
                score=score,
                overlap=overlap,
                exact=exact,
                contains=contains,
                reverse_contains=reverse_contains,
            )
        )

    ranked.sort(
        key=lambda x: (x.score, len(normalize_sector_text(x.name))),
        reverse=True,
    )
    if top_k <= 0:
        return ranked
    return ranked[:top_k]


def pick_sector_resolution(
    query_text: str,
    ranked: List[RankedMatch[T]],
    *,
    ambiguous_top_k: int = 10,
) -> Tuple[Optional[RankedMatch[T]], List[str]]:
    if not ranked:
        return None, []

    top = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else 0
    gap = top.score - second_score
    query_norm = normalize_sector_text(query_text)

    if top.exact:
        return top, []
    if len(ranked) == 1 and top.score >= 260:
        return top, []
    if top.contains and top.score >= 820 and gap >= 120:
        return top, []
    if top.reverse_contains and top.score >= 640 and gap >= 40:
        return top, []
    if top.score >= 900 and gap >= 100:
        return top, []
    if top.score >= 760 and gap >= 180:
        return top, []

    # Single short query tends to be broad; keep ambiguous unless very clear.
    if len(query_norm) <= 2 and len(ranked) > 1 and gap < 180:
        return None, [x.name for x in ranked[:ambiguous_top_k]]

    return None, [x.name for x in ranked[:ambiguous_top_k]]
