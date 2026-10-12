import struct

import pytest

from corpus_pipeline import cli, corpus_db
from corpus_pipeline.commands import health, reembed


def test_rebuild_legacy_vectors_uses_cosine_and_preserves_sources(tmp_path, monkeypatch, capsys):
    pytest.importorskip("sqlite_vec")
    topic_dir = tmp_path / "topic"
    monkeypatch.setattr(corpus_db, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(reembed, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(reembed, "get_dims", lambda: 2)
    conn = corpus_db.connect("topic")
    try:
        corpus_db.init_schema(conn, 2)
        conn.execute("DROP TABLE chunks_vec")
        conn.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0("
                     "id INTEGER PRIMARY KEY, embedding float[2])")
        for source, content, vector in (
            ("aligned.md", "aligned", (10.0, 0.0)),
            ("nearby.md", "nearby", (0.9, 0.1)),
        ):
            corpus_db.insert_chunk(conn, source, 0, content, struct.pack("<2f", *vector))
            corpus_db.upsert_source(conn, source, source, 1)
        conn.commit()
        assert health._vector_distance_metric(conn) == "l2"
        assert corpus_db.search_vector(conn, struct.pack("<2f", 1, 0), 2)[0]["source_path"] == "nearby.md"
    finally:
        conn.close()

    vectors = {"aligned": (10.0, 0.0), "nearby": (0.9, 0.1)}
    monkeypatch.setattr(reembed, "embed_texts_parallel", lambda texts: [
        struct.pack("<2f", *vectors[text]) for text in texts])
    assert cli.main(["reembed", "topic", "--batch-size", "1", "--rebuild-vectors"]) == 0
    conn = corpus_db.connect("topic")
    try:
        assert health._vector_distance_metric(conn) == "cosine"
        assert conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0] == 2
        assert dict(conn.execute("SELECT source_path, chunk_count FROM sources")) == {
            "aligned.md": 1, "nearby.md": 1}
        assert [r["source_path"] for r in corpus_db.search_vector(
            conn, struct.pack("<2f", 1, 0), 2)] == ["aligned.md", "nearby.md"]
    finally:
        conn.close()
    assert list((topic_dir / "processed").glob("chunks.db.backup-*.db"))
    assert "distance_metric=cosine" in capsys.readouterr().out


def test_failed_rebuild_restores_legacy_table(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    topic_dir = tmp_path / "topic"
    monkeypatch.setattr(corpus_db, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(reembed, "corpus_path", lambda topic: topic_dir)
    monkeypatch.setattr(reembed, "get_dims", lambda: 2)
    conn = corpus_db.connect("topic")
    try:
        corpus_db.init_schema(conn, 2)
        conn.execute("DROP TABLE chunks_vec")
        conn.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0("
                     "id INTEGER PRIMARY KEY, embedding float[2])")
        corpus_db.insert_chunk(conn, "a.md", 0, "old", struct.pack("<2f", 1, 0))
        corpus_db.upsert_source(conn, "a.md", "hash", 1)
        conn.commit()
    finally:
        conn.close()
    def fail(texts):
        raise RuntimeError("embedding failed")
    monkeypatch.setattr(reembed, "embed_texts_parallel", fail)
    with pytest.raises(RuntimeError, match="embedding failed"):
        reembed.reembed_changed("topic", rebuild_vectors=True)
    conn = corpus_db.connect("topic")
    try:
        assert health._vector_distance_metric(conn) == "l2"
        assert conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    finally:
        conn.close()
    assert list((topic_dir / "processed").glob("chunks.db.backup-*.db"))
