"""Configuration for the corpus pipeline.

Values load from YAML, then environment variables override them. Importing this
module performs no service probes. Runtime commands probe configured endpoints
only when they need those services.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _default_config_path() -> Path:
    explicit = os.environ.get("CORPUS_CONFIG", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    local = Path.cwd() / "corpus.yml"
    if local.is_file():
        return local
    return Path.home() / ".config" / "corpus-pipeline" / "corpus.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return loaded


CONFIG_PATH = _default_config_path()
_CONFIG = _load_yaml(CONFIG_PATH)


def _cfg(path: str, default: Any) -> Any:
    value: Any = _CONFIG
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _path_env(name: str, config_key: str, default: str | Path) -> Path:
    raw = os.environ.get(name) or _cfg(config_key, str(default))
    return Path(str(raw)).expanduser()


def _str_env(name: str, config_key: str, default: str) -> str:
    return str(os.environ.get(name) or _cfg(config_key, default)).strip()


def _int_env(name: str, config_key: str, default: int) -> int:
    return int(os.environ.get(name) or _cfg(config_key, default))


def _float_env(name: str, config_key: str, default: float) -> float:
    return float(os.environ.get(name) or _cfg(config_key, default))


CORPUS_ROOT = _path_env("CORPUS_ROOT", "paths.corpus_root", Path.home() / ".corpus")
STATUS_FILE = _path_env(
    "CORPUS_STATUS_FILE",
    "paths.status_file",
    CORPUS_ROOT / "corpus-index.md",
)
SNAPSHOT_ROOT = _path_env(
    "CORPUS_SNAPSHOT_ROOT",
    "paths.snapshot_root",
    CORPUS_ROOT / "snapshots",
)
HEALTH_ROOT = _path_env(
    "CORPUS_HEALTH_ROOT",
    "paths.health_root",
    CORPUS_ROOT / "health",
)

OLLAMA_HOST = _str_env("OLLAMA_HOST", "services.ollama.base_url", "http://localhost:11434").rstrip("/")
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_EMBED_URL = f"{OLLAMA_HOST}/api/embed"

OPENAI_EMBED_URL = _str_env(
    "CORPUS_EMBED_URL",
    "services.embedding.url",
    "http://localhost:8000/v1/embeddings",
)
EMBED_URL_2 = _str_env("CORPUS_EMBED_URL_2", "services.embedding.secondary_url", "")
EMBED_API_KEY = _str_env("CORPUS_EMBED_API_KEY", "services.embedding.api_key", "")
EMBED_PROVIDER = _str_env("CORPUS_EMBED_PROVIDER", "services.embedding.provider", "auto").lower()
QUERY_EMBED_TIMEOUT = _float_env(
    "CORPUS_QUERY_EMBED_TIMEOUT", "services.embedding.query_timeout", 120.0)
if QUERY_EMBED_TIMEOUT <= 0:
    raise ValueError("services.embedding.query_timeout must be positive")

LLM_CHAT_URL = _str_env("CORPUS_LLM_CHAT_URL", "services.chat.url", "")
LLM_CHAT_MODEL = _str_env("CORPUS_LLM_CHAT_MODEL", "services.chat.model", "")
LLM_CHAT_API_KEY = _str_env("CORPUS_LLM_CHAT_API_KEY", "services.chat.api_key", "")
RERANK_URL = _str_env(
    "CORPUS_RERANK_URL",
    "services.reranker.url",
    "http://localhost:8003/v1/rerank",
)

MODEL_SCORING = _str_env("CORPUS_SCORING_MODEL", "models.scoring", "qwen3:14b")
MODEL_TRIAGE = _str_env("CORPUS_TRIAGE_MODEL", "models.triage", "qwen3:8b")
MODEL_EMBED_DEFAULT = _str_env(
    "CORPUS_OLLAMA_EMBED_MODEL",
    "models.ollama_embedding",
    "nomic-embed-text-v2-moe",
)
MODEL_EMBED_ENHANCED = _str_env(
    "CORPUS_OPENAI_EMBED_MODEL",
    "models.openai_embedding",
    "local-embedding-model",
)
EMBED_DIMS_DEFAULT = _int_env("CORPUS_OLLAMA_EMBED_DIMS", "embedding.ollama_dims", 768)
EMBED_DIMS_ENHANCED = _int_env("CORPUS_OPENAI_EMBED_DIMS", "embedding.openai_dims", 2560)

CHUNK_SIZE = _int_env("CORPUS_CHUNK_SIZE", "chunking.default_size", 1500)
CHUNK_OVERLAP = _int_env("CORPUS_CHUNK_OVERLAP", "chunking.default_overlap", 200)
CHUNK_SIZE_ENHANCED = _int_env("CORPUS_LARGE_CHUNK_SIZE", "chunking.openai_size", 6000)
CHUNK_OVERLAP_ENHANCED = _int_env("CORPUS_LARGE_CHUNK_OVERLAP", "chunking.openai_overlap", 600)

SEARCH_VECTOR_WEIGHT = _float_env("CORPUS_VECTOR_WEIGHT", "search.vector_weight", 0.7)
SEARCH_BM25_WEIGHT = _float_env("CORPUS_BM25_WEIGHT", "search.bm25_weight", 0.3)
SEARCH_FTS_MATCH = _str_env("CORPUS_FTS_MATCH", "search.fts_match", "any").lower()
if SEARCH_FTS_MATCH not in {"any", "all"}:
    raise ValueError("search.fts_match must be 'any' or 'all'")
SEARCH_MMR_LAMBDA = _float_env("CORPUS_MMR_LAMBDA", "search.mmr_lambda", 0.7)
SEARCH_TOP_K = _int_env("CORPUS_TOP_K", "search.top_k", 30)
SEARCH_PER_SOURCE_LIMIT = _int_env("CORPUS_PER_SOURCE_LIMIT", "search.per_source_limit", 3)
SEARCH_RECENCY_WEIGHT = _float_env("CORPUS_RECENCY_WEIGHT", "search.recency_weight", 0.15)
SEARCH_RERANK = _env_bool("CORPUS_SEARCH_RERANK", bool(_cfg("search.rerank", True)))
SEARCH_RERANK_MODEL = MODEL_SCORING
SEARCH_QUERY_EXPANSION = _env_bool(
    "CORPUS_QUERY_EXPANSION", bool(_cfg("search.query_expansion", True))
)
SEARCH_FUSION = _str_env("CORPUS_SEARCH_FUSION", "search.fusion", "linear").lower()
SEARCH_CONVERSATION_CAP = _int_env("CORPUS_CONVERSATION_CAP", "search.conversation_cap", 0)
SEARCH_DISPLAY = _str_env("CORPUS_SEARCH_DISPLAY", "search.display", "first").lower()
if SEARCH_FUSION not in {"linear", "rrf"}:
    raise ValueError("search.fusion must be 'linear' or 'rrf'")
if SEARCH_CONVERSATION_CAP < 0:
    raise ValueError("search.conversation_cap must be non-negative")
if SEARCH_DISPLAY not in {"first", "passage"}:
    raise ValueError("search.display must be 'first' or 'passage'")
SEARCH_SMART_MIN_CHUNKS = _int_env(
    "CORPUS_SMART_MIN_CHUNKS", "search.smart_min_chunks", 500
)
SEARCH_CAPTION_PREFIX = _str_env(
    "CORPUS_CAPTION_PREFIX", "search.caption_prefix", "[Figure caption"
)
SEARCH_EXCLUDE_CAPTIONS = _env_bool(
    "CORPUS_EXCLUDE_CAPTIONS", bool(_cfg("search.exclude_captions", False))
)

CONTEXT_TOKEN_BUDGET = _int_env(
    "CORPUS_CONTEXT_TOKEN_BUDGET", "processing.context_token_budget", 100_000
)
JINA_READER_PREFIX = _str_env(
    "CORPUS_READER_PREFIX", "acquisition.reader_prefix", "https://r.jina.ai/"
)
MAX_FILE_SIZE_MB = _int_env("CORPUS_MAX_FILE_MB", "acquisition.max_file_mb", 10)
PRIMARY_CORPORA = list(
    _cfg("search.primary_corpora", ["my-docs", "papers", "codebase"])
)


def corpus_path(topic: str) -> Path:
    """Return the directory for a corpus topic."""
    return CORPUS_ROOT / sanitize_topic(topic)


def sanitize_topic(topic: str) -> str:
    """Convert a topic name to a safe directory name."""
    return topic.lower().strip().replace(" ", "-").replace("/", "-")


def _detect_embedding_endpoint(url: str) -> bool:
    """Return whether an OpenAI-compatible embedding service is ready."""
    import httpx

    base = url.rsplit("/v1", 1)[0]
    for path in ("/health", "/health/readiness"):
        try:
            if httpx.get(f"{base}{path}", timeout=2.0).status_code == 200:
                return True
        except Exception:
            continue
    return False


_HEALTHY_EMBED_URLS: list[str] | None = None


def get_embedding_urls() -> list[str]:
    """Return healthy configured OpenAI-compatible embedding endpoints."""
    global _HEALTHY_EMBED_URLS
    if _HEALTHY_EMBED_URLS is None:
        candidates = [OPENAI_EMBED_URL, EMBED_URL_2]
        _HEALTHY_EMBED_URLS = [
            url for url in candidates if url and _detect_embedding_endpoint(url)
        ]
    return list(_HEALTHY_EMBED_URLS)


def get_embedding_config() -> dict[str, Any]:
    """Resolve the active embedding provider.

    ``auto`` prefers the configured OpenAI-compatible endpoint. It falls back
    to Ollama when that endpoint is unavailable. Set the provider to ``openai``
    to fail fast instead of falling back.
    """
    use_openai = EMBED_PROVIDER == "openai" or (
        EMBED_PROVIDER == "auto" and _detect_embedding_endpoint(OPENAI_EMBED_URL)
    )
    if use_openai:
        return {
            "provider": "openai",
            "url": OPENAI_EMBED_URL,
            "api_key": EMBED_API_KEY,
            "model": MODEL_EMBED_ENHANCED,
            "dims": EMBED_DIMS_ENHANCED,
            "chunk_size": CHUNK_SIZE_ENHANCED,
            "chunk_overlap": CHUNK_OVERLAP_ENHANCED,
            "label": f"{MODEL_EMBED_ENHANCED} (OpenAI-compatible)",
        }
    if EMBED_PROVIDER not in {"auto", "ollama"}:
        raise ValueError(f"unsupported embedding provider: {EMBED_PROVIDER}")
    return {
        "provider": "ollama",
        "url": OLLAMA_EMBED_URL,
        "model": MODEL_EMBED_DEFAULT,
        "dims": EMBED_DIMS_DEFAULT,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "label": f"{MODEL_EMBED_DEFAULT} (Ollama)",
    }
