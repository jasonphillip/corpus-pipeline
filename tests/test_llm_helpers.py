import pytest

from corpus_pipeline import ollama_llm


def test_balanced_json_parser_ignores_noise_and_fences():
    assert ollama_llm._parse_json("note\n```json\n{\"ok\": true}\n```") == {"ok": True}


def test_json_parser_salvages_complete_unterminated_array_items():
    assert ollama_llm._parse_json('prefix ["a", "b"') == ["a", "b"]


def test_rerank_failure_does_not_fabricate_scores(monkeypatch):
    chunks = [{"id": 1, "content": "a"}, {"id": 2, "content": "b"}]
    monkeypatch.setattr(
        ollama_llm, "_rerank_cross_encoder", lambda query, rows: (None, "offline")
    )
    monkeypatch.setattr(
        ollama_llm, "generate_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    with pytest.raises(ollama_llm.RerankUnavailable):
        ollama_llm.rerank_chunks("query", chunks)
    assert all("rerank_score" not in chunk for chunk in chunks)

