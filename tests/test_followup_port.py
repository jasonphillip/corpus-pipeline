"""Fictional fixtures for the follow-up retrieval port."""

import json

import pytest

from corpus_pipeline.chunker import chunk_profiled_file
from corpus_pipeline.commands import context_chunks, search
from corpus_pipeline import search_settings
from corpus_pipeline.retrieval_eval.run import parse_settings


def test_manifest_rerank_and_eval_override(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"search": {"rerank": True}}))
    monkeypatch.setattr(search_settings, "read_manifest", lambda _: json.loads(manifest.read_text()))
    assert search_settings.corpus_search_settings("fictional")["rerank"] is True
    manifest.write_text(json.dumps({"search": {"rerank": "true"}}))
    assert search_settings.corpus_search_settings("fictional")["rerank"] is None
    assert parse_settings(["rerank=false"]) == {"rerank": False}
    assert parse_settings(["rerank=true"]) == {"rerank": True}


@pytest.mark.parametrize("manifest,env,override,expected", [
    (True, None, None, True), (False, None, None, False),
    (True, "0", None, False), (False, "1", None, True),
    (True, None, False, False), (False, None, True, True),
])
def test_rerank_decision(tmp_path, monkeypatch, manifest, env, override, expected):
    root = tmp_path / "fictional"
    (root / "processed").mkdir(parents=True)
    (root / "processed" / "chunks.db").touch()
    (root / "manifest.json").write_text(json.dumps({"search": {
        "rerank": manifest, "expansion": False}}))
    monkeypatch.setattr(search, "corpus_path", lambda _: root)
    monkeypatch.setattr(search, "connect", lambda _: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(search, "get_stats", lambda _: {"total_chunks": 2, "unique_sources": 1})
    monkeypatch.setattr(search, "get_label", lambda: "fixture")
    monkeypatch.setattr(search, "SEARCH_SMART_MIN_CHUNKS", 2)
    monkeypatch.setattr(search, "SEARCH_QUERY_EXPANSION", False)
    monkeypatch.delenv("CORPUS_SEARCH_RERANK", raising=False)
    monkeypatch.delenv("SEARCH_RERANK", raising=False)
    if env is not None:
        monkeypatch.setenv("SEARCH_RERANK", env)
    rows = [{"id": i, "source_path": "fictional.md", "content": "Amber relay.",
             "score": 1.0, "vec_score": 1.0, "bm25_score": 1.0,
             "metadata": {}} for i in (1, 2)]
    monkeypatch.setattr(search, "_search_single_corpus", lambda *args, **kwargs: rows)
    calls = []
    import corpus_pipeline.ollama_llm as llm
    monkeypatch.setattr(llm, "rerank_chunks", lambda *args, **kwargs: calls.append(True) or rows)
    search.search(["fictional"], "amber", settings_overrides=(
        {} if override is None else {"rerank": override}))
    assert bool(calls) is expected


def test_federated_rerank_if_any_manifest_opts_in(tmp_path, monkeypatch):
    for topic, enabled in (("alpha", False), ("beta", True)):
        root = tmp_path / topic
        (root / "processed").mkdir(parents=True)
        (root / "processed" / "chunks.db").touch()
        (root / "manifest.json").write_text(json.dumps({"search": {
            "rerank": enabled, "expansion": False}}))
    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path / topic)
    monkeypatch.setattr(search, "connect", lambda _: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(search, "get_stats", lambda _: {"total_chunks": 2, "unique_sources": 1})
    monkeypatch.setattr(search, "get_label", lambda: "fixture")
    monkeypatch.setattr(search, "SEARCH_SMART_MIN_CHUNKS", 2)
    monkeypatch.delenv("CORPUS_SEARCH_RERANK", raising=False)
    monkeypatch.delenv("SEARCH_RERANK", raising=False)
    monkeypatch.setattr(search, "_search_single_corpus", lambda topic, *args, **kwargs: [{
        "id": 1, "corpus": topic, "source_path": "fictional.md", "content": "Amber relay.",
        "score": 1.0, "vec_score": 1.0, "bm25_score": 1.0, "metadata": {}}])
    calls = []
    import corpus_pipeline.ollama_llm as llm
    monkeypatch.setattr(llm, "rerank_chunks", lambda query, rows, **kwargs:
                        calls.append(len(rows)) or rows)
    search.search(["alpha", "beta"], "amber")
    assert calls == [2]


def test_prompt_response_pairs(tmp_path):
    path = tmp_path / "chat.md"
    path.write_text("## Prompt\nWhere is the amber relay?\n\n## Response\nIn the archive.\n\n"
                    "## Prompt\nExplain the cobalt relay.\n\n## Response\n" + "Cobalt detail. " * 80)
    rows = chunk_profiled_file(path, "transcript", {"title": "Fictional chat"}, 240, 20)
    first = [r for r in rows if r["metadata"].get("pair_index") == 1]
    second = [r for r in rows if r["metadata"].get("pair_index") == 2]
    assert len(first) == 1 and "## Response" in first[0]["content"]
    assert len(second) > 1
    assert all("Prompt: Explain the cobalt relay." in r["content"] for r in second)
    assert all(len(r["content"]) <= 240 for r in rows)


def test_document_headings_and_fences(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Fictional report\n## Alpha\n" + "Amber detail. " * 40 +
                    "\n```md\n## Not a section\n```\n## Beta\n" + "Birch detail. " * 40)
    rows = chunk_profiled_file(path, "document", {"title": "Report"}, 300, 20)
    paths = [row["metadata"].get("heading_path") for row in rows]
    assert ["Fictional report", "Alpha"] in paths
    assert ["Fictional report", "Beta"] in paths
    assert all("Not a section" not in path for path in paths)
    assert any("Section: Fictional report > Beta" in row["content"] for row in rows)
    assert all(len(row["content"]) <= 300 for row in rows)


def test_context_entities_and_cap():
    assert context_chunks.CONTEXT_PROMPT_VERSION == "context-v2-entities"
    assert context_chunks._clean_output("Amber relay launched.") == "Amber relay launched.\nEntities: none"
    assert context_chunks._clean_output("A" * 600 + "\nEntities: Amber, 2026").endswith(
        "\nEntities: Amber, 2026")
    assert len(context_chunks._clean_output("A" * 600 + "\nEntities: Amber, 2026")) <= 500
