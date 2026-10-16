"""Per-corpus search options and filtered context behavior."""

import json
import struct

import pytest

from corpus_pipeline import corpus_db, ollama_llm, search_settings
from corpus_pipeline.commands import search
from corpus_pipeline.search_passage import passage_text


def test_manifest_settings_and_environment_precedence(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"search": {
        "fusion": "rrf", "expansion": False, "conversation_cap": 2,
        "display": "passage"}}))
    monkeypatch.setattr(search_settings, "read_manifest", lambda topic: json.loads(
        (tmp_path / "manifest.json").read_text()))
    settings = search_settings.corpus_search_settings("sample")
    assert settings == {"fusion": "rrf", "expansion": False,
                        "conversation_cap": 2, "display": "passage"}
    monkeypatch.setenv("CORPUS_SEARCH_FUSION", "linear")
    assert search_settings.corpus_search_settings("sample")["fusion"] == search_settings.SEARCH_FUSION


def test_rrf_conversation_cap_and_pin(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.setattr(corpus_db, "corpus_path", lambda topic: tmp_path)
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path)
    conn = corpus_db.connect("sample")
    try:
        corpus_db.init_schema(conn, 2)
        for i in range(6):
            conversation = "same" if i < 5 else "other"
            corpus_db.insert_chunk(conn, f"item_{i}.md", 0, f"orbit signal item {i}",
                                   struct.pack("<2f", 1 - i / 10, i / 10),
                                   {"conversation_id": conversation})
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(search, "embed_query", lambda query: struct.pack("<2f", 1, 0))
    settings = {"fusion": "rrf", "expansion": False,
                "conversation_cap": 1, "display": "first"}
    rows = search._search_single_corpus("sample", ["orbit signal"], 3, settings=settings)
    assert len([r for r in rows if r["metadata"]["conversation_id"] == "same"]) == 1
    pinned = search._search_single_corpus("sample", ["orbit signal"], 3,
                                          metadata_filters=[("conversation_id", "same")],
                                          settings=settings)
    assert len(pinned) >= 3


def test_passage_uses_turn_start_and_context_requires_whole_terms(tmp_path, monkeypatch):
    row = {"content": "signal " * 130 + "lunar beacon reply " * 30,
           "metadata": {"turn_start": {"turn": 4, "role": "ASSISTANT"}}}
    shown = passage_text(row, "lunar beacon")
    assert shown.startswith("[turn 4 · ASSISTANT]")
    assert "lunar beacon" in shown
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "CONTEXT.md").write_text(
        "## Dashboard\nThe orbital dashboard is open.\n\n"
        "## Beacon notes\nThe lunar beacon is visible.\n")
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path)
    assert [header for header, _ in search._find_context_sections("sample", "lunar beacon")] == ["Beacon notes"]
    assert search._find_context_sections("sample", "dash lunar") == []


def test_filtered_search_skips_context_injection(tmp_path, monkeypatch, capsys):
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "chunks.db").touch()
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path)
    monkeypatch.setattr(search, "connect", lambda topic: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(search, "get_stats", lambda conn: {"total_chunks": 1, "unique_sources": 1})
    monkeypatch.setattr(search, "_search_single_corpus", lambda *args, **kwargs: [{
        "id": 1, "score": 1.0, "vec_score": 1.0, "bm25_score": 0.0,
        "source_path": "example.md", "content": "A filtered result", "metadata": {}}])
    monkeypatch.setattr(search, "_find_context_sections", lambda *args: (_ for _ in ()).throw(
        AssertionError("context should be skipped")))
    monkeypatch.setattr(search, "SEARCH_RERANK", False)
    monkeypatch.setattr(search, "SEARCH_QUERY_EXPANSION", False)
    search.search(["sample"], "filtered result", metadata_filters=[("group", "red")])
    assert "A filtered result" in capsys.readouterr().out


def test_explicit_expansion_only_reaches_enabled_corpus(tmp_path, monkeypatch):
    for name, enabled in (("on", True), ("off", False)):
        root = tmp_path / name
        (root / "processed").mkdir(parents=True)
        (root / "processed" / "chunks.db").touch()
        (root / "manifest.json").write_text(json.dumps({"search": {"expansion": enabled}}))
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path / topic)
    monkeypatch.setattr(search, "connect", lambda topic: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(search, "get_stats", lambda conn: {"total_chunks": 1, "unique_sources": 1})
    monkeypatch.setattr(search, "get_label", lambda: "fixture")
    monkeypatch.setattr(search, "SEARCH_RERANK", False)
    monkeypatch.setattr(ollama_llm, "expand_query", lambda query, n: ["other terms"])
    calls = []
    monkeypatch.setattr(search, "_search_single_corpus", lambda topic, queries, *args, **kwargs:
                        calls.append((topic, queries)) or [])
    search.search(["on", "off"], "original terms", top_k=2)
    assert calls == [("on", ["original terms", "other terms"]),
                     ("off", ["original terms"])]
