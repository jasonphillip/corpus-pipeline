from datetime import date
import struct

import httpx
import pytest

from corpus_pipeline.commands import process
from corpus_pipeline import corpus_db, embedder, manifest
from corpus_pipeline.commands.process import _coerce_yaml, _file_hash, parse_frontmatter


def test_frontmatter_is_loaded_and_dates_are_serializable(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("---\nts: 2024-01-02\ntags: [one, two]\n---\nBody")
    metadata = parse_frontmatter(path)
    assert metadata["ts"] == "2024-01-02"
    assert metadata["tags"] == ["one", "two"]


def test_yaml_coercion_is_recursive():
    assert _coerce_yaml({"when": date(2024, 2, 3)}) == {"when": "2024-02-03"}


def test_file_hash_changes_with_content(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("first")
    before = _file_hash(path)
    path.write_text("second")
    assert _file_hash(path) != before


def test_dropped_embedding_rolls_back_document_and_retries(tmp_path, monkeypatch, capsys):
    pytest.importorskip("sqlite_vec")
    topic_dir = tmp_path / "topic"
    raw_dir = topic_dir / "raw"
    raw_dir.mkdir(parents=True)
    a = raw_dir / "a.md"
    b = raw_dir / "b.md"
    a.write_text("old")
    b.write_text("first")
    old = ["old alpha " * 20, "old beta " * 20]
    new = ["new alpha " * 20, "new beta " * 20]
    vector = struct.pack("<2f", 1.0, 0.0)
    failing = False
    calls = []

    for module in (process, corpus_db, manifest):
        monkeypatch.setattr(module, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(process, "get_embedding_config", lambda: {
        "chunk_size": 1000, "model": "fake", "provider": "fake"})
    monkeypatch.setattr(process, "get_dims", lambda: 2)
    monkeypatch.setattr(process, "get_label", lambda: "fake")
    monkeypatch.setattr(process, "clean_chunk", lambda text: text)

    def chunks(path, chunk_size):
        contents = (old if path == a and path.read_text() == "old" else
                    new if path == a else [old[0]])
        return [{"content": text, "index": i, "metadata": {}}
                for i, text in enumerate(contents)]

    def error():
        request = httpx.Request("POST", "http://fake.invalid/embed")
        response = httpx.Response(400, request=request)
        return httpx.HTTPStatusError("second batch failed", request=request,
                                     response=response)

    def parallel(texts):
        calls.append(list(texts))
        for batch_index, _ in enumerate([[text] for text in texts]):
            if failing and batch_index == 1:
                raise error()
        return [vector] * len(texts)

    def serial(texts):
        if failing and texts[0] == new[1]:
            raise error()
        return [vector]

    monkeypatch.setattr(process, "chunk_file", chunks)
    monkeypatch.setattr(embedder, "embed_texts_parallel", parallel)
    monkeypatch.setattr(embedder, "embed_texts", serial)

    def state():
        conn = corpus_db.connect("topic")
        try:
            counts = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                           for table in ("chunks", "chunks_fts", "chunks_vec", "sources"))
            rows = [tuple(row) for row in conn.execute(
                "SELECT source_path, content FROM chunks ORDER BY source_path, chunk_index")]
            hashes = dict(conn.execute("SELECT source_path, source_hash FROM sources"))
            return counts, rows, hashes
        finally:
            conn.close()

    process.process_topic("topic")
    before = state()
    assert before[0] == (2, 2, 2, 2)

    a.write_text("new")
    b.write_text("second")
    failing = True
    process.process_topic("topic")
    failed = state()
    assert failed[0] == before[0]
    assert failed[1] == before[1]
    assert failed[2]["a.md"] == before[2]["a.md"]
    assert failed[2]["b.md"] != before[2]["b.md"]
    assert calls == [old, new]
    assert "a.md: 1 embedding(s) dropped" in capsys.readouterr().err

    failing = False
    previous_calls = len(calls)
    process.process_topic("topic")
    recovered = state()
    assert calls[previous_calls:] == [new]
    assert recovered[0] == (2, 2, 2, 2)
    assert recovered[1] == [("a.md", new[0]), ("a.md", new[1])]
    assert recovered[2]["a.md"] == _file_hash(a)
