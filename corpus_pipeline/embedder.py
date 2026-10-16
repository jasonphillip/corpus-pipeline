"""Embedding clients for OpenAI-compatible services and Ollama."""

from __future__ import annotations

import struct
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import httpx

from .config import QUERY_EMBED_TIMEOUT, get_embedding_config


_QUERY_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages "
    "that answer the query.\nQuery: "
)
_config: dict[str, Any] | None = None
_actual_dims: int | None = None


def _get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = get_embedding_config()
    return _config


def reset_config_cache() -> None:
    """Clear cached provider and dimension state, mainly for tests."""
    global _config, _actual_dims
    _config = None
    _actual_dims = None
    getattr(embed_query, "cache_clear", lambda: None)()


def _probe_dims() -> int:
    global _actual_dims
    if _actual_dims is None:
        cfg = _get_config()
        _actual_dims = len(_embed_raw("dimension probe", cfg))
        cfg["dims"] = _actual_dims
    return _actual_dims


def embed_text(text: str) -> bytes:
    """Embed one document string and return packed little-endian float32."""
    return _pack_vector(_embed_raw(text, _get_config()))


def embed_texts(texts: list[str], batch_size: int = 64) -> list[bytes]:
    """Embed document strings in ordered batches."""
    cfg = _get_config()
    output: list[bytes] = []
    for start in range(0, len(texts), batch_size):
        vectors = _embed_batch_raw(texts[start : start + batch_size], cfg)
        output.extend(_pack_vector(vector) for vector in vectors)
    return output


def embed_texts_parallel(
    texts: list[str], batch_size: int = 64, workers: int = 4
) -> list[bytes]:
    """Embed batches concurrently and preserve input order.

    Multiple configured OpenAI-compatible endpoints receive batches in round-
    robin order. Ollama uses one endpoint.
    """
    if not texts:
        return []
    cfg = _get_config()
    if cfg["provider"] == "openai":
        from .config import get_embedding_urls

        urls = get_embedding_urls() or [cfg["url"]]
    else:
        urls = [cfg["url"]]

    natural_batches = (len(texts) + batch_size - 1) // batch_size
    if len(urls) > 1 and natural_batches < len(urls) and len(texts) >= len(urls):
        effective_batch = max(1, (len(texts) + len(urls) - 1) // len(urls))
    else:
        effective_batch = batch_size

    batches: list[tuple[int, list[str], str]] = []
    for index, start in enumerate(range(0, len(texts), effective_batch)):
        batches.append(
            (start, texts[start : start + effective_batch], urls[index % len(urls)])
        )

    results: list[bytes | None] = [None] * len(texts)

    def process(item: tuple[int, list[str], str]) -> tuple[int, list[bytes]]:
        start, batch, url = item
        vectors = _embed_batch_raw(batch, {**cfg, "url": url})
        return start, [_pack_vector(vector) for vector in vectors]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start, vectors in pool.map(process, batches):
            for offset, vector in enumerate(vectors):
                results[start + offset] = vector

    if any(result is None for result in results):
        raise RuntimeError("embedding service returned an incomplete batch")
    return [result for result in results if result is not None]


@lru_cache(maxsize=64)
def embed_query(text: str) -> bytes:
    """Embed a query, applying the instruction used by Qwen3 embedding models."""
    cfg = _get_config()
    if "qwen3" in cfg.get("model", "").lower():
        text = f"{_QUERY_PREFIX}{text}"
    return _pack_vector(_embed_raw(text, cfg, timeout=QUERY_EMBED_TIMEOUT))


def get_dims() -> int:
    """Probe and return the active embedding dimension."""
    return _probe_dims()


def get_label() -> str:
    """Return the active embedding provider label."""
    return str(_get_config()["label"])


def _embed_raw(text: str, cfg: dict[str, Any], timeout: float = 30.0) -> list[float]:
    if cfg["provider"] == "openai":
        return _embed_openai(text, cfg, timeout)
    return _embed_ollama(text, cfg, timeout)


def _embed_batch_raw(texts: list[str], cfg: dict[str, Any]) -> list[list[float]]:
    if cfg["provider"] == "openai":
        return _embed_openai_batch(texts, cfg)
    return _embed_ollama_batch(texts, cfg)


def _embed_ollama(text: str, cfg: dict[str, Any], timeout: float = 30.0) -> list[float]:
    response = httpx.post(
        cfg["url"], json={"model": cfg["model"], "input": text}, timeout=timeout
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def _embed_ollama_batch(texts: list[str], cfg: dict[str, Any]) -> list[list[float]]:
    response = httpx.post(
        cfg["url"], json={"model": cfg["model"], "input": texts}, timeout=120.0
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    api_key = str(cfg.get("api_key") or "")
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _embed_openai(text: str, cfg: dict[str, Any], timeout: float = 30.0) -> list[float]:
    response = httpx.post(
        cfg["url"],
        headers=_headers(cfg),
        json={"model": cfg["model"], "input": text},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    try:
        return data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected embedding response: {str(data)[:300]}") from exc


def _embed_openai_batch(texts: list[str], cfg: dict[str, Any]) -> list[list[float]]:
    response = httpx.post(
        cfg["url"],
        headers=_headers(cfg),
        json={"model": cfg["model"], "input": texts},
        timeout=120.0,
    )
    response.raise_for_status()
    data = response.json()
    try:
        items = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected batch response: {str(data)[:300]}") from exc


def _pack_vector(vector: list[float]) -> bytes:
    """Pack a float list for sqlite-vec."""
    return struct.pack(f"<{len(vector)}f", *vector)
