"""A saturated embedding service has a bounded query wait and exit code."""

import os
import subprocess
import sys
from unittest.mock import Mock

import httpx
import pytest

from corpus_pipeline import embedder
from corpus_pipeline.commands import search


def test_query_timeout_config_and_environment_override(tmp_path):
    config = tmp_path / "corpus.yml"
    config.write_text("services:\n  embedding:\n    query_timeout: 45\n")
    env = {**os.environ, "CORPUS_CONFIG": str(config)}
    env.pop("CORPUS_QUERY_EMBED_TIMEOUT", None)
    command = [sys.executable, "-c", "from corpus_pipeline.config import QUERY_EMBED_TIMEOUT; print(QUERY_EMBED_TIMEOUT)"]
    assert subprocess.check_output(command, env=env, text=True).strip() == "45.0"
    env["CORPUS_QUERY_EMBED_TIMEOUT"] = "9"
    assert subprocess.check_output(command, env=env, text=True).strip() == "9.0"


def test_query_timeout_is_120_seconds_and_cached(monkeypatch):
    embedder.reset_config_cache()
    monkeypatch.setattr(embedder, "_get_config", lambda: {
        "provider": "openai", "model": "fixture", "url": "http://example.invalid/embed"})
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["timeout"])
        response = Mock()
        response.json.return_value = {"data": [{"embedding": [0.0, 1.0]}]}
        return response

    monkeypatch.setattr(embedder.httpx, "post", post)
    first = embedder.embed_query("same query")
    assert embedder.embed_query("same query") == first
    assert calls == [120.0]
    embedder.reset_config_cache()


def test_timeout_exits_five_with_busy_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "chunks.db").touch()
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path)
    monkeypatch.setattr(search, "connect", lambda topic: Mock())
    monkeypatch.setattr(search, "get_stats", lambda conn: {
        "total_chunks": 1, "unique_sources": 1})
    monkeypatch.setattr(search, "SEARCH_RERANK", False)
    monkeypatch.setattr(search, "SEARCH_QUERY_EXPANSION", False)
    monkeypatch.setattr(search, "get_label", lambda: "fixture")
    monkeypatch.setattr(search, "embed_query", lambda query: (_ for _ in ()).throw(
        httpx.ReadTimeout("timed out")))
    with pytest.raises(SystemExit) as result:
        search.search(["sample"], "query")
    assert result.value.code == 5
    assert "Embedding service busy" in capsys.readouterr().err
