from datetime import date
import struct

import httpx
import pytest

from corpus_pipeline.commands import process
from corpus_pipeline import cli, corpus_db, embedder, manifest
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
    assert before[0] == (3, 3, 3, 2)

    a.write_text("new")
    b.write_text("second")
    failing = True
    process.process_topic("topic")
    failed = state()
    assert failed[0] == before[0]
    assert failed[1] == before[1]
    assert failed[2]["a.md"] == before[2]["a.md"]
    assert failed[2]["b.md"] != before[2]["b.md"]
    assert calls == [old, [old[0]], new, [old[0]]]
    assert "a.md: 1 embedding(s) dropped" in capsys.readouterr().err

    failing = False
    previous_calls = len(calls)
    process.process_topic("topic")
    recovered = state()
    assert calls[previous_calls:] == [new]
    assert recovered[0] == (3, 3, 3, 2)
    assert recovered[1] == [("a.md", new[0]), ("a.md", new[1]), ("b.md", old[0])]
    assert recovered[2]["a.md"] == _file_hash(a)


def _setup_topic(tmp_path, monkeypatch, names=("a.md", "b.md")):
    pytest.importorskip("sqlite_vec")
    topic_dir = tmp_path / "topic"
    raw_dir = topic_dir / "raw"
    raw_dir.mkdir(parents=True)
    files = {name: raw_dir / name for name in names}
    for file in files.values():
        file.write_text("shared")
    for module in (process, corpus_db, manifest):
        monkeypatch.setattr(module, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(process, "get_embedding_config", lambda: {
        "chunk_size": 1000, "model": "fake", "provider": "fake"})
    monkeypatch.setattr(process, "get_dims", lambda: 2)
    monkeypatch.setattr(process, "get_label", lambda: "fake")
    monkeypatch.setattr(process, "clean_chunk", lambda text: text)
    monkeypatch.setattr(process, "chunk_file", lambda path, chunk_size: [
        {"content": path.read_text(), "index": 0, "metadata": {}}])
    monkeypatch.setattr(process, "_embed_with_fallback", lambda texts: (
        [struct.pack("<2f", 1, 0)] * len(texts), 0))
    return files


def _index_state():
    conn = corpus_db.connect("topic")
    try:
        rows = [tuple(row) for row in conn.execute(
            "SELECT source_path, content FROM chunks ORDER BY source_path")]
        sources = {row["source_path"]: (row["source_hash"], row["chunk_count"])
                   for row in conn.execute("SELECT * FROM sources")}
        counts = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in ("chunks", "chunks_fts", "chunks_vec"))
        return rows, sources, counts
    finally:
        conn.close()


@pytest.mark.parametrize("first", ["a.md", "b.md"])
def test_shared_content_has_independent_owners(tmp_path, monkeypatch, first):
    files = _setup_topic(tmp_path, monkeypatch)
    # Rename the owner chosen for replacement without changing sort order.
    files[first].write_text("shared")
    process.process_topic("topic")
    rows, sources, counts = _index_state()
    assert rows == [("a.md", "shared"), ("b.md", "shared")]
    assert counts == (2, 2, 2)
    conn = corpus_db.connect("topic")
    try:
        assert {r["source_path"] for r in corpus_db.search_bm25(conn, "shared")} == set(files)
    finally:
        conn.close()
    files[first].write_text("changed")
    process.process_topic("topic")
    assert _index_state()[0] == sorted([(first, "changed"),
                                         (next(name for name in files if name != first), "shared")])
    assert _index_state()[2] == (2, 2, 2)
    conn = corpus_db.connect("topic")
    try:
        remaining = next(name for name in files if name != first)
        assert [r["source_path"] for r in corpus_db.search_bm25(conn, "shared")] == [remaining]
        assert {r["source_path"] for r in corpus_db.search_vector(
            conn, struct.pack("<2f", 1, 0), 2)} == set(files)
    finally:
        conn.close()
    process.process_topic("topic")
    assert _index_state()[1] == {name: (_file_hash(path), 1) for name, path in files.items()}


