"""Run real corpus search and score strict and source-level retrieval."""

from __future__ import annotations

import io
import math
import re
import sqlite3
import os
from pathlib import Path
import statistics
import time
from contextlib import redirect_stdout

import httpx

from ..config import corpus_path
from ..chunk_dates import date_bounds


def real_search(corpora: list[str], query: str, top_k: int,
                settings_overrides: dict, since: str | None = None,
                until: str | None = None) -> list[dict]:
    """Call package search in-process without printing each result."""
    from ..commands.search import search, SEARCH_EXCLUDE_CAPTIONS
    captions = "exclude" if SEARCH_EXCLUDE_CAPTIONS else "include"
    with redirect_stdout(io.StringIO()):
        return search(corpora, query, top_k=top_k, captions=captions,
                      settings_overrides=settings_overrides, since=since, until=until)


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _strict_target_exists(question: dict, searched_corpus: str) -> bool:
    path = corpus_path(searched_corpus) / "processed" / "chunks.db"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT source_path, chunk_index, content FROM chunks WHERE id = ?",
                               (question["chunk_id"],)).fetchone()
        return bool(row and row[0] == question.get("source_path") and
                    (question.get("chunk_index") is None or row[1] == question["chunk_index"]) and
                    (not question.get("answer_span") or _normal(question["answer_span"]) in _normal(row[2])))
    except (sqlite3.Error, OSError):
        return False


def parent_mapping(corpora: list[str], aliases: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve each searched corpus to the question corpus it represents."""
    aliases = aliases or {}
    parents = {}
    for name in corpora:
        parent, separator, suffix = name.rpartition("--")
        parents[name] = aliases.get(name, parent if separator and parent and suffix else name)
    return parents


def parse_aliases(items: list[str]) -> dict[str, str]:
    aliases = {}
    for item in items:
        shadow, separator, parent = item.partition("=")
        if not separator or not shadow.strip() or not parent.strip():
            raise ValueError("--alias requires SHADOW=PARENT")
        shadow, parent = shadow.strip(), parent.strip()
        if shadow in aliases and aliases[shadow] != parent:
            raise ValueError(f"conflicting alias for {shadow}")
        aliases[shadow] = parent
    return aliases


def parse_settings(items: list[str]) -> dict:
    result = {}
    for item in items:
        if "=" not in item:
            raise ValueError("--search-arg requires KEY=VALUE")
        key, value = item.split("=", 1)
        if key == "fusion" and value in ("linear", "rrf"):
            result[key] = value
        elif key in ("expansion", "rerank") and value.lower() in ("true", "false", "1", "0", "on", "off"):
            result[key] = value.lower() in ("true", "1", "on")
        elif key == "conversation_cap" and value.isdigit():
            result[key] = int(value)
        elif key == "display" and value in ("first", "passage"):
            result[key] = value
        else:
            raise ValueError(f"invalid search setting: {item}")
    return result


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    position = (len(values) - 1) * pct
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def rank_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    result = {"count": n, "strict": {}, "source": {}, "span": {}}
    for level in ("strict", "source", "span"):
        ranks = [row.get(f"{level}_rank") for row in rows]
        for cutoff in (1, 5, 10, 30):
            result[level][f"R@{cutoff}"] = sum(rank is not None and rank <= cutoff for rank in ranks) / n if n else 0.0
        result[level]["MRR"] = sum(1 / rank for rank in ranks if rank) / n if n else 0.0
        result[level]["nDCG@10"] = sum(1 / math.log2(rank + 1) for rank in ranks
                                          if rank is not None and rank <= 10) / n if n else 0.0
    latencies = [row.get("latency_ms", 0.0) for row in rows]
    result["median_ms"] = statistics.median(latencies) if latencies else 0.0
    result["p90_ms"] = _percentile(latencies, .90)
    return result


def evaluate_questions(questions: list[dict], search_fn=None,
                       top_k: int = 30, corpora: list[str] | None = None,
                       settings_overrides: dict | None = None,
                       since: str | None = None, until: str | None = None,
                       aliases: dict[str, str] | None = None) -> dict:
    if top_k <= 0:
        raise ValueError("--top-k must be positive")
    search_fn = search_fn or real_search
    settings_overrides = settings_overrides or {}
    since, until = date_bounds(since, until)
    rows = []
    for question in questions:
        selected = corpora or [question["corpus"]]
        parents = parent_mapping(selected, aliases)
        started = time.monotonic()
        try:
            results = (search_fn(selected, question["question"], top_k, settings_overrides,
                                 since=since, until=until) if since or until else
                       search_fn(selected, question["question"], top_k, settings_overrides))
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            raise SystemExit(4) from error
        latency_ms = (time.monotonic() - started) * 1000
        hits = [{"corpus": r.get("corpus"), "chunk_id": r.get("chunk_id", r.get("id")),
                 "source_path": r.get("source_path"), "chunk_index": r.get("chunk_index"),
                 "score": r.get("rerank_score", r.get("rrf_score", r.get("score")))}
                for r in (results or [])[:top_k]]
        strict_defined = (any(_strict_target_exists(question, name) for name in selected
                              if parents[name] == question["corpus"])
                          if search_fn is real_search else True)
        strict = next((i for i, hit in enumerate(hits, 1)
                       if parents.get(hit["corpus"]) == question["corpus"]
                       and str(hit["chunk_id"]) == str(question["chunk_id"])), None) if strict_defined else None
        source = next((i for i, hit in enumerate(hits, 1)
                       if parents.get(hit["corpus"]) == question["corpus"]
                       and question.get("source_path") is not None
                       and hit["source_path"] == question["source_path"]), None)
        answer_span = question.get("answer_span")
        span = next((i for i, hit in enumerate((results or [])[:top_k], 1)
                     if answer_span and parents.get(hit.get("corpus")) == question["corpus"]
                     and _normal(answer_span) in _normal(hit.get("content") or "")), None)
        rows.append({"qid": question["qid"], "kind": question["kind"],
                     "corpus": question["corpus"], "question": question["question"],
                     "target_chunk_id": question["chunk_id"],
                     "strict_rank": strict, "source_rank": source, "span_rank": span,
                     "strict_defined": strict_defined,
                     "span_defined": bool(answer_span),
                     "latency_ms": latency_ms, "results": hits})
    kinds = sorted({row["kind"] for row in rows})
    metrics = {"overall": rank_metrics(rows)}
    metrics.update({kind: rank_metrics([row for row in rows if row["kind"] == kind]) for kind in kinds})
    return {"questions": rows, "metrics": metrics}


ENV_KEYS = ("CORPUS_SEARCH_RERANK", "SEARCH_RERANK", "CORPUS_FTS_MATCH", "SEARCH_FTS_PHRASE",
            "SEARCH_PHRASE_BONUS", "SEARCH_PHRASE_RRF_WEIGHT", "SEARCH_CONTEXT_FTS",
            "CORPUS_RECENCY_WEIGHT", "CORPUS_QUERY_EXPANSION",
            "CORPUS_EXCLUDE_CAPTIONS", "CORPUS_CAPTION_PREFIX", "CORPUS_RERANK_DOC_CHARS",
            "CORPUS_RERANK_URL", "CORPUS_EMBED_URL", "CORPUS_EMBED_URL_2",
            "CORPUS_ROOT", "EVAL_LLM_URL", "EVAL_LLM_MODEL")


def environment_snapshot() -> dict:
    return {name: os.environ[name] for name in ENV_KEYS if name in os.environ}
