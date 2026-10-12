"""Opt-in integration checks against local embedding and Ollama services."""

import os
import shutil
import importlib
from pathlib import Path

import pytest

from corpus_pipeline import chunker, config, corpus_db, embedder, manifest, ollama_llm
from corpus_pipeline.commands import process, search


DOCS = Path(__file__).resolve().parents[1] / "examples" / "docs"
QUERY = "How does hybrid retrieval combine BM25 and cosine distance?"
EXPECTED_SOURCE = "example/2024-06-retrieval.md"


@pytest.fixture
def live_corpus(tmp_path, monkeypatch, request):
    base_url = os.environ.get("CORPUS_TEST_EMBED_URL", "").strip().rstrip("/")
    if not base_url:
        pytest.skip("CORPUS_TEST_EMBED_URL is not set")
    if request.node.name == "test_end_to_end_smart_search_services" and not os.environ.get(
        "CORPUS_TEST_OLLAMA_URL", ""
    ).strip():
        pytest.skip("CORPUS_TEST_OLLAMA_URL is not set")

    topic_dir = tmp_path / "example"
    raw_dir = topic_dir / "raw" / "example"
    raw_dir.mkdir(parents=True)
    for source in DOCS.glob("*.md"):
        shutil.copy2(source, raw_dir / source.name)

    for module in (process, search, corpus_db, manifest):
        monkeypatch.setattr(module, "corpus_path", lambda topic: topic_dir)
    cfg = {
        "provider": "openai",
        "url": f"{base_url}/embeddings",
        "api_key": "",
        "model": os.environ.get("CORPUS_TEST_EMBED_MODEL", "Qwen3-Embedding-4B"),
        "dims": 2560,
        "chunk_size": 6000,
        "chunk_overlap": 600,
        "label": "test OpenAI-compatible embedder",
    }
    monkeypatch.setattr(process, "get_embedding_config", lambda: cfg)
    monkeypatch.setattr(chunker, "get_embedding_config", lambda: cfg)
    monkeypatch.setattr(embedder, "get_embedding_config", lambda: cfg)
    monkeypatch.setattr(config, "get_embedding_urls", lambda: [cfg["url"]])
    embedder.reset_config_cache()
    monkeypatch.setattr(search, "SEARCH_RERANK", False)
    monkeypatch.setattr(search, "SEARCH_QUERY_EXPANSION", False)
    process.process_topic("example")
    yield
    embedder.reset_config_cache()


def test_end_to_end_processing_service(live_corpus, capsys):
    search.search(["example"], QUERY, top_k=3)
    output = capsys.readouterr().out
    assert "### Result 1" in output
    assert "**Source:** " + EXPECTED_SOURCE in output


def test_end_to_end_smart_search_services(live_corpus, monkeypatch, capsys):
    ollama_url = os.environ.get("CORPUS_TEST_OLLAMA_URL", "").strip().rstrip("/")
    model = os.environ.get("CORPUS_TEST_OLLAMA_MODEL", "qwen3:14b")
    with monkeypatch.context() as overrides:
        overrides.setenv("CORPUS_TRIAGE_MODEL", model)
        overrides.setenv("CORPUS_SCORING_MODEL", model)
        importlib.reload(config)
        importlib.reload(ollama_llm)
        assert config.MODEL_TRIAGE == config.MODEL_SCORING == model
        overrides.setattr(ollama_llm, "OLLAMA_GENERATE_URL", f"{ollama_url}/api/generate")
        overrides.setattr(search, "SEARCH_RERANK_MODEL", config.MODEL_SCORING)
        overrides.setattr(search, "SEARCH_SMART_MIN_CHUNKS", 0)
        overrides.setattr(search, "SEARCH_QUERY_EXPANSION", True)
        overrides.setattr(search, "SEARCH_RERANK", True)
        search.search(["example"], QUERY, top_k=3)
    importlib.reload(config)
    importlib.reload(ollama_llm)
    output = capsys.readouterr().out
    assert "Query expansion: +" in output or "Query expansion skipped: " in output
    assert "Rerank: " in output
    assert "### Result 1" in output
    assert "Returned " in output