@pytest.mark.parametrize("zero_stage", ["extract", "clean"])
@pytest.mark.parametrize("other_changes", [False, True])
@pytest.mark.parametrize("empty_first", [False, True])
def test_zero_chunks_commit_independently(tmp_path, monkeypatch, zero_stage,
                                          other_changes, empty_first):
    names = ("a.md", "b.md") if empty_first else ("b.md", "a.md")
    files = _setup_topic(tmp_path, monkeypatch, names)
    process.process_topic("topic")
    empty = files[names[0]]
    other = files[names[1]]
    empty.write_text("empty")
    if other_changes:
        other.write_text("updated")
    if zero_stage == "extract":
        monkeypatch.setattr(process, "chunk_file", lambda path, chunk_size: [] if path == empty else [
            {"content": path.read_text(), "index": 0, "metadata": {}}])
    else:
        monkeypatch.setattr(process, "clean_chunk", lambda text: None if text == "empty" else text)
    process.process_topic("topic")
    rows, sources, counts = _index_state()
    assert rows == [(other.name, other.read_text())]
    assert counts == (1, 1, 1)
    assert sources[empty.name] == (_file_hash(empty), 0)
    process.process_topic("topic")
    assert _index_state() == (rows, sources, counts)


def test_failed_document_cannot_commit_through_next_document(tmp_path, monkeypatch):
    files = _setup_topic(tmp_path, monkeypatch)
    process.process_topic("topic")
    before = _index_state()
    files["a.md"].write_text("fail")
    files["b.md"].write_text("success")
    def embed(texts):
        if texts == ["fail"]:
            raise RuntimeError("embedding failed")
        return [struct.pack("<2f", 1, 0)], 0
    monkeypatch.setattr(process, "_embed_with_fallback", embed)
    process.process_topic("topic")
    rows, sources, counts = _index_state()
    assert rows == [("a.md", "shared"), ("b.md", "success")]
    assert sources["a.md"] == before[1]["a.md"]
    assert sources["b.md"] == (_file_hash(files["b.md"]), 1)
    assert counts == (2, 2, 2)


def test_repeated_chunks_deduplicate_within_one_source(tmp_path, monkeypatch):
    _setup_topic(tmp_path, monkeypatch, names=("a.md",))
    monkeypatch.setattr(process, "chunk_file", lambda path, chunk_size: [
        {"content": "same", "index": i, "metadata": {}} for i in range(2)])
    process.process_topic("topic")
    assert _index_state()[0] == [("a.md", "same")]
    assert _index_state()[2] == (1, 1, 1)


def test_check_parity_passes_for_consistent_database(tmp_path, monkeypatch, capsys):
    _setup_topic(tmp_path, monkeypatch, names=("a.md",))
    process.process_topic("topic")
    capsys.readouterr()
    assert cli.main(["process", "topic", "--check-parity"]) == 0
    assert "parity chunks=1 fts=1 vec=1" in capsys.readouterr().out


def test_check_parity_fails_for_orphan_fts_row(tmp_path, monkeypatch, capsys):
    _setup_topic(tmp_path, monkeypatch, names=("a.md",))
    process.process_topic("topic")
    conn = corpus_db.connect("topic")
    try:
        conn.execute("INSERT INTO chunks_fts (content, source_path, content_len, chunk_id) "
                     "VALUES ('orphan', 'orphan.md', 6, '999')")
        conn.commit()
    finally:
        conn.close()
    capsys.readouterr()
    assert cli.main(["process", "topic", "--check-parity"]) == process.PARITY_EXIT_CODE
    output = capsys.readouterr()
    assert "parity chunks=1 fts=2 vec=1" in output.out
    assert "PARITY MISMATCH chunks=1 fts=2 vec=1" in output.err
    with pytest.raises(process.ParityMismatch):
        process.process_topic("topic")


def test_vec_short_passes_when_run_reports_missing_embeddings(tmp_path, monkeypatch, capsys):
    files = _setup_topic(tmp_path, monkeypatch, names=("a.md",))
    process.process_topic("topic")
    conn = corpus_db.connect("topic")
    try:
        conn.execute("DELETE FROM chunks_vec")
        conn.commit()
    finally:
        conn.close()
    files["a.md"].write_text("changed")
    monkeypatch.setattr(process, "_embed_with_fallback", lambda texts: ([None], 1))
    capsys.readouterr()
    process.process_topic("topic")
    output = capsys.readouterr()
    assert "parity chunks=1 fts=1 vec=0" in output.out
    assert "PARITY MISMATCH" not in output.err


def test_deleting_raw_file_does_not_sync_index(tmp_path, monkeypatch):
    files = _setup_topic(tmp_path, monkeypatch)
    process.process_topic("topic")
    files["a.md"].unlink()
    process.process_topic("topic")
    assert _index_state()[0] == [("a.md", "shared"), ("b.md", "shared")]
