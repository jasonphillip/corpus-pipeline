"""Find the earliest dated mention of a topic in an indexed corpus."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import MODEL_SCORING, OLLAMA_HOST, corpus_path


MAX_EXPANSIONS = 12
MAX_SQL_TERMS_PER_VARIANT = 4
_PROPER_NOUN_DENYLIST = frozenset(
    {
        "true",
        "false",
        "none",
        "null",
        "database",
        "inference",
        "embedding",
        "server",
        "model",
        "memory",
        "session",
        "context",
        "corpus",
        "vector",
        "pipeline",
        "workflow",
        "today",
        "tomorrow",
    }
)
_TS_SQL = (
    "COALESCE("
    "NULLIF(json_extract({prefix}metadata_json, '$.ts'), ''), "
    "NULLIF(json_extract({prefix}metadata_json, '$.started_at'), ''), "
    "NULLIF(json_extract({prefix}metadata_json, '$.created_at'), ''), "
    "NULLIF(json_extract({prefix}metadata_json, '$.content_date'), '')"
    ")"
)
_EXPANSION_PROMPT = """/no_think Generate search variants for finding the earliest document about a topic.

Topic: {topic}

Output 8-12 variants, one per line. Mix expert terms with plain descriptions.
Do not add numbering, bullets, quotes, or prose.
"""


def _is_proper_noun_query(query: str) -> bool:
    query = query.strip()
    return bool(
        query
        and " " not in query
        and len(query) >= 4
        and query[0].isupper()
        and query.lower() not in _PROPER_NOUN_DENYLIST
    )


def expand_topic(
    topic: str, ollama_url: str = OLLAMA_HOST, model: str = MODEL_SCORING
) -> list[str]:
    """Generate search variants. Return the input alone when generation fails."""
    payload = {
        "model": model,
        "prompt": _EXPANSION_PROMPT.format(topic=topic),
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 1024},
    }
    try:
        response = httpx.post(
            f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=60.0
        )
        response.raise_for_status()
        body = response.json().get("response", "")
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[expand] warning: {exc}; using the topic as-is", file=sys.stderr)
        return [topic]
    body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL).strip()
    body = re.sub(r"```(?:json)?\s*", "", body).replace("```", "")
    seen: OrderedDict[str, str] = OrderedDict([(topic.lower(), topic)])
    for raw in body.splitlines():
        line = re.sub(r"^[\"'\[\(\-*\d.\)\s]+", "", raw.strip())
        line = re.sub(r"[\"'\]\)\s,]+$", "", line)
        if line and len(line) <= 120:
            seen.setdefault(line.lower(), line)
        if len(seen) >= MAX_EXPANSIONS:
            break
    return list(seen.values())


def _shorten(text: str, length: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:length] + ("…" if len(text) > length else "")


def sql_chronological_scan(
    database: Path, queries: list[str], limit_per_query: int = 10
) -> list[dict[str, Any]]:
    """Return the earliest FTS or LIKE matches for each query variant."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    has_fts = bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
    )
    results: list[dict[str, Any]] = []
    try:
        for query in queries:
            tokens = [
                token
                for token in re.findall(r"[A-Za-z0-9_]+", query)
                if len(token) > 2
            ][:MAX_SQL_TERMS_PER_VARIANT]
            if not tokens:
                continue
            rows: list[sqlite3.Row] = []
            used_fts = False
            if has_fts:
                timestamp = _TS_SQL.format(prefix="c.")
                phrase = '"' + " ".join(tokens) + '"'
                try:
                    rows = connection.execute(
                        f"SELECT c.source_path, c.chunk_index, c.content, "
                        f"c.metadata_json, {timestamp} AS ts "
                        "FROM chunks c JOIN chunks_fts f ON c.id = f.chunk_id "
                        f"WHERE f.chunks_fts MATCH ? AND {timestamp} IS NOT NULL "
                        "ORDER BY ts, c.chunk_index LIMIT ?",
                        (phrase, limit_per_query),
                    ).fetchall()
                    used_fts = True
                except sqlite3.OperationalError:
                    rows = []
            if not used_fts:
                timestamp = _TS_SQL.format(prefix="")
                clauses = " AND ".join(["content LIKE ?"] * len(tokens))
                rows = connection.execute(
                    f"SELECT source_path, chunk_index, content, metadata_json, "
                    f"{timestamp} AS ts FROM chunks WHERE {clauses} "
                    f"AND {timestamp} IS NOT NULL ORDER BY ts, chunk_index LIMIT ?",
                    [*(f"%{token}%" for token in tokens), limit_per_query],
                ).fetchall()
            for row in rows:
                if row["ts"]:
                    results.append(
                        {
                            "source_path": row["source_path"],
                            "chunk_index": row["chunk_index"],
                            "ts": row["ts"],
                            "snippet": _shorten(row["content"]),
                            "metadata_json": row["metadata_json"],
                            "matched_term": query,
                            "match_method": "text",
                        }
                    )
    finally:
        connection.close()
    return results


