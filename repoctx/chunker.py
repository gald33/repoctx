"""Symbol-aware sliding-window chunker.

Splits a file into overlapping windows sized for embedding. The same greedy
line-walker handles both code and prose; only the split-priority hierarchy
differs:

* Code:  top-level symbol start (5) > nested symbol start (3) > blank line (2) > any line (1)
* Prose: paragraph break (4) > sentence end (2) > any line (1)

When the walker has accumulated >= ``target_tokens`` and is searching for a
boundary, it picks the highest-priority break in the lookahead window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from repoctx.models import FileRecord
from repoctx.symbols import Symbol


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    start_line: int  # 1-indexed inclusive
    end_line: int  # 1-indexed inclusive
    enclosing_symbol: str | None
    chunk_index: int


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    target_tokens: int = 400
    max_tokens: int = 600
    overlap_tokens: int = 80
    min_tokens: int = 40


_CODE_KINDS = {"code", "test", "config"}


def estimate_tokens(text: str) -> int:
    """Cheap token-count proxy. Word count × 1.3.

    Avoids a tokenizer dependency. Accurate enough for chunk sizing — the
    embedding model has its own tokenizer for the actual encode call.
    """
    if not text:
        return 0
    return max(1, int(round(len(text.split()) * 1.3)))


def chunk_record(
    record: FileRecord,
    symbols: list[Symbol] | None = None,
    cfg: ChunkConfig | None = None,
) -> list[Chunk]:
    """Split *record* into overlapping chunks.

    ``symbols`` should come from :func:`repoctx.symbols.extract_symbols`. Pass
    an empty list (or omit) for prose / unsupported languages.
    """
    cfg = cfg or ChunkConfig()
    if not record.content:
        return []
    mode: Literal["code", "prose"] = "code" if record.kind in _CODE_KINDS else "prose"
    return _chunk(record.content, symbols or [], cfg, mode=mode)


# ---------- core walker -------------------------------------------------------


def _chunk(
    content: str,
    symbols: list[Symbol],
    cfg: ChunkConfig,
    *,
    mode: Literal["code", "prose"],
) -> list[Chunk]:
    lines = content.splitlines(keepends=True)
    if not lines:
        return []
    n = len(lines)
    line_tokens = [estimate_tokens(line) for line in lines]
    enclosing = _enclosing_per_line(n, symbols)
    priorities = _split_priorities(lines, symbols, mode)

    chunks: list[Chunk] = []
    chunk_index = 0
    i = 0
    while i < n:
        end = _pick_window_end(i, n, line_tokens, priorities, cfg)
        text = "".join(lines[i:end]).rstrip("\n")
        sym = _dominant_symbol(enclosing[i:end])
        chunks.append(
            Chunk(
                text=text,
                start_line=i + 1,
                end_line=end,
                enclosing_symbol=sym,
                chunk_index=chunk_index,
            )
        )
        chunk_index += 1
        if end >= n:
            break
        i = _next_start(i, end, line_tokens, cfg)

    return _merge_tiny_tail(chunks, cfg)


def _pick_window_end(
    start: int,
    n: int,
    line_tokens: list[int],
    priorities: list[int],
    cfg: ChunkConfig,
) -> int:
    """Return exclusive end index for the chunk starting at *start*.

    Walks forward accumulating tokens. Once at or past ``target_tokens``,
    looks for the highest-priority break in [target_idx, max_idx]. Hard-cuts
    at ``max_tokens`` if no break found.
    """
    token_sum = 0
    target_idx = -1
    cursor = start
    while cursor < n:
        token_sum += line_tokens[cursor]
        if target_idx < 0 and token_sum >= cfg.target_tokens:
            target_idx = cursor + 1
        if token_sum >= cfg.max_tokens:
            cursor += 1
            break
        cursor += 1
    max_idx = cursor  # exclusive
    if target_idx < 0:
        return min(max_idx, n)  # whole file fits

    # Search backwards from max_idx down to target_idx for highest priority.
    # A boundary "before line j" means the chunk ends at j (exclusive).
    best_priority = -1
    best_end = max_idx
    lo = max(target_idx, start + 1)
    hi = min(max_idx, n)
    for j in range(hi, lo - 1, -1):
        if j >= n:
            continue
        p = priorities[j]
        if p > best_priority:
            best_priority = p
            best_end = j
            if p >= 4:  # top-level symbol or paragraph break — take it
                break
    return min(max(best_end, start + 1), n)


def _next_start(prev_start: int, end: int, line_tokens: list[int], cfg: ChunkConfig) -> int:
    """Compute next chunk start with overlap, never moving backwards past prev_start."""
    if cfg.overlap_tokens <= 0:
        return end
    back = end
    overlap = 0
    while back > prev_start + 1 and overlap < cfg.overlap_tokens:
        back -= 1
        overlap += line_tokens[back]
    chunk_lines = end - prev_start
    overlap_lines = end - back
    # Cap overlap at half the previous chunk to guarantee progress.
    if chunk_lines <= 1 or overlap_lines * 2 > chunk_lines:
        return end
    return back


def _merge_tiny_tail(chunks: list[Chunk], cfg: ChunkConfig) -> list[Chunk]:
    if len(chunks) < 2:
        return chunks
    last = chunks[-1]
    if estimate_tokens(last.text) >= cfg.min_tokens:
        return chunks
    prev = chunks[-2]
    merged = Chunk(
        text=prev.text + "\n" + last.text,
        start_line=prev.start_line,
        end_line=last.end_line,
        enclosing_symbol=prev.enclosing_symbol,
        chunk_index=prev.chunk_index,
    )
    return chunks[:-2] + [merged]


# ---------- per-line metadata -------------------------------------------------


def _enclosing_per_line(n: int, symbols: list[Symbol]) -> list[str | None]:
    """Return innermost enclosing symbol qualified name for each 0-indexed line."""
    out: list[str | None] = [None] * n
    # Apply outer symbols first, then inner overwrite. Approximate by sorting
    # by span size descending (bigger first → smaller overwrite).
    ordered = sorted(symbols, key=lambda s: (s.end_line - s.start_line), reverse=True)
    for s in ordered:
        lo = max(0, s.start_line - 1)
        hi = min(n, s.end_line)
        for ln in range(lo, hi):
            out[ln] = s.qualified_name
    return out


def _split_priorities(
    lines: list[str],
    symbols: list[Symbol],
    mode: Literal["code", "prose"],
) -> list[int]:
    """Boundary priority *before* each 0-indexed line. Higher = stronger.

    A break ``before line j`` produces a chunk ending at ``j`` (exclusive).
    """
    n = len(lines)
    p = [1] * n  # baseline: any line break
    if mode == "code":
        for sym in symbols:
            ln = sym.start_line - 1
            if 0 <= ln < n:
                # Top-level if no dot in qualified name.
                pri = 5 if "." not in sym.qualified_name else 3
                if p[ln] < pri:
                    p[ln] = pri
        for i, line in enumerate(lines):
            if line.strip() == "" and i + 1 < n and p[i + 1] < 2:
                p[i + 1] = 2
    else:  # prose
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            if line.strip() == "" and i + 1 < n and p[i + 1] < 4:
                p[i + 1] = 4
            elif stripped.endswith((".", "!", "?")) and i + 1 < n and p[i + 1] < 2:
                p[i + 1] = 2
    return p


def _dominant_symbol(enclosing_slice: list[str | None]) -> str | None:
    """Return the most common non-None symbol, or None if mostly module-level."""
    counts: dict[str | None, int] = {}
    for s in enclosing_slice:
        counts[s] = counts.get(s, 0) + 1
    if not counts:
        return None
    # Prefer non-None when there's a tie or near-tie.
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0] is not None))
    return best[0]
