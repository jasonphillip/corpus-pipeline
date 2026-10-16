"""Query windows for opt-in transcript passage display."""

import re

from .corpus_db import _FTS_COMMON_WORDS


HEADER = re.compile(
    r"^## (?:(?:Turn (\d+) · (USER|ASSISTANT|TOOL|SYSTEM)[^\n]*)|"
    r"(?:(USER|ASSISTANT|TOOL|SYSTEM) \(turn (\d+)\)))$", re.MULTILINE)
WINDOW = 700


def passage_text(row: dict, query: str) -> str:
    """Show a window around whole-word query matches with its governing role."""
    content = row.get("content", "")
    if not content:
        return ""
    terms = {word for word in re.findall(r"\w+", query.casefold())
             if len(word) > 2 and word not in _FTS_COMMON_WORDS}
    best_score, best_start = -1, 0
    for start in range(0, max(1, len(content) - WINDOW // 2), 100):
        window = content[start:start + WINDOW].casefold()
        words = set(re.findall(r"\w+", window))
        score = len(terms & words)
        if score > best_score:
            best_score, best_start = score, start
    snippet = content[best_start:best_start + WINDOW]
    meta = row.get("metadata") or {}
    turn = meta.get("turn_start") if isinstance(meta.get("turn_start"), dict) else None
    for match in HEADER.finditer(content):
        if match.start() > best_start:
            break
        turn = {"turn": int(match.group(1) or match.group(4)),
                "role": match.group(2) or match.group(3)}
    if not turn or not isinstance(turn.get("turn"), int) or \
            turn.get("role") not in {"USER", "ASSISTANT", "TOOL", "SYSTEM"}:
        return snippet.strip()
    heading = HEADER.match(snippet)
    if heading:
        snippet = snippet[heading.end():].lstrip("\n")
    return f"[turn {turn['turn']} · {turn['role']}] {snippet.strip()}"