def semantic_search(corpus: str, query: str) -> list[dict[str, Any]]:
    """Run the package hybrid search and attach source timestamps."""
    from .search import _search_single_corpus

    with contextlib.redirect_stdout(io.StringIO()):
        candidates = _search_single_corpus(corpus, [query], 10)
    database = corpus_path(corpus) / "processed" / "chunks.db"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    timestamp = _TS_SQL.format(prefix="")
    output: list[dict[str, Any]] = []
    try:
        for candidate in candidates:
            row = connection.execute(
                f"SELECT metadata_json, {timestamp} AS ts FROM chunks "
                "WHERE id = ? LIMIT 1",
                (candidate["id"],),
            ).fetchone()
            if row and row["ts"]:
                output.append(
                    {
                        "source_path": candidate["source_path"],
                        "ts": row["ts"],
                        "snippet": _shorten(candidate["content"]),
                        "metadata_json": row["metadata_json"],
                        "matched_term": query,
                        "match_method": "semantic",
                    }
                )
    finally:
        connection.close()
    return output


def merge_and_rank(
    text_results: list[dict[str, Any]],
    semantic_results: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Deduplicate by source and sort oldest first."""
    by_source: dict[str, dict[str, Any]] = {}
    for result in text_results + semantic_results:
        if not result.get("ts"):
            continue
        existing = by_source.get(result["source_path"])
        if existing is None:
            by_source[result["source_path"]] = dict(result)
        elif existing["match_method"] != result["match_method"]:
            existing["match_method"] = "both"
    return sorted(by_source.values(), key=lambda item: item["ts"])[:limit]


def format_markdown(
    topic: str, corpus: str, expansions: list[str], results: list[dict[str, Any]]
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines = [
        f"# Earliest mentions: `{topic}` in `{corpus}`",
        "",
        f"**Generated**: {now}  ",
        f"**Expansion variants** ({len(expansions)}): "
        + ", ".join(f"`{item}`" for item in expansions),
        "",
    ]
    if not results:
        return "\n".join(lines + ["**No dated matches found.**", ""])
    lines.extend(
        [
            "| # | Date | Source | Match | Snippet |",
            "|---|---|---|---|---|",
        ]
    )
    for index, result in enumerate(results, 1):
        snippet = result["snippet"].replace("|", "\\|")
        lines.append(
            f"| {index} | {str(result['ts'])[:10]} | `{result['source_path']}` | "
            f"{result['match_method']} | {snippet} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus first-mention", description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("topic")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-expand", action="store_true")
    parser.add_argument("--force-expand", action="store_true")
    parser.add_argument("--ollama-url", default=OLLAMA_HOST)
    parser.add_argument("--ollama-model", default=MODEL_SCORING)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=["md", "json"], default="md")
    args = parser.parse_args(argv)
    database = corpus_path(args.corpus) / "processed" / "chunks.db"
    if not database.is_file():
        print(f"No index at {database}. Run process first.", file=sys.stderr)
        return 1
    if args.no_expand or (
        _is_proper_noun_query(args.topic) and not args.force_expand
    ):
        expansions = [args.topic]
    else:
        expansions = expand_topic(args.topic, args.ollama_url, args.ollama_model)
    text_results = sql_chronological_scan(database, expansions)
    semantic_results: list[dict[str, Any]] = []
    for query in expansions[:3]:
        try:
            semantic_results.extend(semantic_search(args.corpus, query))
        except Exception as exc:
            print(f"[semantic] warning: {exc}", file=sys.stderr)
    results = merge_and_rank(text_results, semantic_results, args.limit)
    if args.format == "json":
        rendered = json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    else:
        rendered = format_markdown(args.topic, args.corpus, expansions, results)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
